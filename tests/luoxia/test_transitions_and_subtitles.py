from __future__ import annotations

import copy

import pytest

from src.luoxia.compose.subtitles import (
    MARGIN_V_RATIO,
    build_cues,
    frame_size,
    resolve_position,
    resolve_style,
    shot_subtitle_window,
    style_for_frame,
    write_ass,
)
from src.luoxia.paths import TIMELINE_EXAMPLE_PATH
from src.luoxia.timeline.io import load_timeline
from src.luoxia.timeline.transitions import (
    head_room_s,
    needs_filter_graph,
    plan_segments,
    tail_room_s,
    total_duration_s,
    transition_of,
)
from src.luoxia.timeline.validator import TimelineValidationError, validate_timeline


@pytest.fixture
def example():
    return load_timeline(TIMELINE_EXAMPLE_PATH)


def _sum_targets(timeline) -> float:
    return sum(float(s["timing"]["target_duration_s"]) for s in timeline["shots"])


# --- transitions never move the master clock -------------------------------------


def test_plan_preserves_total_duration(example):
    plans = plan_segments(example)
    assert total_duration_s(plans) == pytest.approx(_sum_targets(example))


def test_dissolve_extends_outgoing_and_overlaps_incoming(example):
    plans = plan_segments(example)
    # s001 declares a 0.3s dissolve into s002.
    assert plans[0].extend_s == pytest.approx(0.3)
    assert plans[1].dissolve_in_s == pytest.approx(0.3)
    # The extension exactly pays for the overlap.
    assert plans[0].segment_duration_s - plans[1].dissolve_in_s == pytest.approx(
        plans[0].duration_s
    )


def test_fade_black_splits_across_the_boundary(example):
    plans = plan_segments(example)
    # s003 declares a 0.3s fade_black into s004.
    assert plans[2].fade_out_s == pytest.approx(0.3)
    assert plans[2].fade_out_color == "black"
    assert plans[3].fade_in_s == pytest.approx(0.3)
    # A fade costs no extra material.
    assert plans[2].extend_s == 0.0
    assert plans[2].segment_duration_s == pytest.approx(plans[2].duration_s)


def test_cut_is_a_noop(example):
    plans = plan_segments(example)
    assert transition_of(example["shots"][1]) == ("cut", 0.0)
    assert plans[2].fade_in_s == 0.0
    assert plans[2].dissolve_in_s == 0.0


def test_last_shot_fade_out_has_no_partner(example):
    plans = plan_segments(example)
    assert plans[-1].fade_out_s == pytest.approx(0.5)
    assert plans[-1].extend_s == 0.0


def test_dissolve_on_last_shot_degrades_to_cut(example):
    example["shots"][-1]["transition"] = {"kind": "dissolve", "duration_s": 0.4}
    plans = plan_segments(example)
    assert plans[-1].extend_s == 0.0
    assert total_duration_s(plans) == pytest.approx(_sum_targets(example))


def test_filter_graph_only_needed_for_dissolves(example):
    assert needs_filter_graph(plan_segments(example)) is True
    for shot in example["shots"]:
        shot["transition"] = {"kind": "cut", "duration_s": 0.0}
    assert needs_filter_graph(plan_segments(example)) is False


# --- breathing room accounting ---------------------------------------------------


def test_silent_shot_is_all_breathing_room(example):
    empty = example["shots"][0]  # rhythm shot, no audio
    assert head_room_s(empty) == pytest.approx(4.0)
    assert tail_room_s(empty) == pytest.approx(4.0)


def test_speaking_shot_room_is_lead_and_tail(example):
    talking = example["shots"][1]
    assert head_room_s(talking) == pytest.approx(0.3)
    assert tail_room_s(talking) == pytest.approx(0.5)


def test_fade_longer_than_tail_room_is_rejected(example):
    example["shots"][1]["transition"] = {"kind": "fade_black", "duration_s": 0.9}
    with pytest.raises(TimelineValidationError) as exc:
        validate_timeline(example)
    assert any(i.code == "transition_covers_speech" for i in exc.value.issues)


def test_dissolve_longer_than_next_lead_in_is_rejected(example):
    example["shots"][0]["transition"] = {"kind": "dissolve", "duration_s": 0.8}
    with pytest.raises(TimelineValidationError) as exc:
        validate_timeline(example)
    assert any(i.code == "transition_covers_speech" for i in exc.value.issues)


def test_dissolve_into_silent_shot_may_be_long(example):
    # Reorder so a dialogue shot dissolves into the silent establishing shot.
    example["shots"][2]["transition"] = {"kind": "dissolve", "duration_s": 1.2}
    example["shots"][3]["audio"] = {"status": "pending"}
    example["shots"][3]["timing_driver"] = "rhythm"
    example["shots"][3].pop("dialogue")
    example["shots"][3]["subtitle"] = {"text": None, "start_s": None, "end_s": None}
    example["shots"][3]["transition"] = {"kind": "cut", "duration_s": 0.0}
    assert head_room_s(example["shots"][3]) == pytest.approx(5.0)
    assert validate_timeline(example, raise_on_error=False) == []


def test_cut_with_duration_is_rejected(example):
    example["shots"][1]["transition"] = {"kind": "cut", "duration_s": 0.4}
    with pytest.raises(TimelineValidationError) as exc:
        validate_timeline(example)
    assert any(i.code == "cut_with_duration" for i in exc.value.issues)


def test_non_cut_without_duration_is_rejected(example):
    example["shots"][1]["transition"] = {"kind": "dissolve", "duration_s": 0}
    with pytest.raises(TimelineValidationError) as exc:
        validate_timeline(example)
    assert any(i.code == "transition_without_duration" for i in exc.value.issues)


