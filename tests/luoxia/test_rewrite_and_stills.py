from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.luoxia.llm.client import parse_json_object
from src.luoxia.rewrite import make_rewrite_fn
from src.luoxia.stills.prompts import polish_timeline_prompts


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


def test_unsupported_aspect_ratio_is_rejected():
    """xAI takes an aspect_ratio enum, not pixels; an unknown one must not become 'auto'."""
    from src.models.xai_image import XaiImageError, XaiImageModel

    with pytest.raises(XaiImageError, match="aspect_ratio"):
        XaiImageModel().generate("x", "out.png", aspect_ratio="1280*720")


def test_negative_prompt_survives_into_the_prompt():
    """The API has no negative-prompt field, so dropping it would lose it silently."""
    from src.models.xai_image import compose_prompt

    text = compose_prompt("一间落满灰尘的旧屋", negative_prompt="文字，水印")
    assert "一间落满灰尘的旧屋" in text
    assert "文字，水印" in text


def test_multiple_references_are_named_positionally():
    """With several sources the API addresses them as <IMAGE_n>, so the prompt must map
    each index to a character or the model swaps their faces."""
    from src.models.xai_image import compose_prompt

    text = compose_prompt(
        "两人对峙",
        ref_images=[
            {"display_name": "林晚", "path": "a.png"},
            {"display_name": "沈策", "path": "b.png"},
        ],
    )
    assert "<IMAGE_0> 是林晚" in text
    assert "<IMAGE_1> 是沈策" in text


def test_single_reference_needs_no_index():
    from src.models.xai_image import compose_prompt

    text = compose_prompt("特写", ref_images=[{"display_name": "林晚", "path": "a.png"}])
    assert "林晚" in text
    assert "<IMAGE_" not in text


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


class _Status:
    def __init__(self, mode, signed_in, message="", provider="xai_pool"):
        self.mode = mode
        self.provider = provider
        self.signed_in = signed_in
        self.message = message


def _patch_status(monkeypatch, status):
    import src.auth.resolver as resolver

    monkeypatch.setattr(resolver, "status", lambda: status)


def test_session_login_counts_as_video_credentials(monkeypatch):
    """The bug this replaces: the gate read XAI_API_KEY and ignored session auth, so a
    signed-in pool user was silently downgraded to a slideshow."""
    from src.luoxia.pipeline import assert_video_credentials

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    _patch_status(monkeypatch, _Status("session", True, "Signed in via Grok"))

    assert_video_credentials()  # must not raise


def test_missing_credentials_refuse_instead_of_degrading(monkeypatch):
    from src.luoxia.pipeline import assert_video_credentials

    _patch_status(monkeypatch, _Status("session", False, "Need login for subscription pool"))
    with pytest.raises(RuntimeError, match="no video credential resolved"):
        assert_video_credentials()


def test_offline_mode_refuses_with_actionable_message(monkeypatch):
    from src.luoxia.pipeline import assert_video_credentials

    _patch_status(monkeypatch, _Status("offline", False))
    with pytest.raises(RuntimeError, match="offline"):
        assert_video_credentials()


def test_env_is_loaded_for_luoxia_entrypoints(tmp_path, monkeypatch):
    """TTS reads DASHSCOPE_API_KEY from the environment; nothing else in the package
    loads .env, which is how a whole episode once got a generated tone instead of speech."""
    from src.luoxia.env import load_env_once

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "# commented out because this account uses session auth",
                "# XAI_API_KEY = sk-should-not-load",
                "DASHSCOPE_API_KEY=sk-from-file",
                'QUOTED_KEY="with spaces"',
                "export EXPORTED_KEY=exported",
                "ALREADY_SET=from-file",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("ALREADY_SET", "from-environment")

    load_env_once(root=tmp_path)

    assert os.getenv("DASHSCOPE_API_KEY") == "sk-from-file"
    assert os.getenv("QUOTED_KEY") == "with spaces"
    assert os.getenv("EXPORTED_KEY") == "exported"
    # A commented-out key must stay unset: that is what distinguishes "uses session auth"
    # from "has no credentials".
    assert os.getenv("XAI_API_KEY") is None
    # An explicit export still wins over the file.
    assert os.getenv("ALREADY_SET") == "from-environment"


def test_repo_env_actually_exposes_the_tts_key():
    """Guards the real checkout: .env has the DashScope key, so a run must not be blind."""
    from src.luoxia.env import parse_env_file

    repo_env = Path(__file__).resolve().parents[2] / ".env"
    if not repo_env.is_file():
        pytest.skip("no .env in this checkout")
    assert parse_env_file(repo_env).get("DASHSCOPE_API_KEY")
