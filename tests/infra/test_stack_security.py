"""Template tests for the roles and buckets (NFR3.4 to NFR3.7, NFR3.15, NFR4.9)."""

from __future__ import annotations

import pytest
from infra.model_import_stack import ModelImportStackError
from tests.infra.synth_helpers import (
    ACCOUNT,
    BREAK_GLASS_ARN,
    SMOKE_ARN,
    Synthesised,
    make_stack,
    statements,
)

EXPECTED_TASK_ROLE_ACTIONS = {
    "s3:GetObject",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:AbortMultipartUpload",
    "s3:ListMultipartUploadParts",
    "s3:ListBucket",
    "s3:ListBucketMultipartUploads",
    "sqs:ReceiveMessage",
    "sqs:DeleteMessage",
    "sqs:ChangeMessageVisibility",
    "ecs:UpdateTaskProtection",
    "ses:SendEmail",
}


def _task_role_statements(synth: Synthesised) -> list[dict]:
    properties = synth.properties(synth.stack.task_role)
    (policy,) = properties["Policies"]
    return statements(policy["PolicyDocument"])


def test_task_role_has_exactly_the_closed_action_list_nfr3_4(synth: Synthesised) -> None:
    actions = {action for s in _task_role_statements(synth) for action in s["Action"]}

    assert actions == EXPECTED_TASK_ROLE_ACTIONS


def test_task_role_has_no_wildcard_action_and_no_resource_star_nfr3_4(synth: Synthesised) -> None:
    for statement in _task_role_statements(synth):
        assert statement["Effect"] == "Allow"
        assert not any(action == "*" or action.endswith(":*") for action in statement["Action"])
        assert "*" not in statement["Resource"], statement["Sid"]


def test_task_role_gets_nothing_from_another_policy_and_has_no_log_permission(
    synth: Synthesised,
) -> None:
    """Logs go through the execution role; no attached policy widens the task role."""
    task_role_id = synth.logical_id(synth.stack.task_role)
    assert "ManagedPolicyArns" not in synth.properties(synth.stack.task_role)
    for policy in synth.resources("AWS::IAM::Policy").values():
        assert {"Ref": task_role_id} not in policy["Properties"]["Roles"]
    actions = {action for s in _task_role_statements(synth) for action in s["Action"]}
    assert not any(action.startswith(("logs:", "cloudwatch:")) for action in actions)


def test_task_role_ses_send_is_limited_by_identity_configuration_set_and_from_address(
    synth: Synthesised,
) -> None:
    (send,) = [s for s in _task_role_statements(synth) if s["Action"] == ["ses:SendEmail"]]

    assert send["Condition"] == {"StringEquals": {"ses:FromAddress": "imports@example.com"}}
    arns = [
        "".join(part if isinstance(part, str) else "<partition>" for part in item["Fn::Join"][1])
        for item in send["Resource"]
    ]
    assert any(
        arn.endswith(":ses:us-east-1:123456789012:identity/imports@example.com") for arn in arns
    )
    assert any(arn.endswith(":configuration-set/model-import") for arn in arns)


def test_task_role_lists_whole_buckets_but_touches_objects_only_in_its_prefixes(
    synth: Synthesised,
) -> None:
    by_sid = {s["Sid"]: s for s in _task_role_statements(synth)}

    assert len(by_sid["ListWholeBuckets"]["Resource"]) == 2
    object_paths = [item["Fn::Join"][1][-1] for item in by_sid["ObjectsInOwnPrefixes"]["Resource"]]
    assert object_paths == ["/holding/*", "/state/*", "/models/*"]


