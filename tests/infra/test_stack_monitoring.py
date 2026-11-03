"""Template tests for scaling, alarms, metric filters, email and budget (monitoring-design.md)."""

from __future__ import annotations

import pytest
from infra.model_import_stack import ModelImportStackError
from tests.infra.synth_helpers import ACCOUNT, TEAM, Synthesised, make_stack, statements, synthesise


def _alarm(synth: Synthesised, key: str) -> dict:
    return synth.properties(synth.stack.alarms[key])


def test_scale_out_alarm_starts_the_worker_for_one_visible_message_nfr2_3(
    synth: Synthesised,
) -> None:
    alarm = synth.properties(synth.stack.scale_out_alarm)
    queue = synth.logical_id(synth.stack.import_queue)

    assert alarm["MetricName"] == "ApproximateNumberOfMessagesVisible"
    assert alarm["Dimensions"] == [
        {"Name": "QueueName", "Value": {"Fn::GetAtt": [queue, "QueueName"]}}
    ]
    assert (alarm["Threshold"], alarm["Period"], alarm["EvaluationPeriods"]) == (1, 60, 1)
    assert alarm["ComparisonOperator"] == "GreaterThanOrEqualToThreshold"
    (action,) = alarm["AlarmActions"]
    policies = synth.resources("AWS::ApplicationAutoScaling::ScalingPolicy")
    config = policies[action["Ref"]]["Properties"]["StepScalingPolicyConfiguration"]
    assert config["AdjustmentType"] == "ExactCapacity"
    assert config["StepAdjustments"] == [{"MetricIntervalLowerBound": 0, "ScalingAdjustment": 1}]


def test_scale_in_alarm_uses_visible_plus_in_flight_and_treats_missing_data_as_breaching(
    synth: Synthesised,
) -> None:
    alarm = synth.properties(synth.stack.scale_in_alarm)
    metrics = {item["Id"]: item for item in alarm["Metrics"]}
    names = {
        item["MetricStat"]["Metric"]["MetricName"]
        for item in alarm["Metrics"]
        if "MetricStat" in item
    }

    assert names == {"ApproximateNumberOfMessagesVisible", "ApproximateNumberOfMessagesNotVisible"}
    assert metrics["expr_1"]["Expression"] == "visible + notVisible"
    assert (alarm["Threshold"], alarm["EvaluationPeriods"], alarm["DatapointsToAlarm"]) == (0, 5, 5)
    assert alarm["ComparisonOperator"] == "LessThanOrEqualToThreshold"
    assert alarm["TreatMissingData"] == "breaching"
    (action,) = alarm["AlarmActions"]
    policy = synth.resources("AWS::ApplicationAutoScaling::ScalingPolicy")[action["Ref"]]
    assert policy["Properties"]["StepScalingPolicyConfiguration"]["StepAdjustments"] == [
        {"MetricIntervalUpperBound": 0, "ScalingAdjustment": 0}
    ]


def test_scalable_target_runs_zero_or_one_worker(synth: Synthesised) -> None:
    (target,) = synth.resources("AWS::ApplicationAutoScaling::ScalableTarget").values()

    assert target["Properties"]["MinCapacity"] == 0
    assert target["Properties"]["MaxCapacity"] == 1
    assert target["Properties"]["ScalableDimension"] == "ecs:service:DesiredCount"


def test_every_alert_alarm_notifies_the_alert_topic_nfr5_8(synth: Synthesised) -> None:
    topic = {"Ref": synth.logical_id(synth.stack.alert_topic)}

    assert set(synth.stack.alarms) == {"a", "b", "d-event", "d-queue", "d-reminder", "e", "stuck"}
    for key in synth.stack.alarms:
        assert _alarm(synth, key)["AlarmActions"] == [topic], key


