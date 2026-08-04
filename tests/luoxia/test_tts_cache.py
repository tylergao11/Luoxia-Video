from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.audio.tts import TTSProcessor


def test_content_sha256_stable():
    a = TTSProcessor.content_sha256("你好", "longxiaochun", 1.0)
    b = TTSProcessor.content_sha256("你好", "longxiaochun", 1.0)
    c = TTSProcessor.content_sha256("你好", "longxiaochun", 1.1)
    assert a == b
    assert a != c
    assert a.startswith("sha256:")


def test_synthesize_measured_cache_hit(tmp_path):
    out = tmp_path / "a.wav"
    out.write_bytes(b"RIFF....WAVE")  # placeholder; probe mocked
    digest = TTSProcessor.content_sha256("hi", "longxiaochun", 1.0)
    (tmp_path / "a.wav.sha256").write_text(digest + "\n", encoding="utf-8")

    proc = TTSProcessor.__new__(TTSProcessor)
    proc.voice = "longxiaochun"

    def boom(*args, **kwargs):
        raise AssertionError("synthesize must not be called on cache hit")

    with patch.object(TTSProcessor, "synthesize", boom), patch(
        "src.luoxia.media.ffprobe.measure_media_duration_s", return_value=1.23
    ):
        path, measured, got = TTSProcessor.synthesize_measured(
            proc, "hi", str(out), voice="longxiaochun", speech_rate=1.0
        )
    assert measured == 1.23
    assert got == digest
    assert path == str(out)
