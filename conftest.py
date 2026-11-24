"""Test setup shared by every package.

Recorded CTA responses never enter the repository. A test that needs them asks
for the cta_fixtures fixture, which skips the test when no fixture folder is
present, so a checkout without them still passes and reports the skip.
"""

import os
from pathlib import Path

import pytest

FIXTURES_VARIABLE = "LAMP_FIXTURES_DIR"
DEFAULT_FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def cta_fixtures() -> Path:
    """Return the folder of recorded CTA responses, or skip the test.

    The folder is LAMP_FIXTURES_DIR when it is set and not empty, and fixtures/
    at the repository root otherwise.
    """
    folder = _fixtures_dir()
    if not folder.is_dir():
        pytest.skip(f"no CTA fixtures, set {FIXTURES_VARIABLE} or unpack them into fixtures/")
    return folder


def _fixtures_dir() -> Path:
    configured = os.environ.get(FIXTURES_VARIABLE, "")
    if configured:
        return Path(configured)
    return DEFAULT_FIXTURES_DIR
