from __future__ import annotations

import copy

import pytest

from src.luoxia.timeline.solver import SolverError, solve_timeline
from src.luoxia.timeline.validator import validate_timeline


def _base_timeline():
    return {
        "schema_version": "1.0.0",
        "episode_id": "ep_test",
        "phase": "draft",
        "global": {
            "fps": 25,
            "aspect_ratio": "9:16",
            "resolution": "720p",
            "lead_in_s": 0.3,
            "tail_out_s": 0.5,
            "min_speed_ratio": 0.92,
            "max_speed_ratio": 1.10,
            "default_action_duration_s": 4,
        },
        "cast": [{"character_id": "a", "display_name": "A", "voice_id": "longxiaochun"}],
        "shots": [],
        "cost": {"currency": "USD", "budget_ceiling_usd": 50},
        "audit": [],
    }


def _synth_factory(measured_by_text_len=True, fixed=None):
    calls = []

    def synthesize(shot, speed):
        text = (shot.get("dialogue") or {}).get("text") or ""
        if fixed is not None:
            measured = fixed / max(speed, 1e-6)
        else:
            # ~0.2s per char at speed 1
            measured = max(0.4, len(text) * 0.2 / max(speed, 1e-6))
        calls.append((shot["shot_id"], speed, text, measured))
        return measured, f"audio/{shot['shot_id']}.wav", f"sha256:{shot['shot_id']}:{speed:.3f}"

    synthesize.calls = calls
    return synthesize


def test_rhythm_and_audio_layout_and_branches():
    tl = _base_timeline()
    tl["shots"] = [
        {
            "shot_id": "s_rhythm",
            "index": 0,
            "type": "transition",
            "timing_driver": "rhythm",
            "shot_size": "wide",
            "timing": {"trim": {"strategy": "tail"}},
            "still": {"status": "ready", "aspect_ratio": "9:16"},
            "video": {"status": "pending", "provider": "xai", "model": "grok-imagine-video-1.5"},
            "lipsync": {"required": False, "status": "skipped"},
            "subtitle": {},
        },
        {
            "shot_id": "s_ok",
            "index": 1,
            "type": "dialogue",
            "timing_driver": "audio",
            "shot_size": "medium",
            "dialogue": {"character_id": "a", "text": "你好。", "rewrite_count": 0},
            "audio": {"status": "pending"},
            "timing": {"trim": {"strategy": "tail"}},
            "still": {"status": "ready", "aspect_ratio": "9:16"},
            "video": {"status": "pending", "provider": "xai", "model": "grok-imagine-video-1.5"},
            "lipsync": {"required": False, "status": "skipped"},
            "subtitle": {},
        },
        {
            "shot_id": "s_rewrite",
            "index": 2,
            "type": "dialogue",
            "timing_driver": "audio",
            "shot_size": "medium",
            "dialogue": {
                "character_id": "a",
                "text": "这是一段偏长需要压缩改写的台词内容用于测试偏差区间。",
                "rewrite_count": 0,
            },
            "audio": {"status": "pending"},
            "timing": {"trim": {"strategy": "tail"}},
            "still": {"status": "ready", "aspect_ratio": "9:16"},
            "video": {"status": "pending", "provider": "xai", "model": "grok-imagine-video-1.5"},
            "lipsync": {"required": False, "status": "skipped"},
            "subtitle": {},
        },
    ]

    synth = _synth_factory()

    def rewrite(text, budget, shot):
        return text[: max(2, int(budget / 0.2))]

    solve_timeline(
        tl,
        synthesize=synth,
        rewrite=rewrite,
        planned_durations={"s_ok": 1.2, "s_rewrite": 2.0},
    )
    validate_timeline(tl)
    assert tl["phase"] == "audio_locked"
    ids = [s["shot_id"] for s in tl["shots"]]
    assert "s_rhythm" in ids
    assert any(i == "s_ok" or i.startswith("s_ok") for i in ids)
    # Large planned deviation should rewrite or split (children get *_pN ids).
    assert any(i.startswith("s_rewrite") for i in ids)
    rewritten_or_split = any(
        (s.get("dialogue") or {}).get("rewrite_count", 0) > 0 or "_p" in s["shot_id"]
        for s in tl["shots"]
        if s["shot_id"].startswith("s_rewrite")
    )
    assert rewritten_or_split
    # Master clock uses target not request
    for i in range(1, len(tl["shots"])):
        assert abs(tl["shots"][i]["timing"]["start_s"] - tl["shots"][i - 1]["timing"]["end_s"]) < 1e-6




