from __future__ import annotations

import copy

import pytest

from src.luoxia.beats.hashing import assert_beats_hash, compute_beats_hash
from src.luoxia.beats.io import load_beats
from src.luoxia.beats.validator import (
    BeatsValidationError,
    compute_selection_stats,
    mutate_for_invariant_violation,
    validate_beats,
)
from src.luoxia.paths import BEATS_EXAMPLE_PATH


@pytest.fixture
def example():
    return load_beats(BEATS_EXAMPLE_PATH)


def test_example_passes(example):
    assert validate_beats(example, raise_on_error=False) == []


@pytest.mark.parametrize("invariant", list(range(1, 21)))
def test_each_invariant_fails(example, invariant):
    bad = mutate_for_invariant_violation(example, invariant)
    with pytest.raises(BeatsValidationError) as exc:
        validate_beats(bad)
    assert any(i.invariant == invariant for i in exc.value.issues), [str(i) for i in exc.value.issues]


def test_compression_ratio_is_recomputed_not_trusted(example):
    example["selection"]["compression_ratio"] = 0.001
    issues = validate_beats(example, raise_on_error=False)
    assert any(i.code == "selection_stats_mismatch" for i in issues)


def test_dropping_a_setup_breaks_its_payoff(example):
    """The whole point of depends_on: you cannot cut the humiliation and keep the face slap."""
    setup = next(b for b in example["beats"] if b["beat_id"] == "b003")
    setup["decision"] = "drop"
    setup["drop_reason"] = "flat"
    setup["lines"] = []
    setup["script_char_count"] = 0
    example["episodes"][0]["beat_ids"].remove("b003")

    issues = validate_beats(example, raise_on_error=False)
    assert any(i.invariant == 9 and i.beat_id == "b005" for i in issues), [str(i) for i in issues]


def test_merged_content_must_survive(example):
    filler = next(b for b in example["beats"] if b["beat_id"] == "b002")
    filler["drop_reason"] = "merged"
    filler["merged_into"] = "b004"  # b004 is itself dropped
    issues = validate_beats(example, raise_on_error=False)
    assert any(i.code == "merge_target_dropped" for i in issues)

    filler["merged_into"] = "b001"
    assert validate_beats(example, raise_on_error=False) == []


def test_draft_phase_skips_selection_checks(example):
    draft = copy.deepcopy(example)
    draft["phase"] = "draft"
    draft.pop("selected_at")
    draft.pop("beats_hash")
    draft.pop("episodes")
    draft.pop("selection")
    for beat in draft["beats"]:
        beat.pop("beat_type", None)
        beat.pop("intensity", None)
        beat.pop("decision", None)
        beat.pop("drop_reason", None)
    assert validate_beats(draft, raise_on_error=False) == []


def test_scored_phase_requires_scores(example):
    scored = copy.deepcopy(example)
    scored["phase"] = "scored"
    scored["beats"][0].pop("intensity")
    issues = validate_beats(scored, raise_on_error=False)
    assert any(i.invariant == 5 for i in issues)


def test_hash_detects_silent_edit(example):
    assert_beats_hash(example)
    before = compute_beats_hash(example)
    example["beats"][0]["lines"][0]["text"] = "改了一个字。"
    assert compute_beats_hash(example) != before
    with pytest.raises(ValueError, match="beats_hash mismatch"):
        assert_beats_hash(example)


def test_stats_match_hand_count(example):
    stats = compute_selection_stats(example)
    assert stats["total_script_chars"] == 96
    assert stats["kept"] == 3 and stats["compressed"] == 1 and stats["dropped"] == 2
    assert stats["compression_ratio"] == pytest.approx(0.04)
