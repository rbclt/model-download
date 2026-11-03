"""Template tests for the queues, events, network and compute (NFR2.3, NFR3.3, NFR4.2, NFR4.6)."""

from __future__ import annotations

from tests.infra.synth_helpers import ACCOUNT, Synthesised, statements


def test_import_queue_is_a_fifo_queue_with_the_designed_settings_nfr4_2(synth: Synthesised) -> None:
    properties = synth.properties(synth.stack.import_queue)

    assert properties["QueueName"] == "import-queue.fifo"
    assert properties["FifoQueue"] is True
    assert properties["ContentBasedDeduplication"] is True
    assert properties["VisibilityTimeout"] == 15 * 60
    assert properties["MessageRetentionPeriod"] == 4 * 24 * 60 * 60
    assert properties["SqsManagedSseEnabled"] is True


def test_import_queue_moves_a_message_to_the_fifo_dead_letter_queue_on_first_receive(
    synth: Synthesised,
) -> None:
    redrive = synth.properties(synth.stack.import_queue)["RedrivePolicy"]
    dlq = synth.properties(synth.stack.import_dlq)

    assert redrive["maxReceiveCount"] == 1
    assert redrive["deadLetterTargetArn"] == {
        "Fn::GetAtt": [synth.logical_id(synth.stack.import_dlq), "Arn"]
    }
    assert dlq["QueueName"] == "import-dlq.fifo"
    assert dlq["FifoQueue"] is True
    assert dlq["MessageRetentionPeriod"] == 14 * 24 * 60 * 60
    assert dlq["SqsManagedSseEnabled"] is True


def test_event_dead_letter_queue_is_a_standard_encrypted_queue_nfr4_11(synth: Synthesised) -> None:
    properties = synth.properties(synth.stack.event_dlq)

    assert properties["QueueName"] == "event-dlq"
    assert "FifoQueue" not in properties
    assert properties["MessageRetentionPeriod"] == 14 * 24 * 60 * 60
    assert properties["SqsManagedSseEnabled"] is True


def test_start_rule_matches_contract_c1_and_targets_the_queue_nfr4_6(synth: Synthesised) -> None:
    properties = synth.properties(synth.stack.start_rule)
    (target,) = properties["Targets"]

    assert properties["Name"] == "start-import"
    assert properties["EventPattern"] == {
        "source": ["model.import"],
        "detail-type": ["ModelImportRequested"],
    }
    assert properties["EventBusName"] == {"Ref": synth.logical_id(synth.stack.event_bus)}
    assert target["Arn"] == {"Fn::GetAtt": [synth.logical_id(synth.stack.import_queue), "Arn"]}
    assert target["SqsParameters"] == {"MessageGroupId": "imports"}
    assert target["RetryPolicy"] == {"MaximumEventAgeInSeconds": 86400, "MaximumRetryAttempts": 185}
    assert target["DeadLetterConfig"] == {
        "Arn": {"Fn::GetAtt": [synth.logical_id(synth.stack.event_dlq), "Arn"]}
    }


def test_queue_policies_let_only_the_start_rule_send_nfr4_6(synth: Synthesised) -> None:
    rule_arn = {"Fn::GetAtt": [synth.logical_id(synth.stack.start_rule), "Arn"]}
    for queue in (synth.stack.import_queue, synth.stack.event_dlq):
        policy = synth.json["Resources"][synth.logical_id(queue.node.find_child("Policy"))]
        (statement,) = statements(policy["Properties"]["PolicyDocument"])

        assert statement["Principal"] == {"Service": "events.amazonaws.com"}
        assert "sqs:SendMessage" in statement["Action"]
        assert statement["Condition"] == {"ArnEquals": {"aws:SourceArn": rule_arn}}
    # The FIFO dead-letter queue is filled by the redrive policy and needs no queue policy.
    assert synth.stack.import_dlq.node.try_find_child("Policy") is None


def test_event_bus_is_custom_and_accepts_events_only_from_this_account_nfr3_3(
    synth: Synthesised,
) -> None:
    assert synth.properties(synth.stack.event_bus)["Name"] == "model-import"
    policies = [
        resource["Properties"]["Statement"]
        for resource in synth.resources("AWS::Events::EventBusPolicy").values()
    ]
    allow = next(p for p in policies if p["Effect"] == "Allow")
    deny = next(p for p in policies if p["Effect"] == "Deny")

    assert allow["Action"] == "events:PutEvents"
    assert f"{ACCOUNT}:root" in str(allow["Principal"])
    assert deny["Action"] == "events:PutEvents"
    assert deny["Condition"] == {"StringNotEquals": {"aws:PrincipalAccount": ACCOUNT}}


