from __future__ import annotations

import copy

import pytest

from src.luoxia.beats.io import load_beats
from src.luoxia.beats.selector import estimate_beat_duration_s, select_beats
from src.luoxia.beats.to_timeline import build_timeline_draft
from src.luoxia.beats.validator import (
    BeatsValidationError,
    coverage_budget,
    coverage_settings,
    coverage_visuals,
    shot_count,
    validate_beats,
)
from src.luoxia.paths import BEATS_EXAMPLE_PATH
from src.luoxia.timeline.solver import solve_timeline
from src.luoxia.timeline.validator import validate_timeline


@pytest.fixture
def beats():
    doc = load_beats(BEATS_EXAMPLE_PATH)
    validate_beats(doc)
    return doc


def _fake_tts(shot, speed):
    text = (shot.get("dialogue") or {}).get("text") or ""
    measured = max(0.4, len(text) * 0.22 / max(speed, 1e-6))
    return measured, f"audio/{shot['shot_id']}.wav", f"sha256:{shot['shot_id']}:{speed:.3f}"


def _beat(beats, beat_id):
    return next(b for b in beats["beats"] if b["beat_id"] == beat_id)


# A face-slap beat filmed the way a human would cover it.
COVERAGE = [
    {
        "kind": "establishing",
        "after_line": 0,
        "shot_size": "wide",
        "prompt": "拍卖厅全景，众人回头",
        "action_duration_s": 3,
    },
    {
        "kind": "reaction",
        "after_line": 1,
        "subject": "lin_wan",
        "shot_size": "close_up",
        "prompt": "林晚被点名，指尖一顿",
        "action_duration_s": 1.5,
    },
    {
        "kind": "insert",
        "after_line": 1,
        "shot_size": "insert",
        "prompt": "攥到发白的指节",
        "action_duration_s": 1.5,
    },
]


# --- ordering: the shot list must film in reading order ---------------------------


def test_coverage_interleaves_by_after_line(beats):
    """0 goes before every line; k goes after line k. That is the whole point."""
    target = _beat(beats, "b001")
    target.pop("visual", None)
    target["visuals"] = copy.deepcopy(COVERAGE)

    draft = build_timeline_draft(beats, "ep01")
    ids = [s["shot_id"] for s in draft["shots"] if s["shot_id"].startswith("ep01_b001")]
    assert ids == [
        "ep01_b001_v01",  # establishing, before any line
        "ep01_b001_l01",
        "ep01_b001_v11",  # reaction, after line 1
        "ep01_b001_v12",  # insert, after line 1
        "ep01_b001_l02",
    ]


def test_kinds_become_readable_shot_types(beats):
    target = _beat(beats, "b001")
    target.pop("visual", None)
    target["visuals"] = copy.deepcopy(COVERAGE)

    draft = build_timeline_draft(beats, "ep01")
    by_id = {s["shot_id"]: s for s in draft["shots"]}
    assert by_id["ep01_b001_v11"]["type"] == "reaction"
    assert by_id["ep01_b001_v12"]["type"] == "insert"
    assert by_id["ep01_b001_l01"]["type"] == "dialogue"


def test_reaction_shot_frames_only_its_subject(beats):
    """A reaction shot showing both speakers is not a reaction shot."""
    target = _beat(beats, "b001")
    target.pop("visual", None)
    target["visuals"] = copy.deepcopy(COVERAGE)

    draft = build_timeline_draft(beats, "ep01")
    by_id = {s["shot_id"]: s for s in draft["shots"]}
    assert by_id["ep01_b001_v11"]["characters"] == ["lin_wan"]
    assert by_id["ep01_b001_v11"]["shot_size"] == "close_up"
    # The establishing shot still carries everyone who speaks in the beat.
    assert len(by_id["ep01_b001_v01"]["characters"]) >= 1


def test_reaction_default_duration_is_short(beats):
    """Left to default_action_duration_s a reaction shot would sit on screen for 4s."""
    target = _beat(beats, "b001")
    target.pop("visual", None)
    target["visuals"] = [{"kind": "reaction", "after_line": 1, "subject": "lin_wan"}]

    draft = build_timeline_draft(beats, "ep01")
    reaction = next(s for s in draft["shots"] if s["shot_id"] == "ep01_b001_v11")
    assert reaction["timing"]["target_duration_s"] == 1.5
    assert draft["global"]["default_action_duration_s"] == 4


def test_coverage_survives_solve_and_validate(beats):
    target = _beat(beats, "b001")
    target.pop("visual", None)
    target["visuals"] = copy.deepcopy(COVERAGE)

    draft = build_timeline_draft(beats, "ep01")
    solve_timeline(draft, synthesize=_fake_tts)
    validate_timeline(draft)


def test_silent_shots_default_to_hard_cuts(beats):
    """Reverse-angle coverage must not dissolve; a dissolve reads as a time jump."""
    target = _beat(beats, "b001")
    target.pop("visual", None)
    target["visuals"] = copy.deepcopy(COVERAGE)

    draft = build_timeline_draft(beats, "ep01")
    assert all(s["transition"]["kind"] == "cut" for s in draft["shots"])


# --- the deprecated single visual still works ------------------------------------


def test_legacy_visual_reads_as_one_establishing_shot(beats):
    """1.0.0 files only had a single `visual`; it still films, as one establishing shot."""
    legacy = _beat(beats, "b001")
    legacy.pop("visuals")
    legacy["visual"] = {"shot_size": "wide", "prompt": "拍卖厅全景", "action_duration_s": 3}
    validate_beats(beats)

    visuals = coverage_visuals(legacy)
    assert [v["kind"] for v in visuals] == ["establishing"]
    assert visuals[0]["after_line"] == 0

    draft = build_timeline_draft(beats, "ep01")
    ids = [s["shot_id"] for s in draft["shots"] if s["shot_id"].startswith("ep01_b001")]
    assert ids == ["ep01_b001_v01", "ep01_b001_l01", "ep01_b001_l02"]


