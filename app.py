"""CDK entry point: ``cdk synth``, ``cdk deploy``.

Settings come from CDK context (``-c teamAddress=...``) or from environment variables; see
``infra/config.py``. The stack is named from the ``stackName`` setting (default ``staging``).
"""

from __future__ import annotations

import os
import sys

import aws_cdk as cdk
from infra.config import ConfigError, load_config
from infra.model_import_stack import ModelImportStack


def build_app() -> cdk.App:
    app = cdk.App()
    config = load_config(app.node.try_get_context, os.environ)
    ModelImportStack(
        app,
        config.stack_name,
        team_address=config.team_address,
        sender_address=config.sender_address,
        break_glass_role_arns=config.break_glass_role_arns,
        budget_limit_usd=config.budget_limit_usd,
        smoke_check_role_arn=config.smoke_check_role_arn,
        environment_name=config.stack_name,
        env=cdk.Environment(
            account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
            region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
        ),
    )
    return app


if __name__ == "__main__":
    try:
        build_app().synth()
    except ConfigError as error:
        sys.stderr.write(f"{error}\n")
        sys.exit(2)
