from __future__ import annotations

import copy

import pytest

from src.luoxia.beats.io import load_beats
from src.luoxia.beats.selector import (
    SelectionError,
    apply_thresholds,
    plan_episodes,
    repair_dependencies,
    select_beats,
)
from src.luoxia.beats.validator import validate_beats
from src.luoxia.paths import BEATS_EXAMPLE_PATH


@pytest.fixture
def scored():
    """The example rewound to the scored phase, before any decision was made."""
    doc = load_beats(BEATS_EXAMPLE_PATH)
    doc["phase"] = "scored"
    doc.pop("selected_at", None)
    doc.pop("beats_hash", None)
    doc.pop("selection", None)
    doc.pop("episodes", None)
    for beat in doc["beats"]:
        beat.pop("decision", None)
        beat.pop("drop_reason", None)
    return doc


def test_thresholds_reproduce_the_example_decisions(scored):
    apply_thresholds(scored)
    got = {b["beat_id"]: b["decision"] for b in scored["beats"]}
    assert got == {
        "b001": "keep",
        "b002": "drop",
        "b003": "compress",
        "b004": "drop",
        "b005": "keep",
        "b006": "keep",
    }


def test_filler_never_survives_intact(scored):
    scored["beats"][1]["intensity"] = 9.9  # a filler段落 scored absurdly high
    apply_thresholds(scored)
    assert scored["beats"][1]["decision"] == "compress"


def test_locked_decision_wins_over_score(scored):
    b002 = scored["beats"][1]
    b002["decision"] = "compress"
    b002["decision_locked"] = True
    apply_thresholds(scored)
    assert b002["decision"] == "compress"


def test_dropped_setup_is_rescued_for_its_payoff(scored):
    # Force the setup below the drop line; the face slap still depends on it.
    setup = next(b for b in scored["beats"] if b["beat_id"] == "b003")
    setup["intensity"] = 0.5
    apply_thresholds(scored)
    assert setup["decision"] == "drop"

    rescued = repair_dependencies(scored)
    assert rescued == ["b003"]
    assert setup["decision"] == "compress"
    assert setup["drop_reason"] is None


def test_rescue_refuses_to_override_a_human_lock(scored):
    setup = next(b for b in scored["beats"] if b["beat_id"] == "b003")
    setup["decision"] = "drop"
    setup["drop_reason"] = "flat"
    setup["decision_locked"] = True
    apply_thresholds(scored)
    with pytest.raises(SelectionError, match="locked as drop"):
        repair_dependencies(scored)


def test_episode_must_end_on_a_cliffhanger(scored):
    apply_thresholds(scored)
    next(b for b in scored["beats"] if b["beat_id"] == "b006")["cliffhanger"] = None
    with pytest.raises(SelectionError, match="no cliffhanger"):
        plan_episodes(scored)


def test_full_pass_matches_the_committed_example(scored):
    reference = load_beats(BEATS_EXAMPLE_PATH)
    select_beats(scored, now=None)
    validate_beats(scored)

    assert scored["phase"] == "selected"
    assert scored["beats_hash"] == reference["beats_hash"]
    assert scored["selection"] == reference["selection"]
    assert [e["beat_ids"] for e in scored["episodes"]] == [e["beat_ids"] for e in reference["episodes"]]


def test_delivered_selection_cannot_be_rerun(scored):
    select_beats(scored)
    scored["phase"] = "delivered"
    with pytest.raises(SelectionError, match="already delivered"):
        select_beats(scored)


def test_long_source_splits_into_multiple_episodes(scored):
    """Two hook-bearing beats plus enough material should produce two episodes."""
    doc = copy.deepcopy(scored)
    doc["global"]["target_episode_duration_s"] = 15
    b003 = next(b for b in doc["beats"] if b["beat_id"] == "b003")
    b003["intensity"] = 7.5
    b003["beat_type"] = "reversal"
    b003["cliffhanger"] = {"tier": "tier_2", "question": "她凭什么这么笃定？"}
    b003["lines"][0]["text"] = "三年前你把我赶出林家，说我这辈子都是废物。现在我把话还给你。"

    select_beats(doc)
    validate_beats(doc)
    assert len(doc["episodes"]) == 2
    assert doc["episodes"][0]["beat_ids"][-1] == "b003"