@pytest.mark.parametrize("bucket_name", ["holding_bucket", "final_bucket"])
def test_buckets_are_encrypted_private_unversioned_and_kept_nfr3_5_nfr3_6_nfr4_9(
    synth: Synthesised, bucket_name: str
) -> None:
    bucket = getattr(synth.stack, bucket_name)
    resource, properties = synth.resource(bucket), synth.properties(bucket)

    assert properties["BucketEncryption"]["ServerSideEncryptionConfiguration"] == [
        {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
    ]
    assert properties["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }
    assert "VersioningConfiguration" not in properties
    assert resource["DeletionPolicy"] == "Retain"
    assert resource["UpdateReplacePolicy"] == "Retain"
    assert not synth.resources("Custom::S3AutoDeleteObjects")


@pytest.mark.parametrize("bucket_name", ["holding_bucket", "final_bucket"])
def test_bucket_policy_denies_plain_http_and_tls_below_1_2_nfr3_5_nfr3_15(
    synth: Synthesised, bucket_name: str
) -> None:
    bucket = getattr(synth.stack, bucket_name)
    policy = synth.json["Resources"][synth.logical_id(bucket.policy)]["Properties"][
        "PolicyDocument"
    ]
    conditions = [s["Condition"] for s in statements(policy) if s["Effect"] == "Deny"]

    assert {"Bool": {"aws:SecureTransport": "false"}} in conditions
    assert {"NumericLessThan": {"s3:TlsVersion": 1.2}} in conditions


@pytest.mark.parametrize("bucket_name", ["holding_bucket", "final_bucket"])
def test_bucket_denies_object_data_to_everyone_except_the_named_roles_nfr3_7(
    synth: Synthesised, bucket_name: str
) -> None:
    bucket = getattr(synth.stack, bucket_name)
    policy = synth.json["Resources"][synth.logical_id(bucket.policy)]["Properties"][
        "PolicyDocument"
    ]
    (deny,) = [s for s in statements(policy) if s.get("Sid") == "DenyObjectDataToOthers"]
    allowed = deny["Condition"]["ArnNotLike"]["aws:PrincipalArn"]

    assert deny["Effect"] == "Deny"
    assert set(deny["Action"]) == {"s3:GetObject", "s3:PutObject", "s3:DeleteObject"}
    assert {"Fn::GetAtt": [synth.logical_id(synth.stack.task_role), "Arn"]} in allowed
    assert BREAK_GLASS_ARN in allowed
    assert SMOKE_ARN in allowed
    cdk_roles = [item for item in allowed if isinstance(item, dict) and "Fn::Join" in item]
    assert len(cdk_roles) == 2
    assert any("cdk-hnb659fds-deploy-role-" in str(item) for item in cdk_roles)
    assert any("cdk-hnb659fds-cfn-exec-role-" in str(item) for item in cdk_roles)
    assert len(allowed) == 5  # task role, 2 CDK roles, break-glass role, smoke-check role


def test_holding_bucket_lifecycle_rules_nfr3_5_nfr4_9(synth: Synthesised) -> None:
    rules = {
        rule["Id"]: rule
        for rule in synth.properties(synth.stack.holding_bucket)["LifecycleConfiguration"]["Rules"]
    }

    assert rules["expire-holding"]["Prefix"] == "holding/"
    assert rules["expire-holding"]["ExpirationInDays"] == 2
    assert rules["expire-state"]["Prefix"] == "state/"
    assert rules["expire-state"]["ExpirationInDays"] == 30
    assert rules["abort-incomplete-multipart-uploads"]["AbortIncompleteMultipartUpload"] == {
        "DaysAfterInitiation": 1
    }
    assert all(rule["Status"] == "Enabled" for rule in rules.values())


def test_final_bucket_never_expires_models_but_aborts_unfinished_uploads(
    synth: Synthesised,
) -> None:
    (rule,) = synth.properties(synth.stack.final_bucket)["LifecycleConfiguration"]["Rules"]

    assert rule["AbortIncompleteMultipartUpload"] == {"DaysAfterInitiation": 1}
    assert "ExpirationInDays" not in rule


def test_empty_break_glass_roles_fail_the_build_nfr3_7() -> None:
    with pytest.raises(ModelImportStackError, match="break_glass_role_arns"):
        make_stack(break_glass_role_arns=[])
    with pytest.raises(ModelImportStackError, match="break_glass_role_arns"):
        make_stack(break_glass_role_arns=["  ", ""])


def test_break_glass_value_that_is_not_a_role_arn_fails_the_build() -> None:
    with pytest.raises(ModelImportStackError, match="not a role ARN"):
        make_stack(break_glass_role_arns=[f"arn:aws:iam::{ACCOUNT}:user/someone"])
