"""Helpers for the CDK template tests.

CDK synthesis needs no AWS account and no Docker. The worker image is a stand-in registry
reference (``ContainerImage.from_registry``), so no folder is hashed and nothing is built; the
real build from the ``Dockerfile`` (``ContainerImage.from_asset``) is used only by ``app.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aws_cdk as cdk
from aws_cdk import aws_ecs as ecs
from aws_cdk.assertions import Template
from infra.model_import_stack import ModelImportStack

ACCOUNT = "123456789012"
BREAK_GLASS_ARN = f"arn:aws:iam::{ACCOUNT}:role/OwnerAdmin"
SMOKE_ARN = f"arn:aws:iam::{ACCOUNT}:role/PipelineSmokeCheck"
TEAM = "team@example.com"
SENDER = "imports@example.com"


def make_stack(app: cdk.App | None = None, **overrides: Any) -> ModelImportStack:
    settings: dict[str, Any] = {
        "team_address": TEAM,
        "sender_address": SENDER,
        "break_glass_role_arns": [BREAK_GLASS_ARN],
        "smoke_check_role_arn": SMOKE_ARN,
        "image": ecs.ContainerImage.from_registry("example.invalid/model-import-worker:test"),
        "env": cdk.Environment(account=ACCOUNT, region="us-east-1"),
    }
    settings.update(overrides)
    return ModelImportStack(app or cdk.App(), "staging", **settings)


@dataclass(frozen=True)
class Synthesised:
    stack: ModelImportStack
    template: Template
    json: dict[str, Any]

    def resources(self, resource_type: str) -> dict[str, dict[str, Any]]:
        return {
            logical_id: resource
            for logical_id, resource in self.json["Resources"].items()
            if resource["Type"] == resource_type
        }

    def logical_id(self, construct: Any) -> str:
        return self.stack.get_logical_id(construct.node.default_child)

    def resource(self, construct: Any) -> dict[str, Any]:
        return self.json["Resources"][self.logical_id(construct)]

    def properties(self, construct: Any) -> dict[str, Any]:
        """The template properties of a construct's own CloudFormation resource."""
        return self.resource(construct)["Properties"]


def synthesise(**overrides: Any) -> Synthesised:
    stack = make_stack(**overrides)
    template = Template.from_stack(stack)
    return Synthesised(stack=stack, template=template, json=template.to_json())


def statements(policy_document: dict[str, Any]) -> list[dict[str, Any]]:
    """The statements of a policy document, each with ``Action`` and ``Resource`` as lists."""
    found = []
    for statement in policy_document["Statement"]:
        normal = dict(statement)
        for key in ("Action", "Resource"):
            if key in normal and not isinstance(normal[key], list):
                normal[key] = [normal[key]]
        found.append(normal)
    return found