def test_service_never_runs_a_second_worker_and_rolls_back_a_bad_deployment_nfr2_3(
    synth: Synthesised,
) -> None:
    properties = synth.properties(synth.stack.service)
    deployment = properties["DeploymentConfiguration"]

    assert properties["ServiceName"] == "import-worker"
    assert properties["DesiredCount"] == 0
    assert deployment["MinimumHealthyPercent"] == 0
    assert deployment["MaximumPercent"] == 100
    assert deployment["DeploymentCircuitBreaker"] == {"Enable": True, "Rollback": True}
    assert properties["NetworkConfiguration"]["AwsvpcConfiguration"]["AssignPublicIp"] == "ENABLED"


def test_task_definition_size_architecture_and_disk(synth: Synthesised) -> None:
    properties = synth.properties(synth.stack.task_definition)

    assert properties["Cpu"] == "4096"
    assert properties["Memory"] == "8192"
    assert properties["EphemeralStorage"] == {"SizeInGiB": 30}
    assert properties["RuntimePlatform"] == {
        "CpuArchitecture": "X86_64",
        "OperatingSystemFamily": "LINUX",
    }
    assert properties["RequiresCompatibilities"] == ["FARGATE"]
    assert properties["TaskRoleArn"] != properties["ExecutionRoleArn"]  # two separate roles


def test_container_gets_its_settings_but_no_hugging_face_token_nfr3_2(synth: Synthesised) -> None:
    (container,) = synth.properties(synth.stack.task_definition)["ContainerDefinitions"]
    environment = {item["Name"]: item["Value"] for item in container["Environment"]}

    assert set(environment) == {
        "MODEL_IMPORT_HOLDING_BUCKET",
        "MODEL_IMPORT_FINAL_BUCKET",
        "MODEL_IMPORT_QUEUE_URL",
        "MODEL_IMPORT_SENDER_ADDRESS",
        "MODEL_IMPORT_TEAM_ADDRESS",
        "MODEL_IMPORT_SES_CONFIGURATION_SET",
        "AWS_REGION",
    }
    assert environment["MODEL_IMPORT_SES_CONFIGURATION_SET"] == "model-import"
    assert environment["MODEL_IMPORT_QUEUE_URL"] == {
        "Ref": synth.logical_id(synth.stack.import_queue)
    }
    assert not any("TOKEN" in name or "SECRET" in name for name in environment)
    assert "Secrets" not in container


def test_container_logs_go_to_the_worker_log_group_with_30_day_retention(
    synth: Synthesised,
) -> None:
    (container,) = synth.properties(synth.stack.task_definition)["ContainerDefinitions"]
    log_group = synth.properties(synth.stack.log_group)

    assert container["LogConfiguration"]["LogDriver"] == "awslogs"
    assert container["LogConfiguration"]["Options"]["awslogs-stream-prefix"] == "container"
    assert container["LogConfiguration"]["Options"]["awslogs-group"] == {
        "Ref": synth.logical_id(synth.stack.log_group)
    }
    assert log_group["LogGroupName"] == "/model-import/worker"
    assert log_group["RetentionInDays"] == 30


def test_network_has_two_public_subnets_no_nat_an_s3_endpoint_and_a_closed_security_group(
    synth: Synthesised,
) -> None:
    subnets = synth.resources("AWS::EC2::Subnet").values()
    endpoint = next(iter(synth.resources("AWS::EC2::VPCEndpoint").values()))["Properties"]
    group = synth.properties(synth.stack.security_group)

    assert len(subnets) == 2
    assert len({str(s["Properties"]["AvailabilityZone"]) for s in subnets}) == 2
    assert all(s["Properties"]["MapPublicIpOnLaunch"] is True for s in subnets)
    assert not synth.resources("AWS::EC2::NatGateway")
    assert endpoint["VpcEndpointType"] == "Gateway"
    assert endpoint["ServiceName"]["Fn::Join"][1][-1] == ".s3"
    assert "SecurityGroupIngress" not in group
    assert group["SecurityGroupEgress"] == [
        {
            "CidrIp": "0.0.0.0/0",
            "Description": "Allow all outbound traffic by default",
            "IpProtocol": "-1",
        }
    ]


def test_cluster_has_container_insights_on(synth: Synthesised) -> None:
    settings = synth.properties(synth.stack.cluster)["ClusterSettings"]

    assert {"Name": "containerInsights", "Value": "enabled"} in settings


def test_every_taggable_resource_carries_the_app_and_env_tags(synth: Synthesised) -> None:
    expected = [{"Key": "app", "Value": "model-import-scan"}, {"Key": "env", "Value": "staging"}]
    for construct in (
        synth.stack.holding_bucket,
        synth.stack.final_bucket,
        synth.stack.import_queue,
        synth.stack.import_dlq,
        synth.stack.event_dlq,
        synth.stack.cluster,
        synth.stack.service,
        synth.stack.alert_topic,
        synth.stack.task_role,
        synth.stack.log_group,
        synth.stack.start_rule,
    ):
        tags = synth.properties(construct)["Tags"]
        assert sorted(tags, key=lambda tag: tag["Key"]) == expected, construct.node.id
    assert synth.json["Resources"]["MonthlyBudget"]["Properties"]["ResourceTags"] == expected
