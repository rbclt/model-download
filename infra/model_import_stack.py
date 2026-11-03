"""The one AWS CDK stack of the model import application (u1-import-slice).

Every resource of ``infrastructure-specification.md`` and ``monitoring-design.md`` lives here, so
u2-import-rules adds code to the same stack and no resource has a second owner.

Design points kept in this file:

* The task role has the closed list of ``security-design.md`` row NFR3.4: no ``s3:*``, no
  ``Resource: *``, no log and no metric permission (container output reaches CloudWatch Logs
  through the ``awslogs`` driver under the separate execution role).
* Both buckets deny object data actions to every principal except the task role, the CDK roles,
  the smoke-check role (when given), and the break-glass roles (NFR3.7). The stack refuses to
  build when no break-glass role is given.
* Queue, scaling and alarms follow ``scalability-design.md``, ``reliability-design.md`` and
  ``monitoring-design.md``. Scaling alarms are built by hand (CloudWatch alarm plus a step
  scaling action), because the scale-in alarm needs metric math and missing data as breaching.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import jsii
from aws_cdk import (
    ArnFormat,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
)
from aws_cdk import aws_applicationautoscaling as appscaling
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_ses as ses
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from model_import import settings as worker_settings
from model_import.deadline import REASON_TIME_LIMIT

APP_TAG = "model-import-scan"
BUS_NAME = "model-import"
RULE_NAME = "start-import"
EVENT_SOURCE = "model.import"
EVENT_DETAIL_TYPE = "ModelImportRequested"
MESSAGE_GROUP_ID = "imports"
IMPORT_QUEUE_NAME = "import-queue.fifo"
IMPORT_DLQ_NAME = "import-dlq.fifo"
EVENT_DLQ_NAME = "event-dlq"
SERVICE_NAME = "import-worker"
LOG_GROUP_NAME = "/model-import/worker"
CONFIGURATION_SET_NAME = "model-import"
TOPIC_NAME = "model-import-alerts"
METRIC_NAMESPACE = "ModelImport"
DEFAULT_BUDGET_LIMIT_USD = 100.0
DEFAULT_QUALIFIER = "hnb659fds"  # the standard CDK bootstrap qualifier

_ROLE_ARN = re.compile(r"^arn:aws:iam::\d{12}:role/\S+$")

# NFR4.19 (unexpected stop): files of an unfinished model version do not expire, so the team is
# told where to look first. Used by the descriptions of alarms d-queue and d-reminder.
_DEAD_LETTER_ADVICE = (
    "Open state/<importId>/record.json first. If its step is keeping, look for files without "
    "a _COMPLETE marker under models/ for that model version, and remove them by hand or run "
    "the import again."
)


class ModelImportStackError(ValueError):
    """The stack cannot be built from the settings it was given."""


def check_role_arns(arns: Sequence[str]) -> list[str]:
    """Return the cleaned break-glass role ARNs; raise when none is given or one is not a role.

    NFR3.7: someone must always be able to inspect the buckets, so an empty list stops the
    deployment. The ARN may use ``*`` (for example for a single sign-on role).
    """
    cleaned = [arn.strip() for arn in arns if arn and arn.strip()]
    if not cleaned:
        raise ModelImportStackError(
            "break_glass_role_arns must name at least one administrator role (NFR3.7)"
        )
    for arn in cleaned:
        if not _ROLE_ARN.fullmatch(arn):
            raise ModelImportStackError(
                "break_glass_role_arns holds a value that is not a role ARN"
            )
    return cleaned


@jsii.implements(events.IRuleTarget)
class _TopicTarget:
    """An EventBridge target that sends the event to the alert topic.

    The CDK ``SnsTopic`` target adds a topic policy statement for ``events.amazonaws.com`` with no
    condition, which would let any rule of any account publish. The stack's own topic policy
    already allows rules of this account, so this target adds nothing to the policy.
    """

    def __init__(self, topic: sns.ITopic) -> None:
        self._topic = topic

    def bind(self, _rule: events.IRule, _id: str | None = None) -> events.RuleTargetConfig:
        return events.RuleTargetConfig(arn=self._topic.topic_arn)


class ModelImportStack(Stack):
    """All resources of the model import application."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        team_address: str,
        sender_address: str,
        break_glass_role_arns: Sequence[str],
        image: ecs.ContainerImage | None = None,
        budget_limit_usd: float = DEFAULT_BUDGET_LIMIT_USD,
        smoke_check_role_arn: str | None = None,
        ses_identity: str | None = None,
        environment_name: str = "staging",
        bootstrap_qualifier: str = DEFAULT_QUALIFIER,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if not worker_settings.is_valid_address(team_address):
            raise ModelImportStackError("team_address is not a valid email address")
        if not worker_settings.is_valid_address(sender_address):
            raise ModelImportStackError("sender_address is not a valid email address")
        if budget_limit_usd <= 0:
            raise ModelImportStackError("budget_limit_usd must be above 0")
        allowed_readers = check_role_arns(break_glass_role_arns)
        if smoke_check_role_arn:
            allowed_readers += check_role_arns([smoke_check_role_arn])

        self._team_address = team_address
        self._sender_address = sender_address
        self._ses_identity = ses_identity or sender_address
        self._bootstrap_qualifier = bootstrap_qualifier
        self._allowed_readers = allowed_readers

        Tags.of(self).add("app", APP_TAG)
        Tags.of(self).add("env", environment_name)

        self.alert_topic = self._build_alert_topic(team_address)
        self.configuration_set = self._build_configuration_set()
        self.holding_bucket = self._build_bucket("HoldingBucket", holding=True)
        self.final_bucket = self._build_bucket("FinalBucket", holding=False)

        self.event_dlq, self.import_dlq, self.import_queue = self._build_queues()
        self.event_bus, self.start_rule = self._build_event_bus()

        self.vpc, self.security_group = self._build_network()
        self.cluster = ecs.Cluster(
            self,
            "Cluster",
            vpc=self.vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )
        self.log_group = logs.LogGroup(
            self,
            "WorkerLogGroup",
            log_group_name=LOG_GROUP_NAME,
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.task_role = self._build_task_role()
        self.execution_role = iam.Role(
            self,
            "ExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Pulls the worker image and writes its container logs",
        )
        self._restrict_bucket_data_access(self.holding_bucket)
        self._restrict_bucket_data_access(self.final_bucket)

        self.task_definition = self._build_task_definition(image)
        self.service = self._build_service()
        self._build_scaling()
        self._build_monitoring()
        self._build_budget(budget_limit_usd)
        self._build_outputs()

    # -- alerts and email --------------------------------------------------------------------

    def _build_alert_topic(self, team_address: str) -> sns.Topic:
        topic = sns.Topic(
            self,
            "AlertTopic",
            topic_name=TOPIC_NAME,
            display_name="Model import alerts",
            enforce_ssl=True,
        )
        topic.add_subscription(subscriptions.EmailSubscription(team_address))
        publish = ["sns:Publish"]
        account_only = {"StringEquals": {"aws:SourceAccount": self.account}}
        # SES gets its statement from the configuration set's event destination below (CDK adds
        # it, limited to this account and this configuration set).
        for sid, service in (
            ("AllowCloudWatchAlarms", "cloudwatch.amazonaws.com"),
            ("AllowBudgets", "budgets.amazonaws.com"),
        ):
            topic.add_to_resource_policy(
                iam.PolicyStatement(
                    sid=sid,
                    principals=[iam.ServicePrincipal(service)],
                    actions=publish,
                    resources=[topic.topic_arn],
                    conditions=account_only,
                )
            )
        topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowEventBridgeRules",
                principals=[iam.ServicePrincipal("events.amazonaws.com")],
                actions=publish,
                resources=[topic.topic_arn],
                conditions={
                    "ArnLike": {
                        "aws:SourceArn": self.format_arn(
                            service="events",
                            resource="rule",
                            resource_name="*",
                            arn_format=ArnFormat.SLASH_RESOURCE_NAME,
                        )
                    }
                },
            )
        )
        return topic

    def _build_configuration_set(self) -> ses.ConfigurationSet:
        configuration_set = ses.ConfigurationSet(
            self, "ConfigurationSet", configuration_set_name=CONFIGURATION_SET_NAME
        )
        configuration_set.add_event_destination(
            "BounceAndComplaintAlerts",
            destination=ses.EventDestination.sns_topic(self.alert_topic),
            events=[ses.EmailSendingEvent.BOUNCE, ses.EmailSendingEvent.COMPLAINT],
        )
        return configuration_set

    # -- storage -----------------------------------------------------------------------------

    def _build_bucket(self, construct_id: str, *, holding: bool) -> s3.Bucket:
        """NFR3.5, NFR3.6, NFR3.15, NFR4.9: encrypted, private, TLS 1.2, kept on stack delete."""
        abort_uploads = s3.LifecycleRule(
            id="abort-incomplete-multipart-uploads",
            abort_incomplete_multipart_upload_after=Duration.days(1),
        )
        rules = [abort_uploads]
        if holding:
            rules += [
                s3.LifecycleRule(
                    id="expire-holding", prefix="holding/", expiration=Duration.days(2)
                ),
                s3.LifecycleRule(id="expire-state", prefix="state/", expiration=Duration.days(30)),
            ]
        return s3.Bucket(
            self,
            construct_id,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            minimum_tls_version=1.2,
            versioned=False,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            removal_policy=RemovalPolicy.RETAIN,
            auto_delete_objects=False,
            lifecycle_rules=rules,
        )

    def _cdk_role_arns(self) -> list[str]:
        """The CDK deploy and CloudFormation execution roles created by ``cdk bootstrap``."""
        suffix = f"{self.account}-{self.region}"
        base = f"arn:{self.partition}:iam::{self.account}:role/cdk-{self._bootstrap_qualifier}"
        return [f"{base}-deploy-role-{suffix}", f"{base}-cfn-exec-role-{suffix}"]

    def _restrict_bucket_data_access(self, bucket: s3.Bucket) -> None:
        """NFR3.7: only the listed roles read or change object data. Bucket configuration
        actions are not denied, so a stack update or delete is never blocked."""
        allowed = [self.task_role.role_arn, *self._cdk_role_arns(), *self._allowed_readers]
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyObjectDataToOthers",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                resources=[bucket.arn_for_objects("*")],
                conditions={"ArnNotLike": {"aws:PrincipalArn": allowed}},
            )
        )

    # -- queues and events -------------------------------------------------------------------

    def _build_queues(self) -> tuple[sqs.Queue, sqs.Queue, sqs.Queue]:
        event_dlq = sqs.Queue(
            self,
            "EventDeadLetterQueue",
            queue_name=EVENT_DLQ_NAME,
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )
        import_dlq = sqs.Queue(
            self,
            "ImportDeadLetterQueue",
            queue_name=IMPORT_DLQ_NAME,
            fifo=True,
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )
        import_queue = sqs.Queue(
            self,
            "ImportQueue",
            queue_name=IMPORT_QUEUE_NAME,
            fifo=True,
            content_based_deduplication=True,
            visibility_timeout=Duration.minutes(15),
            retention_period=Duration.days(4),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=1, queue=import_dlq),
        )
        return event_dlq, import_dlq, import_queue

    def _build_event_bus(self) -> tuple[events.EventBus, events.Rule]:
        """NFR3.3: a custom bus that only principals of this account can put events on."""
        bus = events.EventBus(self, "EventBus", event_bus_name=BUS_NAME)
        bus.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowPutEventsFromThisAccount",
                principals=[iam.AccountRootPrincipal()],
                actions=["events:PutEvents"],
                resources=[bus.event_bus_arn],
            )
        )
        bus.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyPutEventsFromOtherAccounts",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["events:PutEvents"],
                resources=[bus.event_bus_arn],
                conditions={"StringNotEquals": {"aws:PrincipalAccount": self.account}},
            )
        )
        rule = events.Rule(
            self,
            "StartImportRule",
            rule_name=RULE_NAME,
            event_bus=bus,
            description="Contract C1: sends a start event to the import queue",
            event_pattern=events.EventPattern(
                source=[EVENT_SOURCE], detail_type=[EVENT_DETAIL_TYPE]
            ),
        )
        rule.add_target(
            targets.SqsQueue(
                self.import_queue,
                message_group_id=MESSAGE_GROUP_ID,
                retry_attempts=185,
                max_event_age=Duration.hours(24),
                dead_letter_queue=self.event_dlq,
            )
        )
        return bus, rule

    # -- network and compute -----------------------------------------------------------------

    def _build_network(self) -> tuple[ec2.Vpc, ec2.SecurityGroup]:
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                )
            ],
            gateway_endpoints={
                "S3": ec2.GatewayVpcEndpointOptions(service=ec2.GatewayVpcEndpointAwsService.S3)
            },
        )
        security_group = ec2.SecurityGroup(
            self,
            "WorkerSecurityGroup",
            vpc=vpc,
            description="Import worker: no inbound traffic, all outbound traffic",
            allow_all_outbound=True,
        )
        return vpc, security_group

    def _build_task_role(self) -> iam.Role:
        """NFR3.4: exactly the closed list, each statement on named resources only."""
        holding, final = self.holding_bucket, self.final_bucket
        object_actions = [
            "s3:GetObject",
            "s3:PutObject",
            "s3:DeleteObject",
            "s3:AbortMultipartUpload",
            "s3:ListMultipartUploadParts",
        ]
        task_arn = self.format_arn(
            service="ecs",
            resource="task",
            resource_name=f"{self.cluster.cluster_name}/*",
            arn_format=ArnFormat.SLASH_RESOURCE_NAME,
        )
        identity_arn = self.format_arn(
            service="ses",
            resource="identity",
            resource_name=self._ses_identity,
            arn_format=ArnFormat.SLASH_RESOURCE_NAME,
        )
        configuration_set_arn = self.format_arn(
            service="ses",
            resource="configuration-set",
            resource_name=CONFIGURATION_SET_NAME,
            arn_format=ArnFormat.SLASH_RESOURCE_NAME,
        )
        statements = [
            iam.PolicyStatement(
                sid="ObjectsInOwnPrefixes",
                actions=object_actions,
                resources=[
                    holding.arn_for_objects("holding/*"),
                    holding.arn_for_objects("state/*"),
                    final.arn_for_objects("models/*"),
                ],
            ),
            iam.PolicyStatement(
                sid="ListWholeBuckets",
                actions=["s3:ListBucket", "s3:ListBucketMultipartUploads"],
                resources=[holding.bucket_arn, final.bucket_arn],
            ),
            iam.PolicyStatement(
                sid="ReadImportQueue",
                actions=["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility"],
                resources=[self.import_queue.queue_arn],
            ),
            iam.PolicyStatement(
                sid="ProtectOwnTask",
                actions=["ecs:UpdateTaskProtection"],
                resources=[task_arn],
            ),
            iam.PolicyStatement(
                sid="SendFailureEmail",
                actions=["ses:SendEmail"],
                resources=[identity_arn, configuration_set_arn],
                conditions={"StringEquals": {"ses:FromAddress": self._sender_address}},
            ),
        ]
        return iam.Role(
            self,
            "TaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="The one role of the import worker (NFR3.4)",
            inline_policies={"worker": iam.PolicyDocument(statements=statements)},
        )

    def _build_task_definition(self, image: ecs.ContainerImage | None) -> ecs.FargateTaskDefinition:
        task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDefinition",
            cpu=4096,
            memory_limit_mib=8192,
            ephemeral_storage_gib=30,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
            task_role=self.task_role,
            execution_role=self.execution_role,
        )
        # NFR3.2: HF_TOKEN is not set; the worker reads nothing else secret.
        environment = {
            worker_settings.ENV_HOLDING_BUCKET: self.holding_bucket.bucket_name,
            worker_settings.ENV_FINAL_BUCKET: self.final_bucket.bucket_name,
            worker_settings.ENV_QUEUE_URL: self.import_queue.queue_url,
            worker_settings.ENV_SENDER_ADDRESS: self._sender_address,
            worker_settings.ENV_TEAM_ADDRESS: self._team_address,
            worker_settings.ENV_CONFIGURATION_SET: CONFIGURATION_SET_NAME,
            worker_settings.ENV_REGION: self.region,
        }
        task_definition.add_container(
            "worker",
            image=image if image is not None else self._asset_image(),
            essential=True,
            environment=environment,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="container", log_group=self.log_group),
        )
        return task_definition

    @staticmethod
    def _asset_image() -> ecs.ContainerImage:
        """Build the image from the project (the Dockerfile at the root). Needs Docker at
        deploy time only; synthesis hashes the folder and does not run Docker."""
        root = Path(__file__).resolve().parent.parent
        return ecs.ContainerImage.from_asset(str(root), platform=ecr_assets.Platform.LINUX_AMD64)

    def _build_service(self) -> ecs.FargateService:
        """NFR2.3: a deployment never runs a second worker (0 percent minimum, 100 maximum)."""
        return ecs.FargateService(
            self,
            "Service",
            service_name=SERVICE_NAME,
            cluster=self.cluster,
            task_definition=self.task_definition,
            desired_count=0,
            min_healthy_percent=0,
            max_healthy_percent=100,
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
            assign_public_ip=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[self.security_group],
        )

    # -- scaling -----------------------------------------------------------------------------

    def _build_scaling(self) -> None:
        """Scale 0 to 1 and back with explicit alarms (scalability-design.md)."""
        target = appscaling.ScalableTarget(
            self,
            "ScalableTarget",
            service_namespace=appscaling.ServiceNamespace.ECS,
            scalable_dimension="ecs:service:DesiredCount",
            resource_id=f"service/{self.cluster.cluster_name}/{self.service.service_name}",
            min_capacity=0,
            max_capacity=1,
        )
        target.node.add_dependency(self.service)

        scale_out = appscaling.StepScalingAction(
            self,
            "ScaleOutAction",
            scaling_target=target,
            adjustment_type=appscaling.AdjustmentType.EXACT_CAPACITY,
            metric_aggregation_type=appscaling.MetricAggregationType.MAXIMUM,
            cooldown=Duration.minutes(1),
        )
        scale_out.add_adjustment(adjustment=1, lower_bound=0)
        scale_in = appscaling.StepScalingAction(
            self,
            "ScaleInAction",
            scaling_target=target,
            adjustment_type=appscaling.AdjustmentType.EXACT_CAPACITY,
            metric_aggregation_type=appscaling.MetricAggregationType.MAXIMUM,
        )
        scale_in.add_adjustment(adjustment=0, upper_bound=0)

        one_minute = Duration.minutes(1)
        self.scale_out_alarm = cloudwatch.Alarm(
            self,
            "ScaleOutAlarm",
            alarm_name="model-import-scale-out",
            alarm_description="One or more visible messages for one minute: start the worker",
            metric=self.import_queue.metric_approximate_number_of_messages_visible(
                period=one_minute, statistic="Maximum"
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.scale_out_alarm.add_alarm_action(cw_actions.ApplicationScalingAction(scale_out))

        in_flight_or_waiting = cloudwatch.MathExpression(
            expression="visible + notVisible",
            using_metrics={
                "visible": self.import_queue.metric_approximate_number_of_messages_visible(
                    period=one_minute, statistic="Maximum"
                ),
                "notVisible": self.import_queue.metric_approximate_number_of_messages_not_visible(
                    period=one_minute, statistic="Maximum"
                ),
            },
            label="Waiting plus in-flight messages",
            period=one_minute,
        )
        self.scale_in_alarm = cloudwatch.Alarm(
            self,
            "ScaleInAlarm",
            alarm_name="model-import-scale-in",
            alarm_description="No visible and no in-flight messages for 5 minutes: stop the worker",
            metric=in_flight_or_waiting,
            threshold=0,
            evaluation_periods=5,
            datapoints_to_alarm=5,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
        )
        self.scale_in_alarm.add_alarm_action(cw_actions.ApplicationScalingAction(scale_in))

    # -- monitoring --------------------------------------------------------------------------

    def _count_filter(self, construct_id: str, metric_name: str, pattern: logs.IFilterPattern):
        return logs.MetricFilter(
            self,
            construct_id,
            log_group=self.log_group,
            metric_namespace=METRIC_NAMESPACE,
            metric_name=metric_name,
            filter_pattern=pattern,
            metric_value="1",
            unit=cloudwatch.Unit.COUNT,
        )

    def _value_filter(
        self,
        construct_id: str,
        metric_name: str,
        event: str,
        field: str,
        unit: cloudwatch.Unit,
        *,
        reason: str | None = None,
    ):
        """A metric from a numeric field of the lines of one event; with ``reason`` only the
        lines that carry exactly that reason (``DeadlineStopSeconds``)."""
        conditions = [logs.FilterPattern.string_value("$.event", "=", event)]
        if reason is not None:
            conditions.append(logs.FilterPattern.string_value("$.reason", "=", reason))
        conditions.append(logs.FilterPattern.exists(f"$.{field}"))
        return logs.MetricFilter(
            self,
            construct_id,
            log_group=self.log_group,
            metric_namespace=METRIC_NAMESPACE,
            metric_name=metric_name,
            filter_pattern=logs.FilterPattern.all(*conditions),
            metric_value=f"$.{field}",
            unit=unit,
        )

    def _build_import_rules_metrics(self) -> None:
        """u2-import-rules, ``monitoring-design.md``: the count and value filters for the
        durations, leftovers, emails not sent, and the time limit. No alarm reads them."""
        outcome = logs.FilterPattern.string_value
        for construct_id, role in (("EmailNotSentSender", "sender"), ("EmailNotSentTeam", "team")):
            self._count_filter(
                construct_id,
                construct_id,
                logs.FilterPattern.all(
                    outcome("$.event", "=", "email_not_sent"),
                    outcome("$.recipientsNotReached", "=", role),
                ),
            )
        self._count_filter(
            "MarkerUnreadable", "MarkerUnreadable", outcome("$.event", "=", "marker_unreadable")
        )
        for construct_id, field, unit in (
            ("FrontDoorSeconds", "frontDoorSeconds", cloudwatch.Unit.SECONDS),  # NFR1.11
            ("ProofSeconds", "proofSeconds", cloudwatch.Unit.SECONDS),  # NFR1.13
            ("LeftoversTotal", "leftoversTotal", cloudwatch.Unit.COUNT),  # NFR5.14
            ("OlderFilesRemoved", "olderFilesRemoved", cloudwatch.Unit.COUNT),
        ):
            self._value_filter(construct_id, construct_id, "import_outcome", field, unit)
        # NFR1.10: the total time of the imports the time limit stopped. The text is the constant
        # the application writes as the reason, so the filter and the code cannot drift apart.
        self._value_filter(
            "DeadlineStopSeconds",
            "DeadlineStopSeconds",
            "import_outcome",
            "totalSeconds",
            cloudwatch.Unit.SECONDS,
            reason=REASON_TIME_LIMIT,
        )

    def _alert(
        self,
        construct_id: str,
        name: str,
        description: str,
        metric: cloudwatch.IMetric,
        *,
        threshold: float = 1,
        evaluation_periods: int = 1,
        operator: cloudwatch.ComparisonOperator = (
            cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD
        ),
    ) -> cloudwatch.Alarm:
        alarm = cloudwatch.Alarm(
            self,
            construct_id,
            alarm_name=name,
            alarm_description=description,
            metric=metric,
            threshold=threshold,
            evaluation_periods=evaluation_periods,
            comparison_operator=operator,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        alarm.add_alarm_action(cw_actions.SnsAction(self.alert_topic))
        return alarm

    def _build_monitoring(self) -> None:
        """Metric filters on the worker log and the alerts (a) to (f) of monitoring-design.md."""
        five_minutes = Duration.minutes(5)
        one_minute = Duration.minutes(1)
        outcome = logs.FilterPattern.string_value
        finished = outcome("$.event", "=", "import_outcome")

        outcome_filters = {}
        for construct_id, metric_name, value in (
            ("OutcomeStored", "ImportOutcomeStored", "stored"),
            ("OutcomeRemovedWithEmail", "ImportOutcomeRemovedWithEmail", "removed-with-email"),
            (
                "OutcomeRemovedEmailFailed",
                "ImportOutcomeRemovedEmailFailed",
                "removed-email-failed",
            ),
            # u2-import-rules (NFR5.20, NFR4.12): the two outcomes of an event that is not run.
            ("OutcomeRejected", "ImportOutcomeRejected", "rejected"),
            (
                "OutcomeRefusedAlreadyStored",
                "ImportOutcomeRefusedAlreadyStored",
                "refused-already-stored",
            ),
        ):
            outcome_filters[value] = self._count_filter(
                construct_id,
                metric_name,
                logs.FilterPattern.all(finished, outcome("$.outcome", "=", value)),
            )
        # u2-import-rules (NFR5.18): alert (a) reads its own filter, so the count of the outcome
        # removed-email-failed above stays a pure count by outcome (NFR4.12).
        email_not_reached = self._count_filter(
            "EmailNotReached",
            "EmailNotReached",
            logs.FilterPattern.all(
                finished, logs.FilterPattern.boolean_value("$.emailSent", False)
            ),
        )
        removal_incomplete = self._count_filter(
            "RemovalIncomplete",
            "RemovalIncomplete",
            logs.FilterPattern.all(
                finished, logs.FilterPattern.boolean_value("$.removalComplete", False)
            ),
        )
        email_limit = self._count_filter(
            "EmailLimitReached",
            "EmailLimitReached",
            outcome("$.event", "=", "email_limit_reached"),
        )
        self._value_filter(
            "TotalSeconds",
            "ImportTotalSeconds",
            "import_outcome",
            "totalSeconds",
            cloudwatch.Unit.SECONDS,
        )
        self._value_filter(
            "DownloadSeconds",
            "DownloadSeconds",
            "import_outcome",
            "downloadSeconds",
            cloudwatch.Unit.SECONDS,
        )
        self._value_filter(
            "ScanSeconds", "ScanSeconds", "import_outcome", "scanSeconds", cloudwatch.Unit.SECONDS
        )
        self._value_filter(
            "KeepSeconds", "KeepSeconds", "import_outcome", "keepSeconds", cloudwatch.Unit.SECONDS
        )
        self._value_filter(
            "BytesDownloaded", "BytesDownloaded", "file_downloaded", "bytes", cloudwatch.Unit.BYTES
        )
        self._value_filter(
            "FindingsTotal",
            "FindingsTotal",
            "import_outcome",
            "findingsTotal",
            cloudwatch.Unit.COUNT,
        )
        self._build_import_rules_metrics()

        self.alarms: dict[str, cloudwatch.Alarm] = {}
        self.alarms["a"] = self._alert(
            "AlarmEmailFailed",
            "model-import-a-failure-email-not-sent",
            "An email about an import did not reach everyone: a failed import, a rejected event, "
            "or an event for a model that is already stored. Check the outcome object of the "
            "import (state/<importId>/outcome.json) first.",
            email_not_reached.metric(statistic="Sum", period=five_minutes),
        )
        self.alarms["b"] = self._alert(
            "AlarmRemovalIncomplete",
            "model-import-b-removal-not-complete",
            "Files of a failed import may still be in the buckets. Remove them by hand.",
            removal_incomplete.metric(statistic="Sum", period=five_minutes),
        )
        self.alarms["d-event"] = self._alert(
            "AlarmEventDeadLetterQueue",
            "model-import-d-event-dead-letter-queue",
            "A start event could not be put in the import queue. "
            "Check the event dead-letter queue.",
            self.event_dlq.metric_number_of_messages_sent(statistic="Sum", period=one_minute),
        )
        self.alarms["d-queue"] = self._alert(
            "AlarmImportDeadLetterQueue",
            "model-import-d-import-dead-letter-queue",
            "A message moved to the import dead-letter queue. " + _DEAD_LETTER_ADVICE,
            cloudwatch.MathExpression(
                expression="RATE(visible)",
                using_metrics={
                    "visible": self.import_dlq.metric_approximate_number_of_messages_visible(
                        period=one_minute, statistic="Maximum"
                    )
                },
                label="New dead-letter messages",
                period=one_minute,
            ),
            threshold=0,
            operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        )
        self.alarms["d-reminder"] = self._alert(
            "AlarmImportDeadLetterReminder",
            "model-import-d-import-dead-letter-reminder",
            "A message has waited in the import dead-letter queue for 60 minutes. Empty the "
            "queue. " + _DEAD_LETTER_ADVICE,
            self.import_dlq.metric_approximate_number_of_messages_visible(
                period=one_minute, statistic="Maximum"
            ),
            evaluation_periods=60,
        )
        self.alarms["e"] = self._alert(
            "AlarmEmailLimit",
            "model-import-e-email-limit-reached",
            "A daily email limit is reached: 30 failure emails a day, or 20 rejection and "
            "already-stored emails a day. The log record email_limit_reached has the field kind "
            "(failure or notice) that says which one.",
            email_limit.metric(statistic="Sum", period=five_minutes),
        )
        self.alarms["stuck"] = self._alert(
            "AlarmStuck",
            "model-import-stuck-oldest-message",
            "The oldest message in the import queue is older than 10 hours.",
            self.import_queue.metric_approximate_age_of_oldest_message(
                period=five_minutes, statistic="Maximum"
            ),
            threshold=10 * 60 * 60,
            operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        )

        # (c) A worker task that stopped with an error or never started. The default event bus
        # receives the ECS events. A normal scale-in stop exits with code 0 and does not match.
        self.stopped_task_rule = events.Rule(
            self,
            "WorkerStoppedRule",
            rule_name="model-import-c-worker-stopped",
            description="Alert (c): a worker task stopped with an error",
            event_pattern=events.EventPattern(
                source=["aws.ecs"],
                detail_type=["ECS Task State Change"],
                detail={
                    "clusterArn": [self.cluster.cluster_arn],
                    "lastStatus": ["STOPPED"],
                    "$or": [
                        {"containers": {"exitCode": [{"anything-but": 0}]}},
                        {"stopCode": ["TaskFailedToStart"]},
                    ],
                },
            ),
        )
        self.stopped_task_rule.add_target(_TopicTarget(self.alert_topic))

    def _build_budget(self, limit: float) -> None:
        """NFR5.10: a monthly cost budget for the resources tagged ``app``."""
        self.budget = budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="model-import-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=limit, unit="USD"),
                cost_filters={"TagKeyValue": [f"user:app${APP_TAG}"]},
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        notification_type="ACTUAL",
                        comparison_operator="GREATER_THAN",
                        threshold=100,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            subscription_type="SNS", address=self.alert_topic.topic_arn
                        )
                    ],
                )
            ],
        )
        # Budgets checks that it may publish when the budget is created.
        self.budget.node.add_dependency(self.alert_topic.node.find_child("Policy"))

    def _build_outputs(self) -> None:
        CfnOutput(self, "EventBusName", value=self.event_bus.event_bus_name)
        CfnOutput(self, "ImportQueueUrl", value=self.import_queue.queue_url)
        CfnOutput(self, "HoldingBucketName", value=self.holding_bucket.bucket_name)
        CfnOutput(self, "FinalBucketName", value=self.final_bucket.bucket_name)
        CfnOutput(self, "AlertTopicArn", value=self.alert_topic.topic_arn)
