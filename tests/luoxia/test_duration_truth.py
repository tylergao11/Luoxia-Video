from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from src.luoxia.paths import REPO_ROOT
from src.luoxia.render.duration import require_request_duration
from src.luoxia.timeline.io import load_timeline


def test_require_request_duration_from_timeline_only():
    tl = {
        "shots": [
            {
                "shot_id": "s1",
                "timing": {"request_duration_s": 7, "target_duration_s": 6.2},
            }
        ]
    }
    assert require_request_duration(tl, "s1") == 7
    with pytest.raises(KeyError):
        require_request_duration(tl, "missing")


def test_no_default_duration_in_create_video_task_signature():
    """Inspect source AST — avoid importing ComicGenPipeline (heavy optional deps)."""
    src = (REPO_ROOT / "src" / "apps" / "comic_gen" / "pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ComicGenPipeline":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "create_video_task":
                    fn = item
                    break
    assert fn is not None
    duration_arg = next(a for a in fn.args.args if a.arg == "duration")
    # defaults align to the end of args
    defaults = fn.args.defaults
    positional = fn.args.args[len(fn.args.args) - len(defaults) :]
    default_map = {a.arg: d for a, d in zip(positional, defaults)}
    assert "duration" in default_map
    assert isinstance(default_map["duration"], ast.Constant)
    assert default_map["duration"].value is None


def test_timeline_duration_lookup_path(tmp_path, monkeypatch):
    tl = {
        "shots": [
            {
                "shot_id": "frame_a",
                "timing": {
                    "request_duration_s": 9,
                    "target_duration_s": 8.2,
                    "start_s": 0,
                    "end_s": 8.2,
                    "slack_s": 0.8,
                    "trim": {"strategy": "tail", "head_s": 0, "tail_s": 0.8},
                },
            }
        ]
    }
    out = tmp_path / "output" / "proj1"
    out.mkdir(parents=True)
    (out / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    loaded = load_timeline(out / "timeline.json")
    assert require_request_duration(loaded, "frame_a") == 9
