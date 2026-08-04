from __future__ import annotations

import sys
import types

from src.models.factory import ModelFactory
from src.utils.provider_registry import resolve_provider_backend


def _stub_dashscope():
    if "dashscope" not in sys.modules:
        sys.modules["dashscope"] = types.ModuleType("dashscope")


def test_resolve_backend_is_xai_not_dashscope():
    assert resolve_provider_backend("grok-imagine-video-1.5") == "xai"
    assert resolve_provider_backend("grok-imagine-video-1.5-i2v") == "xai"


def test_pipeline_source_forbids_grok_dashscope_fallback():
    """Source-level guard: grok must not fall through to silent dashscope default."""
    from src.luoxia.paths import REPO_ROOT

    src = (REPO_ROOT / "src" / "apps" / "comic_gen" / "pipeline.py").read_text(encoding="utf-8")
    assert 'startswith("grok-imagine-video")' in src
    assert "expected backend 'xai'" in src



def test_factory_creates_grok_adapter():
    model = ModelFactory.create_model(
        {"model.name": "grok-imagine-video-1.5", "model": {"params": {"model_name": "grok-imagine-video-1.5"}}}
    )
    assert model.__class__.__name__ == "GrokVideoModel"


def test_local_still_becomes_data_uri(tmp_path):
    from src.models.grok import _coerce_image_url

    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    url = _coerce_image_url(str(png))
    assert url.startswith("data:image/png;base64,")
    assert _coerce_image_url("https://example.com/a.png") == "https://example.com/a.png"


def test_missing_still_refuses_instead_of_dropping_to_t2v(tmp_path):
    """A shot that asked for i2v must not be quietly generated from text alone."""
    import pytest

    from src.models.grok import _coerce_image_url

    with pytest.raises(FileNotFoundError, match="refusing to fall back"):
        _coerce_image_url(str(tmp_path / "missing.png"))


def test_pricing_registered_via_adapter():
    from src.luoxia.pricing import get_pricing

    per_s, per_img = get_pricing("xai")("720p")
    assert per_s == 0.07
    assert per_img == 0.002