def test_transition_is_not_part_of_the_freeze_hash(example):
    from src.luoxia.timeline.hashing import compute_timeline_hash

    before = compute_timeline_hash(example)
    mutated = copy.deepcopy(example)
    mutated["shots"][1]["transition"] = {"kind": "fade_white", "duration_s": 0.2}
    mutated["global"]["subtitle_style"] = {"position": "top"}
    assert compute_timeline_hash(mutated) == before


# --- subtitle style and cue splitting --------------------------------------------


def test_frame_size_follows_aspect_and_resolution(example):
    assert frame_size(example) == (1920, 1080)
    hd = copy.deepcopy(example)
    hd["global"]["resolution"] = "720p"
    assert frame_size(hd) == (1280, 720)
    portrait = copy.deepcopy(example)
    portrait["global"]["aspect_ratio"] = "9:16"
    assert frame_size(portrait) == (1080, 1920)


def test_geometry_derives_from_the_real_frame(example):
    style = style_for_frame(resolve_style(example), 1280, 720)
    assert style["margin_v_px"] == round(720 * MARGIN_V_RATIO)
    assert style["font_size_px"] == round(720 * 0.05)
    assert style["margin_h_px"] == round(1280 * 0.08)
    assert style["outline_px"] == 2

    # Same style, bigger frame: every size scales, nothing needs retuning.
    hd = style_for_frame(resolve_style(example), 1920, 1080)
    assert hd["font_size_px"] == round(1080 * 0.05)
    assert hd["margin_v_px"] == round(1080 * MARGIN_V_RATIO)


def test_chars_per_line_clamped_to_what_fits(example):
    wide = style_for_frame(resolve_style(example), 1280, 720)
    assert wide["max_chars_per_line"] == 16  # configured value fits comfortably

    # A narrow frame cannot fit 16 glyphs; libass would re-wrap and break the line budget.
    narrow = style_for_frame(resolve_style(example), 360, 640)
    assert narrow["max_chars_per_line"] < 16


def test_ass_declares_the_frame_and_maps_position(example, tmp_path):
    style = style_for_frame(resolve_style(example), 1280, 720)
    cues = build_cues("你怎么在这？", start=0.3, end=1.72, style=style)

    body = write_ass(
        tmp_path / "s.ass", cues, style=style, position="bottom", width=1280, height=720
    ).read_text(encoding="utf-8")

    # PlayRes must equal the frame, otherwise px is not px.
    assert "PlayResX: 1280" in body
    assert "PlayResY: 720" in body
    assert "WrapStyle: 2" in body
    # ASS colours are &HAABBGGRR, not #RRGGBB.
    assert "&H00FFFFFF" in body
    assert body.count("Dialogue:") == 1
    assert "0:00:00.30,0:00:01.72" in body

    fields = next(l for l in body.splitlines() if l.startswith("Style: ")).split(",")
    assert fields[-5] == "2"  # bottom-centre alignment
    top = write_ass(
        tmp_path / "t.ass", cues, style=style, position="top", width=1280, height=720
    ).read_text(encoding="utf-8")
    assert next(l for l in top.splitlines() if l.startswith("Style: ")).split(",")[-5] == "8"


def test_ass_escapes_line_breaks(example, tmp_path):
    style = style_for_frame(resolve_style(example), 1280, 720)
    cues = build_cues(
        "我等了三年。每一天都在想，如果那天我没有走。", start=0.0, end=4.0, style=style
    )
    body = write_ass(
        tmp_path / "m.ass", cues, style=style, position="bottom", width=1280, height=720
    ).read_text(encoding="utf-8")
    assert "\\N" in body
    assert "\n\n" not in body.split("[Events]")[1]


def test_per_shot_position_overrides_global(example):
    style = resolve_style(example)
    assert resolve_position(example["shots"][1], style) == "bottom"
    example["shots"][1]["subtitle"]["position"] = "top"
    assert resolve_position(example["shots"][1], style) == "top"


def test_long_line_splits_into_multiple_cues(example):
    style = style_for_frame(resolve_style(example), 1280, 720)
    shot = example["shots"][2]
    window = shot_subtitle_window(shot)
    assert window is not None
    cues = build_cues(shot["subtitle"]["text"], start=window[0], end=window[1], style=style)

    assert len(cues) > 1
    for cue in cues:
        for line in cue.text.split("\n"):
            assert len(line) <= style["max_chars_per_line"]
        assert len(cue.text.split("\n")) <= style["max_lines_per_cue"]

    # Cues tile the speech window without gaps or overlaps.
    assert cues[0].start == pytest.approx(window[0])
    assert cues[-1].end == pytest.approx(window[1])
    for prev, nxt in zip(cues, cues[1:]):
        assert nxt.start == pytest.approx(prev.end)

    # No characters lost in wrapping.
    joined = "".join(c.text.replace("\n", "") for c in cues)
    assert joined == shot["subtitle"]["text"]


def test_short_line_stays_one_cue(example):
    style = style_for_frame(resolve_style(example), 1280, 720)
    cues = build_cues("你怎么在这？", start=0.3, end=1.72, style=style)
    assert len(cues) == 1
    assert cues[0].text == "你怎么在这？"


def test_subtitle_window_is_segment_local(example):
    shot = example["shots"][1]
    start, end = shot_subtitle_window(shot)
    assert start == pytest.approx(0.3)
    assert end == pytest.approx(0.3 + 1.42)


def test_silent_shot_has_no_subtitle_window(example):
    assert shot_subtitle_window(example["shots"][0]) is None
