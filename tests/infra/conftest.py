"""Fixtures for the CDK template tests (one synthesis shared by the whole session)."""

import pytest
from tests.infra.synth_helpers import Synthesised, synthesise


@pytest.fixture(scope="session")
def synth() -> Synthesised:
    return synthesise()
