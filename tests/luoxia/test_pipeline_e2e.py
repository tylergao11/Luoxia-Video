from __future__ import annotations

import json

import pytest

from src.luoxia import pipeline as pipeline_mod
from src.luoxia.beats.repairs import StrictRepairError
from tests.luoxia.test_analyzer import NOVEL, _fake_chat_json

LOOSE = {"max_compression_ratio": 0.5, "min_drop_rate": 0.2}


class _FakeLLM:
    """Stands in for DashScope: scores beats, and echoes back any polish request."""

    is_configured = True

    def chat_json(self, messages, **kwargs):
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        if "短剧改编总监" in system:
            return _fake_chat_json(messages)
        return {}

    def chat(self, messages, **kwargs):
        return ""


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Real analyze/select/bridge/solve/stills wiring; only the paid calls are faked."""
    monkeypatch.setattr(pipeline_mod, "LuoxiaLLM", lambda *a, **k: _FakeLLM())
    monkeypatch.setattr(
        pipeline_mod, "polish_timeline_prompts", lambda tl, **k: tl
    )

    generated = {"sheets": [], "stills": []}

    def fake_sheets(cast, *, output_root, **kwargs):
        out = {}
        for c in cast:
            if not (c.get("appearance") or "").strip():
                continue
            p = tmp_path / "sheets" / f"{c['character_id']}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"png")
            c["reference_image_path"] = str(p)
            out[c["character_id"]] = str(p)
            generated["sheets"].append(c["character_id"])
        return out

    def fake_stills(timeline, *, output_root, **kwargs):
        from src.luoxia.stills.characters import reference_map, refs_for_shot

        refs = reference_map(timeline)
        for shot in timeline["shots"]:
            p = tmp_path / "stills" / f"{shot['shot_id']}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"png")
            shot_refs = refs_for_shot(shot, refs)
            shot["still"].update({"status": "ready", "local_path": str(p)})
            if shot_refs:
                shot["still"]["reference_image_paths"] = shot_refs
            generated["stills"].append((shot["shot_id"], shot_refs))
        return timeline

    def fake_tts(episode_dir, timeline):
        def synthesize(shot, speed):
            text = (shot.get("dialogue") or {}).get("text") or ""
            return max(0.5, len(text) * 0.18 / max(speed, 1e-6)), f"a/{shot['shot_id']}.wav", "sha256:x"

        return synthesize

    monkeypatch.setattr(pipeline_mod, "ensure_character_sheets", fake_sheets)
    monkeypatch.setattr(pipeline_mod, "render_timeline_stills", fake_stills)
    monkeypatch.setattr(pipeline_mod, "_make_tts", fake_tts)
    monkeypatch.setattr(pipeline_mod, "make_rewrite_fn", lambda llm=None: None)

    novel = tmp_path / "demo.txt"
    novel.write_text(NOVEL, encoding="utf-8")
    return novel, tmp_path, generated


def _run(novel, root, **kwargs):
    # Injecting the render step is the only way to skip the paid video call; there is
    # deliberately no offline video mode that could pass off held stills as an episode.
    kwargs.setdefault("render_videos", lambda tl, **k: tl)
    return pipeline_mod.run_from_novel(
        novel,
        output_root=root / "out",
        work_id="e2e",
        skip_compose=True,
        beats_overrides=LOOSE,
        **kwargs,
    )


def test_novel_to_frozen_timeline_without_touching_paid_apis(wired):
    novel, root, generated = wired

    result = _run(novel, root)

    assert result.phase in {"frozen", "rendering", "rendered"}
    beats = json.loads(result.beats_path.read_text(encoding="utf-8"))
    assert beats["phase"] == "delivered"
    assert beats["quality"]["repair_count"] >= 0

    timeline = json.loads(result.timeline_path.read_text(encoding="utf-8"))
    assert timeline["shots"]
    assert all(s["still"]["status"] == "ready" for s in timeline["shots"])
    # Every dialogue shot got real measured audio, not a guessed duration.
    audio_shots = [s for s in timeline["shots"] if s.get("timing_driver") == "audio"]
    assert audio_shots
    for shot in audio_shots:
        assert shot["audio"]["measured_duration_s"] > 0
        assert shot["timing"]["end_s"] > shot["timing"]["start_s"]


def test_faces_are_locked_end_to_end(wired):
    novel, root, generated = wired

    result = _run(novel, root)

    assert set(generated["sheets"]) == {"lin_wan", "shen_ce"}
    timeline = json.loads(result.timeline_path.read_text(encoding="utf-8"))
    assert all(c.get("reference_image_asset_id") for c in timeline["cast"])
    with_refs = [sid for sid, refs in generated["stills"] if refs]
    assert with_refs, "shots with characters must carry portrait references"


def test_faces_can_be_turned_off(wired):
    novel, root, generated = wired

    _run(novel, root, lock_faces=False)
    assert generated["sheets"] == []


def test_strict_gate_stops_before_any_spending(wired, monkeypatch):
    novel, root, generated = wired
    spent = []

    def mute_payoff(messages):
        data = _fake_chat_json(messages)
        for beat in data["beats"]:
            if beat["beat_id"] == "b005":
                beat["lines"] = []
        return data

    monkeypatch.setattr(
        pipeline_mod, "analyze_novel", lambda text, **kw: _real_analyze(text, mute_payoff, **kw)
    )

    with pytest.raises(StrictRepairError):
        _run(
            novel,
            root,
            max_repair_severity="medium",
            render_videos=lambda tl, **k: spent.append("video"),
        )

    assert generated["sheets"] == [], "no portraits should be generated after a refusal"
    assert generated["stills"] == []
    assert spent == []


def test_refused_beats_are_still_written_for_inspection(wired, monkeypatch):
    novel, root, _ = wired

    def mute_payoff(messages):
        data = _fake_chat_json(messages)
        data["beats"][4]["lines"] = []
        return data

    monkeypatch.setattr(
        pipeline_mod, "analyze_novel", lambda text, **kw: _real_analyze(text, mute_payoff, **kw)
    )
    with pytest.raises(StrictRepairError):
        _run(novel, root, max_repair_severity="medium")

    path = root / "out" / "e2e" / "beats.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert any(r["code"] == "line_invented" for r in doc["repairs"])


def _real_analyze(text, chat_json, **kwargs):
    from src.luoxia.beats.analyzer import analyze_novel

    kwargs.pop("llm", None)
    return analyze_novel(text, chat_json=chat_json, **kwargs)
