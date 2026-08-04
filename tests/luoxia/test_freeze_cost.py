from __future__ import annotations

import copy

import pytest

from src.luoxia.paths import TIMELINE_EXAMPLE_PATH
from src.luoxia.timeline.freeze import BudgetExceededError, freeze_timeline, unfreeze_timeline
from src.luoxia.timeline.hashing import assert_timeline_hash, compute_timeline_hash
from src.luoxia.timeline.io import load_timeline
from src.luoxia.timeline.validator import TimelineValidationError, validate_timeline


def test_freeze_budget_gate_and_hash():
    tl = load_timeline(TIMELINE_EXAMPLE_PATH)
    unfreeze_timeline(tl, reason="test")
    # Example is already solved; mark audio_locked
    tl["phase"] = "audio_locked"
    tl["cost"]["budget_ceiling_usd"] = 0.01
    with pytest.raises(BudgetExceededError) as exc:
        freeze_timeline(tl)
    assert "exceeds" in str(exc.value)
    assert "TOTAL" in exc.value.detail

    tl["cost"]["budget_ceiling_usd"] = 50.0
    freeze_timeline(tl)
    assert tl["phase"] == "frozen"
    assert tl["timeline_hash"] == compute_timeline_hash(tl)
    validate_timeline(tl)

    # Tamper timing after freeze
    tl["shots"][0]["timing"]["target_duration_s"] += 0.1
    with pytest.raises(ValueError):
        assert_timeline_hash(tl)
