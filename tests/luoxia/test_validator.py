from __future__ import annotations

import copy

import pytest

from src.luoxia.paths import TIMELINE_EXAMPLE_PATH
from src.luoxia.timeline.io import load_timeline
from src.luoxia.timeline.validator import (
    TimelineValidationError,
    mutate_for_invariant_violation,
    validate_timeline,
)


@pytest.fixture
def example():
    return load_timeline(TIMELINE_EXAMPLE_PATH)


def test_example_passes(example):
    issues = validate_timeline(example, raise_on_error=False)
    assert issues == []


@pytest.mark.parametrize("invariant", list(range(1, 17)))
def test_each_invariant_fails(example, invariant):
    bad = mutate_for_invariant_violation(example, invariant)
    with pytest.raises(TimelineValidationError) as exc:
        validate_timeline(bad)
    assert any(i.invariant == invariant for i in exc.value.issues), exc.value.issues
    # Error message should mention shot_id when applicable
    text = str(exc.value)
    if invariant not in {14}:
        assert "shot=" in text or "shots[0]" in text or "ep01_" in text
