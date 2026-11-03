"""Template tests for u2-import-rules: the stack changes of ``infrastructure-specification.md``
and ``monitoring-design.md`` (NFR3.17, NFR3.18, NFR4.19, NFR5.13, NFR5.14, NFR5.17 to NFR5.20).

This Unit adds no resource except metric filters, and no role or permission. The tests check the
task role against the list recorded for u1, the filters and alarms that changed, and the ones that
must not have changed.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest
from tests.infra.synth_helpers import Synthesised, statements

from model_import.deadline import REASON_TIME_LIMIT

# The statements of the task role, recorded from the stack of u1-import-slice BEFORE this Unit
# changed it (NFR3.17). Logical ids are those of the template. A change to the role, a resource,
# an action, or a condition makes the first test fail.
RECORDED_U1_TASK_ROLE_STATEMENTS: list[dict[str, Any]] = [
    {
        "Sid": "ObjectsInOwnPrefixes",
        "Effect": "Allow",
        "Action": [
            "s3:GetObject",
            "s3:PutObject",
            "s3:DeleteObject",
            "s3:AbortMultipartUpload",
            "s3:ListMultipartUploadParts",
        ],
        "Resource": [
            {"Fn::Join": ["", [{"Fn::GetAtt": ["HoldingBucket76F58CB4", "Arn"]}, "/holding/*"]]},
            {"Fn::Join": ["", [{"Fn::GetAtt": ["HoldingBucket76F58CB4", "Arn"]}, "/state/*"]]},
            {"Fn::Join": ["", [{"Fn::GetAtt": ["FinalBucket3B40AFDB", "Arn"]}, "/models/*"]]},
        ],
    },
    {
        "Sid": "ListWholeBuckets",
        "Effect": "Allow",
        "Action": ["s3:ListBucket", "s3:ListBucketMultipartUploads"],
        "Resource": [
            {"Fn::GetAtt": ["HoldingBucket76F58CB4", "Arn"]},
            {"Fn::GetAtt": ["FinalBucket3B40AFDB", "Arn"]},
        ],
    },
    {
        "Sid": "ReadImportQueue",
        "Effect": "Allow",
        "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility"],
        "Resource": [{"Fn::GetAtt": ["ImportQueueB0839EEE", "Arn"]}],
    },
    {
        "Sid": "ProtectOwnTask",
        "Effect": "Allow",
        "Action": ["ecs:UpdateTaskProtection"],
        "Resource": [
            {
                "Fn::Join": [
                    "",
                    [
                        "arn:",
                        {"Ref": "AWS::Partition"},
                        ":ecs:us-east-1:123456789012:task/",
                        {"Ref": "ClusterEB0386A7"},
                        "/*",
                    ],
                ]
            }
        ],
    },
    {
        "Sid": "SendFailureEmail",
        "Effect": "Allow",
        "Action": ["ses:SendEmail"],
        "Condition": {"StringEquals": {"ses:FromAddress": "imports@example.com"}},
        "Resource": [
            {
                "Fn::Join": [
                    "",
                    [
                        "arn:",
                        {"Ref": "AWS::Partition"},
                        ":ses:us-east-1:123456789012:identity/imports@example.com",
                    ],
                ]
            },
            {
                "Fn::Join": [
                    "",
                    [
                        "arn:",
                        {"Ref": "AWS::Partition"},
                        ":ses:us-east-1:123456789012:configuration-set/model-import",
                    ],
                ]
            },
        ],
    },
]

# The resource types of the template, without the metric filters, as they were when this Unit
# began (the only resources it adds are metric filters).
RESOURCE_COUNTS_WITHOUT_FILTERS = {
    "AWS::ApplicationAutoScaling::ScalableTarget": 1,
    "AWS::ApplicationAutoScaling::ScalingPolicy": 2,
    "AWS::Budgets::Budget": 1,
    "AWS::CloudWatch::Alarm": 9,
    "AWS::EC2::InternetGateway": 1,
    "AWS::EC2::Route": 2,
    "AWS::EC2::RouteTable": 2,
    "AWS::EC2::SecurityGroup": 1,
    "AWS::EC2::Subnet": 2,
    "AWS::EC2::SubnetRouteTableAssociation": 2,
    "AWS::EC2::VPC": 1,
    "AWS::EC2::VPCEndpoint": 1,
    "AWS::EC2::VPCGatewayAttachment": 1,
    "AWS::ECS::Cluster": 1,
    "AWS::ECS::Service": 1,
    "AWS::ECS::TaskDefinition": 1,
    "AWS::Events::EventBus": 1,
    "AWS::Events::EventBusPolicy": 2,
    "AWS::Events::Rule": 2,
    "AWS::IAM::Policy": 1,
    "AWS::IAM::Role": 3,
    "AWS::Logs::LogGroup": 1,
    "AWS::S3::Bucket": 2,
    "AWS::S3::BucketPolicy": 2,
    "AWS::SES::ConfigurationSet": 1,
    "AWS::SES::ConfigurationSetEventDestination": 1,
    "AWS::SNS::Subscription": 1,
    "AWS::SNS::Topic": 1,
    "AWS::SNS::TopicPolicy": 1,
    "AWS::SQS::Queue": 3,
    "AWS::SQS::QueuePolicy": 2,
}

OUTCOME_FILTERS = {
    "ImportOutcomeStored": "stored",
    "ImportOutcomeRemovedWithEmail": "removed-with-email",
    "ImportOutcomeRemovedEmailFailed": "removed-email-failed",
    "ImportOutcomeRejected": "rejected",
    "ImportOutcomeRefusedAlreadyStored": "refused-already-stored",
}


def _filters(synth: Synthesised) -> dict[str, dict[str, Any]]:
    return {
        item["Properties"]["MetricTransformations"][0]["MetricName"]: item["Properties"]
        for item in synth.resources("AWS::Logs::MetricFilter").values()
    }


def _task_role_statements(synth: Synthesised) -> list[dict[str, Any]]:
    (policy,) = synth.properties(synth.stack.task_role)["Policies"]
    return statements(policy["PolicyDocument"])


def _by_sid(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["Sid"]: item for item in items}


def _alarm(synth: Synthesised, key: str) -> dict[str, Any]:
    return synth.properties(synth.stack.alarms[key])


# -- NFR3.17: nothing is added to the role ----
def test_nfr3_17_the_task_role_statements_equal_the_list_recorded_for_u1(
    synth: Synthesised,
) -> None:
    assert _by_sid(_task_role_statements(synth)) == _by_sid(RECORDED_U1_TASK_ROLE_STATEMENTS)


def test_nfr3_17_the_task_role_has_no_s3_wildcard_and_no_resource_star(
    synth: Synthesised,
) -> None:
    text = json.dumps(synth.properties(synth.stack.task_role))

    assert "s3:*" not in text and '"*:' not in text
    for statement in _task_role_statements(synth):
        assert "*" not in statement["Resource"] and "*" not in statement["Action"]
        assert not any(action.endswith(":*") for action in statement["Action"])


def test_nfr3_17_no_resource_other_than_metric_filters_was_added(synth: Synthesised) -> None:
    counts = Counter(resource["Type"] for resource in synth.json["Resources"].values())
    filters = counts.pop("AWS::Logs::MetricFilter")

    assert dict(counts) == RESOURCE_COUNTS_WITHOUT_FILTERS
    assert filters == 22  # the 11 of u1 and the 11 of this Unit


def test_nfr4_9_the_lifecycle_rules_are_unchanged_from_u1(synth: Synthesised) -> None:
    holding = synth.properties(synth.stack.holding_bucket)["LifecycleConfiguration"]["Rules"]
    final = synth.properties(synth.stack.final_bucket)["LifecycleConfiguration"]["Rules"]

    assert sorted(holding, key=lambda rule: rule["Id"]) == [
        {
            "Id": "abort-incomplete-multipart-uploads",
            "Status": "Enabled",
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
        },
        {"Id": "expire-holding", "Status": "Enabled", "Prefix": "holding/", "ExpirationInDays": 2},
        {"Id": "expire-state", "Status": "Enabled", "Prefix": "state/", "ExpirationInDays": 30},
    ]
    # The final bucket never expires a stored model (NFR4.19: no rule can expire unfinished
    # files either, because S3 cannot test for the marker).
    assert final == [
        {
            "Id": "abort-incomplete-multipart-uploads",
            "Status": "Enabled",
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
        }
    ]


# -- NFR5.18: alert (a) ----
def test_nfr5_18_alert_a_reads_email_not_reached_and_the_outcome_count_stays_a_count(
    synth: Synthesised,
) -> None:
    filters = _filters(synth)
    alarm = _alarm(synth, "a")

    assert (alarm["MetricName"], alarm["Namespace"]) == ("EmailNotReached", "ModelImport")
    assert (alarm["Period"], alarm["Threshold"], alarm["Statistic"]) == (300, 1, "Sum")
    assert alarm["AlarmName"] == "model-import-a-failure-email-not-sent"  # the name is kept
    pattern = filters["EmailNotReached"]["FilterPattern"]
    assert '$.event = "import_outcome"' in pattern and "$.emailSent IS FALSE" in pattern
    assert "$.outcome" not in pattern  # any outcome whose email was not accepted
    assert filters["EmailNotReached"]["MetricTransformations"][0]["MetricValue"] == "1"
    # The count of the outcome removed-email-failed is exactly what it was: no emailSent test.
    kept = filters["ImportOutcomeRemovedEmailFailed"]["FilterPattern"]
    assert '$.outcome = "removed-email-failed"' in kept and "emailSent" not in kept
    assert all(
        _alarm(synth, key)["MetricName"] != "ImportOutcomeRemovedEmailFailed"
        for key in synth.stack.alarms
        if "MetricName" in _alarm(synth, key)
    )


def test_nfr5_18_alert_a_has_the_same_topic_as_before_and_its_text_names_the_new_cases(
    synth: Synthesised,
) -> None:
    alarm = _alarm(synth, "a")

    assert alarm["AlarmActions"] == [{"Ref": synth.logical_id(synth.stack.alert_topic)}]
    assert "rejected event" in alarm["AlarmDescription"]
    assert "state/<importId>/outcome.json" in alarm["AlarmDescription"]


# -- NFR5.20, NFR4.12: counts by outcome ----
def test_nfr5_20_there_is_one_count_filter_for_each_of_the_five_outcomes(
    synth: Synthesised,
) -> None:
    filters = _filters(synth)

    for metric_name, value in OUTCOME_FILTERS.items():
        properties = filters[metric_name]
        assert f'$.outcome = "{value}"' in properties["FilterPattern"], metric_name
        assert '$.event = "import_outcome"' in properties["FilterPattern"], metric_name
        transformation = properties["MetricTransformations"][0]
        assert (transformation["MetricValue"], transformation["Unit"]) == ("1", "Count")
        assert transformation["MetricNamespace"] == "ModelImport"
    # No alarm is added on the new counts (NFR5.20): alarms stay as listed in u1.
    assert set(synth.stack.alarms) == {"a", "b", "d-event", "d-queue", "d-reminder", "e", "stuck"}
    assert len(synth.resources("AWS::CloudWatch::Alarm")) == 9


# -- NFR5.19: alert (b) ----
def test_nfr5_19_alert_b_has_no_condition_on_the_outcome_so_it_covers_stored_too(
    synth: Synthesised,
) -> None:
    pattern = _filters(synth)["RemovalIncomplete"]["FilterPattern"]

    assert '$.event = "import_outcome"' in pattern
    assert "$.removalComplete IS FALSE" in pattern
    assert "$.outcome" not in pattern  # the outcome stored with a holding folder left is covered
    alarm = _alarm(synth, "b")
    assert (alarm["MetricName"], alarm["Threshold"], alarm["Period"]) == (
        "RemovalIncomplete",
        1,
        300,
    )


# -- NFR5.13, NFR5.14, NFR5.17, NFR1.10: the new filters ----
@pytest.mark.parametrize(
    ("metric", "role"), [("EmailNotSentSender", "sender"), ("EmailNotSentTeam", "team")]
)
def test_nfr5_13_each_role_that_was_not_reached_has_its_own_count(
    synth: Synthesised, metric: str, role: str
) -> None:
    properties = _filters(synth)[metric]

    assert '$.event = "email_not_sent"' in properties["FilterPattern"]
    assert f'$.recipientsNotReached = "{role}"' in properties["FilterPattern"]
    assert properties["MetricTransformations"][0]["MetricValue"] == "1"


def test_nfr5_14_the_unreadable_marker_has_a_count(synth: Synthesised) -> None:
    properties = _filters(synth)["MarkerUnreadable"]

    assert properties["FilterPattern"] == '{ $.event = "marker_unreadable" }'
    assert properties["MetricTransformations"][0]["MetricValue"] == "1"


@pytest.mark.parametrize(
    ("metric", "field", "unit"),
    [
        ("FrontDoorSeconds", "frontDoorSeconds", "Seconds"),
        ("ProofSeconds", "proofSeconds", "Seconds"),
        ("LeftoversTotal", "leftoversTotal", "Count"),
        ("OlderFilesRemoved", "olderFilesRemoved", "Count"),
    ],
)
def test_nfr5_17_the_durations_and_totals_of_the_outcome_line_become_value_metrics(
    synth: Synthesised, metric: str, field: str, unit: str
) -> None:
    properties = _filters(synth)[metric]
    transformation = properties["MetricTransformations"][0]

    assert '$.event = "import_outcome"' in properties["FilterPattern"]
    assert f'$.{field} = "*"' in properties["FilterPattern"]
    assert (transformation["MetricValue"], transformation["Unit"]) == (f"$.{field}", unit)


def test_nfr1_10_the_deadline_stop_filter_matches_the_reason_text_of_the_application(
    synth: Synthesised,
) -> None:
    properties = _filters(synth)["DeadlineStopSeconds"]
    transformation = properties["MetricTransformations"][0]

    # The text comes from the constant the application writes; the literal guards the constant
    # itself, so a change to either one fails here.
    assert REASON_TIME_LIMIT == "the time limit of 6 hours passed"
    assert f'$.reason = "{REASON_TIME_LIMIT}"' in properties["FilterPattern"]
    assert '$.event = "import_outcome"' in properties["FilterPattern"]
    assert (transformation["MetricValue"], transformation["Unit"]) == ("$.totalSeconds", "Seconds")
    # The existing total-time filter has no condition on the reason (it is not changed).
    total = _filters(synth)["ImportTotalSeconds"]["FilterPattern"]
    assert "$.reason" not in total


def test_nfr5_17_every_new_filter_reads_the_worker_log_group(synth: Synthesised) -> None:
    log_group = {"Ref": synth.logical_id(synth.stack.log_group)}

    assert all(properties["LogGroupName"] == log_group for properties in _filters(synth).values())


# -- NFR4.19, NFR3.18: the alarm descriptions ----
@pytest.mark.parametrize("key", ["d-queue", "d-reminder"])
def test_nfr4_19_the_dead_letter_alarms_name_the_record_to_open_and_the_unfinished_files(
    synth: Synthesised, key: str
) -> None:
    description = _alarm(synth, key)["AlarmDescription"]

    assert "state/<importId>/record.json" in description
    assert "keeping" in description and "_COMPLETE" in description and "models/" in description


def test_nfr4_19_the_event_dead_letter_alarm_text_is_not_changed(synth: Synthesised) -> None:
    assert _alarm(synth, "d-event")["AlarmDescription"] == (
        "A start event could not be put in the import queue. Check the event dead-letter queue."
    )


def test_nfr3_18_alert_e_names_both_email_limits_and_not_the_old_one(synth: Synthesised) -> None:
    alarm = _alarm(synth, "e")

    assert "30 failure emails" in alarm["AlarmDescription"]
    assert "20 rejection and already-stored emails" in alarm["AlarmDescription"]
    assert "50" not in alarm["AlarmDescription"]
    assert "kind" in alarm["AlarmDescription"]
    assert (alarm["MetricName"], alarm["Threshold"], alarm["Period"]) == (
        "EmailLimitReached",
        1,
        300,
    )