def test_split_on_provider_max():
    tl = _base_timeline()
    long_text = "啊" * 120  # ~24s at 0.2s/char
    tl["shots"] = [
        {
            "shot_id": "s_long",
            "index": 0,
            "type": "dialogue",
            "timing_driver": "audio",
            "shot_size": "medium",
            "dialogue": {"character_id": "a", "text": long_text, "rewrite_count": 0},
            "audio": {"status": "pending"},
            "timing": {"trim": {"strategy": "tail"}},
            "still": {"status": "ready", "aspect_ratio": "9:16"},
            "video": {"status": "pending", "provider": "xai", "model": "grok-imagine-video-1.5"},
            "lipsync": {"required": False, "status": "skipped"},
            "subtitle": {},
        }
    ]
    synth = _synth_factory()
    solve_timeline(tl, synthesize=synth)
    assert len(tl["shots"]) >= 2
    assert all(s["timing"]["request_duration_s"] <= 15 for s in tl["shots"])
    validate_timeline(tl)


def test_llm_rewrite_branch():
    tl = _base_timeline()
    tl["shots"] = [
        {
            "shot_id": "s_mid",
            "index": 0,
            "type": "dialogue",
            "timing_driver": "audio",
            "shot_size": "medium",
            "dialogue": {"character_id": "a", "text": "abcdefghij", "rewrite_count": 0},
            "audio": {"status": "pending"},
            "timing": {"trim": {"strategy": "tail"}},
            "still": {"status": "ready", "aspect_ratio": "9:16"},
            "video": {"status": "pending", "provider": "xai", "model": "grok-imagine-video-1.5"},
            "lipsync": {"required": False, "status": "skipped"},
            "subtitle": {},
        }
    ]
    # measured=2.0 -> target=2.8; planned=2.24 => deviation≈0.25 => llm_rewrite
    solve_timeline(
        tl,
        synthesize=_synth_factory(),
        rewrite=lambda t, b, s: "abcd",
        planned_durations={"s_mid": 2.24},
    )
    assert tl["shots"][0]["timing"]["resolution_branch"] == "llm_rewrite"
    assert tl["shots"][0]["dialogue"]["rewrite_count"] == 1
    validate_timeline(tl)


def test_pinned_fit():
    tl = _base_timeline()
    tl["shots"] = [
        {
            "shot_id": "s_pin",
            "index": 0,
            "type": "dialogue",
            "timing_driver": "pinned",
            "shot_size": "close_up",
            "dialogue": {"character_id": "a", "text": "钉死画面的台词需要适配。", "rewrite_count": 0},
            "audio": {"status": "pending"},
            "timing": {"pinned_duration_s": 5.0, "trim": {"strategy": "tail"}},
            "still": {"status": "ready", "aspect_ratio": "9:16"},
            "video": {"status": "pending", "provider": "xai", "model": "grok-imagine-video-1.5"},
            "lipsync": {"required": False, "status": "skipped"},
            "subtitle": {},
        }
    ]
    synth = _synth_factory()
    solve_timeline(tl, synthesize=synth, rewrite=lambda t, b, s: t[:8])
    assert tl["shots"][0]["timing"]["resolution_branch"] == "pinned_fit"
    assert tl["shots"][0]["timing"]["target_duration_s"] == 5.0
    validate_timeline(tl)
    assert tl["shots"][0]["lipsync"]["required"] is True
