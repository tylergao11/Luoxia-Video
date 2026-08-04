from __future__ import annotations

import pytest

from src.luoxia.beats.analyzer import analyze_novel
from src.luoxia.beats.repairs import StrictRepairError
from src.luoxia.beats.selector import select_beats
from src.luoxia.beats.to_timeline import build_timeline_draft
from src.luoxia.beats.validator import validate_beats
from src.luoxia.timeline.solver import solve_timeline
from src.luoxia.timeline.validator import validate_timeline

PARAGRAPHS = [
    "拍卖会门口，沈策伸手拦住了林晚。他说这里的门槛是一个亿，让她别自取其辱。",
    "水晶灯照着长廊，两侧陈列着历年拍出的珍品，宾客三三两两地低声交谈。",
    "林晚想起三年前被赶出林家那天，所有人都说她是废物，连行李都是被扔出门的。",
    "拍卖流程开始，前三件拍品依次落槌，价格一次比一次高。",
    "压轴画作的落款被投上大屏，那两个字是林晚。她同时是这场拍卖会最大的债主。",
    "她转身走向门口，只留下一句：三年前那场火，不是意外。",
]
NOVEL = "\n\n".join(PARAGRAPHS)


def _fake_chat_json(messages):
    """A well-behaved model: paragraph ranges only, never character offsets."""
    return {
        "title": "废物千金",
        "cast": [
            {
                "character_id": "lin_wan",
                "display_name": "林晚",
                "voice_id": "longxiaochun",
                "role": "protagonist",
                "appearance": "二十五岁女性，黑色长发挽起，旧灰呢外套，眼神清冷",
                "aliases": [],
            },
            {
                "character_id": "shen_ce",
                "display_name": "沈策",
                "voice_id": "longshu",
                "role": "antagonist",
                "appearance": "三十岁男性，深色三件套西装，短发，下颌线锋利",
                "aliases": [],
            },
        ],
        "beats": [
            {
                "beat_id": "b001",
                "para_start": 0,
                "para_end": 0,
                "summary": "沈策当众拦下林晚羞辱她",
                "beat_type": "conflict_escalation",
                "intensity": 7.2,
                "depends_on": [],
                "scene_id": "scene_gate",
                "lines": [
                    {
                        "character_id": "shen_ce",
                        "text": "林晚，你也配站在这里？",
                        "delivery": "轻蔑",
                        "shot_size": "medium",
                        "line_type": "dialogue",
                    }
                ],
                "visual": {
                    "scene_id": "scene_gate",
                    "shot_size": "wide",
                    "prompt": "拍卖会门口夜景",
                    "action_duration_s": 3,
                },
                "cliffhanger": None,
            },
            {
                "beat_id": "b002",
                "para_start": 1,
                "para_end": 1,
                "summary": "环境描写",
                "beat_type": "filler",
                "intensity": 1.0,
                "depends_on": [],
                "lines": [],
                "cliffhanger": None,
            },
            {
                "beat_id": "b003",
                "para_start": 2,
                "para_end": 2,
                "summary": "回忆被赶出林家",
                "beat_type": "setup",
                "intensity": 3.5,
                "depends_on": [],
                "lines": [
                    {
                        "character_id": "lin_wan",
                        "text": "三年前你把我赶出林家。",
                        "delivery": "平静",
                        "shot_size": "close_up",
                        "line_type": "dialogue",
                    }
                ],
                "cliffhanger": None,
            },
            {
                "beat_id": "b004",
                "para_start": 3,
                "para_end": 3,
                "summary": "流程铺陈",
                "beat_type": "filler",
                "intensity": 1.5,
                "depends_on": [],
                "lines": [],
                "cliffhanger": None,
            },
            {
                "beat_id": "b005",
                "para_start": 4,
                "para_end": 4,
                "summary": "落款曝光打脸",
                "beat_type": "face_slap",
                "intensity": 9.0,
                "depends_on": ["b001", "b003"],
                "lines": [
                    {
                        "character_id": "lin_wan",
                        "text": "这幅画的落款，是我写的。",
                        "delivery": "冷静",
                        "shot_size": "close_up",
                        "line_type": "dialogue",
                    }
                ],
                "cliffhanger": None,
            },
            {
                "beat_id": "b006",
                "para_start": 5,
                "para_end": 5,
                "summary": "抛出火灾悬念",
                "beat_type": "hook",
                "intensity": 8.0,
                "depends_on": ["b005"],
                "lines": [
                    {
                        "character_id": "lin_wan",
                        "text": "三年前那场火，不是意外。",
                        "delivery": "决绝",
                        "shot_size": "close_up",
                        "line_type": "dialogue",
                    }
                ],
                "cliffhanger": {"tier": "tier_1", "question": "火是谁放的？"},
            },
        ],
    }


def _loose(**extra):
    # Short fixture text can't hit the default 0.15 compression budget.
    return {"max_compression_ratio": 0.5, "min_drop_rate": 0.2, **extra}


