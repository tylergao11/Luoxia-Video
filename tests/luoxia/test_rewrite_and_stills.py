from __future__ import annotations

from pathlib import Path

import pytest

from src.luoxia.llm.client import parse_json_object
from src.luoxia.render.still_hold import render_still_hold_videos
from src.luoxia.rewrite import make_rewrite_fn
from src.luoxia.stills.prompts import polish_timeline_prompts
from src.luoxia.stills.sizing import size_for_aspect


def test_parse_json_object_fenced():
    data = parse_json_object('这里是前言\n```json\n{"a": 1}\n```\n尾巴')
    assert data == {"a": 1}


def test_local_rewrite_without_llm(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rewrite = make_rewrite_fn()
    out = rewrite("我在这里等了整整三年。这三年里的每一天我都在想同一个问题。", 2.0, {"dialogue": {}})
    assert len(out) < 40
    assert out


def test_size_for_aspect():
    assert size_for_aspect("9:16") == "720*1280"
    assert size_for_aspect("16:9") == "1280*720"


def test_polish_prompts_offline(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    tl = {
        "global": {"aspect_ratio": "9:16"},
        "cast": [{"character_id": "a", "display_name": "A"}],
        "shots": [
            {
                "shot_id": "s1",
                "type": "dialogue",
                "shot_size": "close_up",
                "scene_id": "room",
                "dialogue": {"text": "你好"},
                "still": {},
                "video": {"request": {}},
            }
        ],
    }
    polish_timeline_prompts(tl)
    assert tl["shots"][0]["still"]["prompt"]
    assert tl["shots"][0]["video"]["request"]["prompt"]


def test_still_hold_requires_still(tmp_path):
    from src.utils.system_check import get_ffmpeg_path

    if not get_ffmpeg_path():
        pytest.skip("ffmpeg not installed")
    tl = {
        "phase": "frozen",
        "timeline_hash": "sha256:x",
        "shots": [
            {
                "shot_id": "s1",
                "timing": {"request_duration_s": 2, "target_duration_s": 2.0},
                "still": {"local_path": str(tmp_path / "missing.png")},
                "video": {},
            }
        ],
    }
    with pytest.raises(FileNotFoundError):
        render_still_hold_videos(tl, output_root=tmp_path)


def test_still_hold_ffmpeg(tmp_path):
    from src.utils.system_check import get_ffmpeg_path

    if not get_ffmpeg_path():
        pytest.skip("ffmpeg not installed")

    # Tiny valid PNG
    png = tmp_path / "s1.png"
    png.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
    )
    tl = {
        "phase": "frozen",
        "timeline_hash": "sha256:x",
        "shots": [
            {
                "shot_id": "s1",
                "timing": {"request_duration_s": 1, "target_duration_s": 1.0},
                "still": {"local_path": str(png)},
                "video": {},
            }
        ],
    }
    render_still_hold_videos(tl, output_root=tmp_path)
    assert Path(tl["shots"][0]["video"]["local_path"]).is_file()
    assert tl["shots"][0]["video"]["provider"] == "still_hold"
