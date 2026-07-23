"""Shared test setup.

`helpers/__init__.py` loads `.env`, which means a developer's real credentials are present
in `os.environ` during the test run. Without scrubbing them, a test that means to exercise
the "no credentials configured" path instead falls back to the real ones and makes a live
authenticated request. Clearing them for every test keeps the suite hermetic and offline.
"""

import pytest

CREDENTIAL_VARS = (
    "ESPN_LEAGUE_ID",
    "ESPN_S2",
    "ESPN_SWID",
    "EMAIL_SENDER",
    "EMAIL_PASSWORD",
    "EMAIL_RECIPIENT",
    "FORM_TOKEN",
    "BASE_URL",
    "OWNERSHIP_THRESHOLD",
)


@pytest.fixture(autouse=True)
def scrub_credentials(monkeypatch):
    for name in CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)