def test_alarms_a_b_and_e_count_log_metrics_over_five_minutes(synth: Synthesised) -> None:
    expected = {
        # Changed from u1 (NFR5.18): alert (a) reads EmailNotReached, no longer the count of the
        # outcome removed-email-failed, so it also covers a rejected or refused event.
        "a": "EmailNotReached",
        "b": "RemovalIncomplete",
        "e": "EmailLimitReached",
    }
    for key, metric_name in expected.items():
        alarm = _alarm(synth, key)
        assert alarm["MetricName"] == metric_name
        assert alarm["Namespace"] == "ModelImport"
        assert (alarm["Period"], alarm["Threshold"], alarm["Statistic"]) == (300, 1, "Sum")


def test_dead_letter_alarms_use_sent_count_rate_and_a_60_minute_reminder_nfr4_11(
    synth: Synthesised,
) -> None:
    event = _alarm(synth, "d-event")
    queue = _alarm(synth, "d-queue")
    reminder = _alarm(synth, "d-reminder")

    assert (event["MetricName"], event["Statistic"], event["Period"]) == (
        "NumberOfMessagesSent",
        "Sum",
        60,
    )
    assert any(item.get("Expression") == "RATE(visible)" for item in queue["Metrics"])
    assert queue["ComparisonOperator"] == "GreaterThanThreshold"
    assert queue["Threshold"] == 0
    assert (reminder["MetricName"], reminder["Period"], reminder["EvaluationPeriods"]) == (
        "ApproximateNumberOfMessagesVisible",
        60,
        60,
    )


def test_stuck_alarm_fires_when_the_oldest_message_is_over_ten_hours_old(
    synth: Synthesised,
) -> None:
    alarm = _alarm(synth, "stuck")

    assert alarm["MetricName"] == "ApproximateAgeOfOldestMessage"
    assert alarm["Threshold"] == 10 * 60 * 60
    assert alarm["ComparisonOperator"] == "GreaterThanThreshold"


def test_stopped_task_rule_alerts_on_error_exit_or_failed_start_and_not_on_exit_zero(
    synth: Synthesised,
) -> None:
    properties = synth.properties(synth.stack.stopped_task_rule)
    pattern = properties["EventPattern"]

    assert pattern["source"] == ["aws.ecs"]
    assert pattern["detail-type"] == ["ECS Task State Change"]
    assert pattern["detail"]["clusterArn"] == [
        {"Fn::GetAtt": [synth.logical_id(synth.stack.cluster), "Arn"]}
    ]
    assert pattern["detail"]["lastStatus"] == ["STOPPED"]
    assert pattern["detail"]["$or"] == [
        {"containers": {"exitCode": [{"anything-but": 0}]}},
        {"stopCode": ["TaskFailedToStart"]},
    ]
    assert properties["Targets"][0]["Arn"] == {"Ref": synth.logical_id(synth.stack.alert_topic)}


def test_metric_filters_make_the_outcome_count_and_duration_metrics(synth: Synthesised) -> None:
    filters = {
        item["Properties"]["MetricTransformations"][0]["MetricName"]: item["Properties"]
        for item in synth.resources("AWS::Logs::MetricFilter").values()
    }

    assert set(filters) == {
        "ImportOutcomeStored",
        "ImportOutcomeRemovedWithEmail",
        "ImportOutcomeRemovedEmailFailed",
        "RemovalIncomplete",
        "EmailLimitReached",
        "ImportTotalSeconds",
        "DownloadSeconds",
        "ScanSeconds",
        "KeepSeconds",
        "BytesDownloaded",
        "FindingsTotal",
        # u2-import-rules (the full list is checked in test_stack_import_rules.py)
        "ImportOutcomeRejected",
        "ImportOutcomeRefusedAlreadyStored",
        "EmailNotReached",
        "EmailNotSentSender",
        "EmailNotSentTeam",
        "MarkerUnreadable",
        "FrontDoorSeconds",
        "ProofSeconds",
        "LeftoversTotal",
        "OlderFilesRemoved",
        "DeadlineStopSeconds",
    }
    failed = filters["ImportOutcomeRemovedEmailFailed"]
    assert '$.outcome = "removed-email-failed"' in failed["FilterPattern"]
    assert failed["MetricTransformations"][0]["MetricValue"] == "1"
    assert "$.removalComplete IS FALSE" in filters["RemovalIncomplete"]["FilterPattern"]
    assert '$.event = "email_limit_reached"' in filters["EmailLimitReached"]["FilterPattern"]
    total = filters["ImportTotalSeconds"]["MetricTransformations"][0]
    assert (total["MetricValue"], total["MetricNamespace"], total["Unit"]) == (
        "$.totalSeconds",
        "ModelImport",
        "Seconds",
    )
    assert filters["BytesDownloaded"]["MetricTransformations"][0]["MetricValue"] == "$.bytes"
    assert all(
        properties["LogGroupName"] == {"Ref": synth.logical_id(synth.stack.log_group)}
        for properties in filters.values()
    )


