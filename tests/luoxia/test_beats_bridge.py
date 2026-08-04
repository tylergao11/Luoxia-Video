from __future__ import annotations

import pytest

from src.luoxia.beats.io import load_beats
from src.luoxia.beats.to_timeline import BridgeError, build_timeline_draft
from src.luoxia.beats.validator import validate_beats
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


def test_draft_shape(beats):
    draft = build_timeline_draft(beats, "ep01")
    assert draft["phase"] == "draft"
    assert draft["episode_id"] == "ep01"
    assert [c["character_id"] for c in draft["cast"]] == ["lin_wan", "shen_ce"]

    ids = [s["shot_id"] for s in draft["shots"]]
    # b001 and b005 carry a visual, so each contributes one rhythm shot before its lines.
    assert ids == [
        "ep01_b001_v",
        "ep01_b001_l01",
        "ep01_b001_l02",
        "ep01_b003_l01",
        "ep01_b005_v",
        "ep01_b005_l01",
        "ep01_b005_l02",
        "ep01_b006_l01",
    ]
    assert [s["index"] for s in draft["shots"]] == list(range(8))

    visual = draft["shots"][0]
    assert visual["timing_driver"] == "rhythm"
    assert visual["timing"]["target_duration_s"] == 3.0

    line = draft["shots"][1]
    assert line["timing_driver"] == "audio"
    assert line["dialogue"]["text"] == "林晚，你也配站在这里？"
    assert line["dialogue"]["emotion"] == "轻蔑，音量不高但压过全场"
    assert line["audio"]["voice_id"] == "longshu"
    assert line["still"]["aspect_ratio"] == "9:16"


def test_draft_carries_no_durations_of_its_own(beats):
    """Dialogue length may only come from measured audio, never from the beats file."""
    draft = build_timeline_draft(beats, "ep01")
    for shot in draft["shots"]:
        if shot["timing_driver"] == "audio":
            assert "target_duration_s" not in shot["timing"]
            assert "request_duration_s" not in shot["timing"]
            assert shot["audio"]["status"] == "pending"


def test_draft_validates_under_the_draft_profile(beats):
    """A draft is a legal phase, so validating it must not report every timing field as missing."""
    draft = build_timeline_draft(beats, "ep01")
    assert validate_timeline(draft, raise_on_error=False) == []

    # Structural mistakes are still caught before a single image is generated.
    draft["shots"][1]["dialogue"]["character_id"] = "ghost"
    draft["shots"][2]["still"]["aspect_ratio"] = "16:9"
    issues = validate_timeline(draft, raise_on_error=False)
    assert {i.invariant for i in issues} == {12, 13}


def test_beats_to_solve_to_valid_timeline(beats):
    draft = build_timeline_draft(beats, "ep01")
    solve_timeline(draft, synthesize=_fake_tts)
    validate_timeline(draft)

    assert draft["phase"] == "audio_locked"
    assert draft["shots"][0]["timing"]["start_s"] == 0.0
    total = draft["shots"][-1]["timing"]["end_s"]
    assert total == pytest.approx(sum(s["timing"]["target_duration_s"] for s in draft["shots"]))


def test_unselected_beats_cannot_be_bridged(beats):
    beats["phase"] = "scored"
    with pytest.raises(BridgeError, match="must be selected"):
        build_timeline_draft(beats, "ep01")


def test_unknown_episode_is_reported_with_options(beats):
    with pytest.raises(BridgeError, match=r"known episodes: \['ep01'\]"):
        build_timeline_draft(beats, "ep99")


def test_missing_voice_blocks_the_bridge(beats):
    next(c for c in beats["cast"] if c["character_id"] == "lin_wan")["voice_id"] = None
    with pytest.raises(BridgeError, match="no voice_id"):
        build_timeline_draft(beats, "ep01")
