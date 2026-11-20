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


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """A new, migrated database with the seed data (campaigns, accounts). Offline LLM."""
    from clipfarm import db
    monkeypatch.setenv("CLIPFARM_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "t.db")
    db.set_setting("media_dir", str(tmp_path / "media"))
    db.set_setting("transcription", {"enabled": False})
    db.set_setting("llm", {"provider": "ollama", "base_url": "http://127.0.0.1:9"})
    return tmp_path
