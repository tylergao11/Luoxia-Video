"""App-facing Luoxia bridge: ID layout, status, bridge→frames sync."""
from __future__ import annotations

import json
from pathlib import Path

from src.apps.comic_gen.models import Script
from src.apps.comic_gen import luoxia_service as ls
from src.luoxia.beats.io import save_beats
from src.luoxia.timeline.io import load_timeline


def _script(sid: str = "app_ep_bridge") -> Script:
    return Script(
        id=sid,
        title="bridge-demo",
        original_text="sample novel text",
        created_at=1.0,
        updated_at=1.0,
    )


def test_work_and_episode_ids_align_with_duration_resolver():
    s = _script("script_abc")
    assert ls.episode_id_for(s) == "script_abc"
    assert ls.work_id_for(s) == "script_abc"
    s.series_id = "series_xyz"
    assert ls.work_id_for(s) == "series_xyz"
    assert ls.episode_id_for(s) == "script_abc"


def test_ensure_episode_id_rewrites_to_script_id():
    s = _script("script_rew")
    doc = {
        "episodes": [
            {"episode_id": "ep01", "episode_no": 1, "beat_ids": ["b1"]},
            {"episode_id": "ep02", "episode_no": 2, "beat_ids": ["b2"]},
        ]
    }
    eid = ls._ensure_episode_id(doc, s)
    assert eid == "script_rew"
    assert doc["episodes"][0]["episode_id"] == "script_rew"


def test_bridge_writes_timeline_and_syncs_frames(tmp_path, monkeypatch):
    # Keep artifacts under repo output/ so /files mount path layout stays realistic.
    s = _script("app_ep_bridge")
    raw = json.loads(Path("contracts/examples/beats.example.json").read_text(encoding="utf-8"))
    raw["work_id"] = s.id
    if not raw.get("episodes"):
        keep = [
            b["beat_id"]
            for b in raw["beats"]
            if b.get("decision") in ("keep", "compress")
        ]
        raw["episodes"] = [
            {"episode_id": "ep01", "episode_no": 1, "beat_ids": keep, "title": "e1"}
        ]
    raw["phase"] = "selected"
    save_beats(ls._bpath(s), raw)

    status, updated = ls.bridge_to_timeline(s, budget_usd=3.0)
    assert status["has_timeline"] is True
    assert status["timeline_phase"] == "draft"
    tpath = Path("output") / s.id / "timeline.json"
    assert tpath.is_file()
    tl = load_timeline(tpath)
    assert tl["episode_id"] == s.id
    assert updated.frames and len(updated.frames) == len(tl["shots"])
    # Duration fields on frames come from timeline when present (draft may be null)
    assert all(f.id for f in updated.frames)

    st = ls.public_status(updated)
    assert st["has_beats"] and st["beats_phase"] in {"selected", "delivered"}
    assert st["episode_id"] == s.id
