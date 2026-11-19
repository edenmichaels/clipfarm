"""Test setup. Runs before any clipfarm import.

Keys written by tests go to a throw-away Credential Manager service name (on Windows CI this is the
real Credential Manager), never to the user's "ClipFarm" entries, and are wiped afterwards.
"""
import os
import uuid

os.environ.setdefault("CLIPFARM_KEYRING_SERVICE", f"ClipFarmTest-{uuid.uuid4().hex[:8]}")

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _wipe_test_keys():
    yield
    try:
        from clipfarm import secrets
        secrets.wipe_all()
    except Exception:
        pass