def test_every_source_span_matches_the_real_text():
    """The audit trail must be true: the excerpt has to be what is actually at the span."""
    doc = analyze_novel(NOVEL, work_id="demo_span", chat_json=_fake_chat_json)
    assert doc["beats"], "expected beats"
    for beat in doc["beats"]:
        span = beat["source_span"]
        actual = NOVEL[span["start_char"] : span["end_char"]].strip().replace("\n", " ")
        assert actual.startswith(span["excerpt"][:20])


def test_spans_are_ordered_and_non_overlapping():
    doc = analyze_novel(NOVEL, work_id="demo_order", chat_json=_fake_chat_json)
    prev_end = 0
    for beat in doc["beats"]:
        span = beat["source_span"]
        assert span["start_char"] >= prev_end
        assert span["end_char"] > span["start_char"]
        prev_end = span["end_char"]


def test_unclaimed_paragraphs_become_filler_beats():
    """Prose the model forgot must stay visible, or drop_rate quietly lies."""

    def partial(messages):
        data = _fake_chat_json(messages)
        # Model only reports the first and last paragraph.
        data["beats"] = [data["beats"][0], data["beats"][-1]]
        return data

    doc = analyze_novel(NOVEL, work_id="demo_gap", chat_json=partial)
    fillers = [b for b in doc["beats"] if b["beat_id"].startswith("gap_")]
    assert fillers, "middle paragraphs should be recorded as filler"
    assert any(r["code"] == "coverage_gap_filled" for r in doc["repairs"])
    covered = sum(b["source_span"]["end_char"] - b["source_span"]["start_char"] for b in doc["beats"])
    assert covered > len(NOVEL) * 0.9


def test_analyze_select_bridge_solve_mocked():
    doc = analyze_novel(
        NOVEL,
        work_id="demo_ch1",
        title="废物千金",
        chat_json=_fake_chat_json,
        global_overrides=_loose(),
    )
    assert doc["phase"] == "scored"
    assert doc["source"]["char_count"] == len(NOVEL)

    select_beats(doc)
    validate_beats(doc)
    assert doc["selection"]["dropped"] >= 1

    draft = build_timeline_draft(doc, doc["episodes"][0]["episode_id"])

    def synth(shot, speed):
        text = (shot.get("dialogue") or {}).get("text") or ""
        measured = max(0.5, len(text) * 0.2 / max(speed, 1e-6))
        return measured, f"a/{shot['shot_id']}.wav", f"sha256:{shot['shot_id']}"

    solve_timeline(draft, synthesize=synth)
    validate_timeline(draft)
    assert draft["phase"] == "audio_locked"


def test_clean_run_records_no_high_severity_repairs():
    doc = analyze_novel(NOVEL, work_id="demo_clean", chat_json=_fake_chat_json, global_overrides=_loose())
    select_beats(doc, max_repair_severity="medium")
    assert doc["quality"]["by_severity"]["high"] == 0


def test_cold_open_reorders_the_episode_not_the_source():
    """A weak opening is fixed in narrative order; the beats array stays in source order."""

    def bad_open(messages):
        data = _fake_chat_json(messages)
        data["beats"][0]["beat_type"] = "setup"
        data["beats"][0]["intensity"] = 6.6
        data["beats"][0]["depends_on"] = []
        return data

    doc = analyze_novel(NOVEL, work_id="demo_open", chat_json=bad_open, global_overrides=_loose())
    assert doc["beats"][0]["beat_type"] == "setup", "source order must not be rewritten"
    assert any(r["code"] == "weak_opening_detected" for r in doc["repairs"])

    select_beats(doc)
    first_ep_ids = doc["episodes"][0]["beat_ids"]
    opener = next(b for b in doc["beats"] if b["beat_id"] == first_ep_ids[0])
    assert opener["beat_type"] != "setup"
    validate_beats(doc)


def test_invented_dialogue_is_logged_and_can_be_refused():
    """Silently patching a hot beat with narration is exactly what the ledger exists to catch."""

    def mute_payoff(messages):
        data = _fake_chat_json(messages)
        for beat in data["beats"]:
            if beat["beat_id"] == "b005":
                beat["lines"] = []
        return data

    doc = analyze_novel(NOVEL, work_id="demo_mute", chat_json=mute_payoff, global_overrides=_loose())
    invented = [r for r in doc["repairs"] if r["code"] == "line_invented"]
    assert invented and invented[0]["severity"] == "high"
    assert invented[0]["beat_id"] == "b005"

    with pytest.raises(StrictRepairError) as exc:
        select_beats(doc, max_repair_severity="medium")
    assert "line_invented" in str(exc.value)


def test_missing_appearance_is_flagged_before_faces_drift():
    def faceless(messages):
        data = _fake_chat_json(messages)
        for c in data["cast"]:
            c.pop("appearance", None)
        return data

    doc = analyze_novel(NOVEL, work_id="demo_face", chat_json=faceless, global_overrides=_loose())
    flagged = [r for r in doc["repairs"] if r["code"] == "appearance_missing"]
    assert len(flagged) == 2
    assert all(c["appearance"] is None for c in doc["cast"])
