"""Highlight evaluation gate (runs in CI after scripts/build_eval_set.py + scripts/eval_highlights.py).

The new detector must be clearly better than the old one on the labelled set: top-10 precision of at
least 70 % and at least 20 points above v0.3. Skipped when the report isn't there (local runs)."""
import json
import os
from pathlib import Path

import pytest

REPORT = os.environ.get("CLIPFARM_EVAL_REPORT")


@pytest.mark.skipif(not REPORT or not Path(REPORT).exists(), reason="evaluation report not built (CI only)")
def test_new_highlights_clearly_better():
    r = json.loads(Path(REPORT).read_text())
    assert r["after"] >= 0.7, r["after"]
    assert r["after"] >= r["before"] + 0.2, (r["before"], r["after"])