def test_visual_and_visuals_together_is_rejected(beats):
    target = _beat(beats, "b001")
    target["visual"] = {"shot_size": "wide", "prompt": "拍卖厅全景"}
    with pytest.raises(BeatsValidationError) as exc:
        validate_beats(beats)
    assert any(i.code == "visual_and_visuals" for i in exc.value.issues)


# --- structural checks ------------------------------------------------------------


def test_after_line_beyond_line_count_is_rejected(beats):
    target = _beat(beats, "b001")
    target.pop("visual", None)
    target["visuals"] = [{"kind": "insert", "after_line": 99}]
    with pytest.raises(BeatsValidationError) as exc:
        validate_beats(beats)
    assert any(i.code == "after_line_out_of_range" for i in exc.value.issues)


def test_backwards_coverage_is_rejected(beats):
    target = _beat(beats, "b001")
    target.pop("visual", None)
    target["visuals"] = [
        {"kind": "insert", "after_line": 2},
        {"kind": "insert", "after_line": 1},
    ]
    with pytest.raises(BeatsValidationError) as exc:
        validate_beats(beats)
    assert any(i.code == "coverage_out_of_order" for i in exc.value.issues)


def test_reaction_without_subject_is_rejected(beats):
    target = _beat(beats, "b001")
    target.pop("visual", None)
    target["visuals"] = [{"kind": "reaction", "after_line": 1}]
    with pytest.raises(BeatsValidationError) as exc:
        validate_beats(beats)
    assert any(i.code == "reaction_without_subject" for i in exc.value.issues)


def test_unknown_subject_is_rejected(beats):
    target = _beat(beats, "b001")
    target.pop("visual", None)
    target["visuals"] = [{"kind": "reaction", "after_line": 1, "subject": "ghost"}]
    with pytest.raises(BeatsValidationError) as exc:
        validate_beats(beats)
    assert any(i.code == "unknown_character" for i in exc.value.issues)


def test_bridge_rejects_unknown_subject(beats):
    from src.luoxia.beats.to_timeline import BridgeError

    target = _beat(beats, "b001")
    target.pop("visual", None)
    target["visuals"] = [{"kind": "insert", "after_line": 1, "subject": "ghost"}]
    with pytest.raises(BridgeError, match="not in cast"):
        build_timeline_draft(beats, "ep01")


# --- budget: coverage costs money -------------------------------------------------


def test_budget_follows_intensity(beats):
    coverage = coverage_settings(beats)
    g = beats["global"]
    peak = {"intensity": 8.0}
    mid = {"intensity": 5.0}
    low = {"intensity": 1.0}
    assert coverage_budget(peak, coverage, g) == 6
    assert coverage_budget(mid, coverage, g) == 3
    assert coverage_budget(low, coverage, g) == 1


def test_shot_count_includes_lines_and_visuals(beats):
    target = _beat(beats, "b001")
    target.pop("visual", None)
    target["visuals"] = copy.deepcopy(COVERAGE)
    assert shot_count(target) == len(target["lines"]) + 3


def test_over_budget_coverage_is_trimmed_and_logged(beats):
    """A mid-intensity beat cannot afford six shots; the surplus is cut, not silently kept."""
    target = _beat(beats, "b003")
    target.pop("visual", None)
    target["intensity"] = 5.0
    target["decision_locked"] = True
    target["decision"] = "compress"
    target["visuals"] = copy.deepcopy(COVERAGE) + [
        {"kind": "insert", "after_line": 1, "prompt": "多余的插入镜头"},
    ]

    select_beats(beats)

    # One line + a 3-shot budget leaves room for two silent shots.
    assert shot_count(target) == 3
    # Both inserts are sacrificed before the reaction shot, which is the payoff.
    assert [v["kind"] for v in target["visuals"]] == ["establishing", "reaction"]
    trims = [r for r in beats["repairs"] if r["code"] == "coverage_trimmed"]
    assert trims and trims[0]["beat_id"] == "b003"
    assert beats["quality"]["repair_count"] >= 1


def test_peak_beat_keeps_full_coverage(beats):
    target = _beat(beats, "b005")
    target.pop("visual", None)
    target["visuals"] = copy.deepcopy(COVERAGE)
    before = len(target["visuals"])

    select_beats(beats)

    assert float(target["intensity"]) >= 7.0
    assert len(target["visuals"]) == before
    assert not [r for r in (beats.get("repairs") or []) if r["code"] == "coverage_trimmed"]


def test_line_heavy_beat_is_not_flagged(beats):
    """The budget governs added silent shots, not lines selection already approved."""
    target = _beat(beats, "b003")
    target.pop("visual", None)
    target["intensity"] = 5.0
    target["lines"] = target["lines"] * 4
    select_beats(beats)
    assert validate_beats(beats, raise_on_error=False) == []


def test_duration_estimate_counts_every_silent_shot(beats):
    target = _beat(beats, "b001")
    target.pop("visual", None)
    target.pop("visuals", None)
    bare = estimate_beat_duration_s(target)
    target["visuals"] = copy.deepcopy(COVERAGE)
    assert estimate_beat_duration_s(target) == pytest.approx(bare + 3 + 1.5 + 1.5)
