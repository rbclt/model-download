"""Settings read from the environment (NFR3.1, NFR3.2).

The worker needs no password, key, or token. Only bucket names, the queue URL, and two
mail addresses come from the environment. Nothing here reads a Hugging Face token: any
``HF_TOKEN`` style variable is ignored on purpose.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

ENV_HOLDING_BUCKET = "MODEL_IMPORT_HOLDING_BUCKET"
ENV_FINAL_BUCKET = "MODEL_IMPORT_FINAL_BUCKET"
ENV_QUEUE_URL = "MODEL_IMPORT_QUEUE_URL"
ENV_SENDER_ADDRESS = "MODEL_IMPORT_SENDER_ADDRESS"
ENV_TEAM_ADDRESS = "MODEL_IMPORT_TEAM_ADDRESS"
ENV_CONFIGURATION_SET = "MODEL_IMPORT_SES_CONFIGURATION_SET"  # optional (NFR3.9)
ENV_REGION = "AWS_REGION"
DEFAULT_REGION = "us-east-1"
_CONFIGURATION_SET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_REQUIRED = (
    ENV_HOLDING_BUCKET,
    ENV_FINAL_BUCKET,
    ENV_QUEUE_URL,
    ENV_SENDER_ADDRESS,
    ENV_TEAM_ADDRESS,
)

# One local@domain address, no whitespace, comma, semicolon, or control character (NFR3.14).
_ADDRESS_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
MAX_ADDRESS_LENGTH = 254


class SettingsError(ValueError):
    """The environment is missing a setting or holds one that is not valid."""


def is_valid_address(value: str) -> bool:
    """Return True for a single header-safe email address of at most 254 characters."""
    return len(value) <= MAX_ADDRESS_LENGTH and _ADDRESS_PATTERN.fullmatch(value) is not None


@dataclass(frozen=True)
class Settings:
    """Everything the worker reads from its environment."""

    holding_bucket: str
    final_bucket: str
    queue_url: str
    sender_address: str
    team_address: str
    region: str = DEFAULT_REGION
    configuration_set: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build the settings from ``env`` (the process environment when omitted).

        Raises ``SettingsError`` naming every missing or invalid setting. The message never
        shows a value, so a mistyped secret cannot reach a log.
        """
        source = os.environ if env is None else env
        missing = [name for name in _REQUIRED if not source.get(name, "").strip()]
        if missing:
            raise SettingsError("missing settings: " + ", ".join(missing))

        invalid = [
            name
            for name in (ENV_SENDER_ADDRESS, ENV_TEAM_ADDRESS)
            if not is_valid_address(source[name].strip())
        ]
        if invalid:
            raise SettingsError("invalid email address in: " + ", ".join(invalid))

        configuration_set = source.get(ENV_CONFIGURATION_SET, "").strip() or None
        if configuration_set is not None and not _CONFIGURATION_SET_PATTERN.fullmatch(
            configuration_set
        ):
            raise SettingsError("invalid SES configuration set name in: " + ENV_CONFIGURATION_SET)

        return cls(
            holding_bucket=source[ENV_HOLDING_BUCKET].strip(),
            final_bucket=source[ENV_FINAL_BUCKET].strip(),
            queue_url=source[ENV_QUEUE_URL].strip(),
            sender_address=source[ENV_SENDER_ADDRESS].strip(),
            team_address=source[ENV_TEAM_ADDRESS].strip(),
            region=source.get(ENV_REGION, "").strip() or DEFAULT_REGION,
            configuration_set=configuration_set,
        )