def test_ses_configuration_set_sends_bounces_and_complaints_to_the_alert_topic_nfr3_9(
    synth: Synthesised,
) -> None:
    assert synth.properties(synth.stack.configuration_set)["Name"] == "model-import"
    (destination,) = synth.resources("AWS::SES::ConfigurationSetEventDestination").values()
    definition = destination["Properties"]["EventDestination"]

    assert sorted(definition["MatchingEventTypes"]) == ["bounce", "complaint"]
    assert definition["SnsDestination"] == {
        "TopicARN": {"Ref": synth.logical_id(synth.stack.alert_topic)}
    }


def test_alert_topic_has_the_team_subscription_and_the_four_service_principals_nfr5_8(
    synth: Synthesised,
) -> None:
    properties = synth.properties(synth.stack.alert_topic)
    (subscription,) = synth.resources("AWS::SNS::Subscription").values()
    policy = synth.json["Resources"][
        synth.logical_id(synth.stack.alert_topic.node.find_child("Policy"))
    ]
    publishers = {
        s["Principal"]["Service"]
        for s in statements(policy["Properties"]["PolicyDocument"])
        if s["Effect"] == "Allow"
    }

    assert properties["TopicName"] == "model-import-alerts"
    assert (subscription["Properties"]["Protocol"], subscription["Properties"]["Endpoint"]) == (
        "email",
        TEAM,
    )
    assert publishers == {
        "cloudwatch.amazonaws.com",
        "events.amazonaws.com",
        "ses.amazonaws.com",
        "budgets.amazonaws.com",
    }


def test_budget_defaults_to_100_dollars_filtered_by_the_app_tag_and_alerts_the_topic_nfr5_10(
    synth: Synthesised,
) -> None:
    budget = synth.json["Resources"]["MonthlyBudget"]["Properties"]
    (notification,) = budget["NotificationsWithSubscribers"]

    assert budget["Budget"]["BudgetLimit"] == {"Amount": 100, "Unit": "USD"}
    assert budget["Budget"]["TimeUnit"] == "MONTHLY"
    assert budget["Budget"]["CostFilters"] == {"TagKeyValue": ["user:app$model-import-scan"]}
    assert notification["Notification"]["Threshold"] == 100
    assert notification["Subscribers"] == [
        {"SubscriptionType": "SNS", "Address": {"Ref": synth.logical_id(synth.stack.alert_topic)}}
    ]


def test_budget_limit_comes_from_the_setting_and_must_be_positive() -> None:
    custom = synthesise(budget_limit_usd=250)

    assert custom.json["Resources"]["MonthlyBudget"]["Properties"]["Budget"]["BudgetLimit"] == {
        "Amount": 250,
        "Unit": "USD",
    }
    with pytest.raises(ModelImportStackError, match="budget_limit_usd"):
        make_stack(budget_limit_usd=0)


@pytest.mark.parametrize("field", ["team_address", "sender_address"])
def test_invalid_mail_address_fails_the_build_without_echoing_it(field: str) -> None:
    with pytest.raises(ModelImportStackError, match=field) as raised:
        make_stack(**{field: "not an address"})

    assert "not an address" not in str(raised.value)
    assert ACCOUNT not in str(raised.value)
