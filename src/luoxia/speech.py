"""The single Luoxia dialogue-to-audio boundary.

Every entry point (pipeline, CLI and desktop API) must use this factory so provider,
performance compilation, cache identity and output layout cannot drift apart.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from src.audio.qwen3_tts import Qwen3TTS
from src.audio.xai_tts import XaiTTS


def make_tts_synthesize(
    episode_dir: Path,
    timeline: Dict[str, Any],
    *,
    tts: Optional[Any] = None,
):
    cast_voices = {
        item.get("character_id"): item.get("voice_id")
        for item in (timeline.get("cast") or [])
        if item.get("character_id")
    }
    engines: Dict[str, Any] = {}

    def resolve_engine(provider: str):
        if tts is not None:
            return tts
        if provider not in engines:
            engines[provider] = Qwen3TTS() if provider == "qwen3" else XaiTTS()
        return engines[provider]

    def synthesize(shot: Dict[str, Any], speed: float):
        dialogue = shot.get("dialogue") or {}
        audio = shot.setdefault("audio", {})
        text = dialogue.get("text") or ""
        voice = audio.get("voice_id") or cast_voices.get(dialogue.get("character_id"))
        if not voice:
            raise ValueError(f"{shot.get('shot_id')}: no voice_id in audio or cast")

        declared_provider = str(
            audio.get("provider") or os.getenv("LUOXIA_TTS_PROVIDER") or "qwen3"
        ).strip().lower()
        aliases = {
            "qwen3.tts": "qwen3",
            "qwen3-tts": "qwen3",
            "xai.tts": "xai",
            "xai-tts": "xai",
        }
        provider = aliases.get(declared_provider, declared_provider)
        if provider not in {"qwen3", "xai"}:
            raise ValueError(
                f"{shot.get('shot_id')}: timeline requests audio provider "
                f"{declared_provider!r}; supported providers are qwen3 and xai"
            )
        audio["provider"] = provider
        engine = resolve_engine(provider)

        out = Path(episode_dir) / "audio" / f"{shot['shot_id']}.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        path, measured, digest = engine.synthesize_measured(
            text=text,
            output_path=str(out),
            voice=voice,
            speech_rate=speed,
            instructions=dialogue.get("emotion"),
            performance=dialogue.get("performance"),
            take_id=audio.get("take_id"),
        )
        return measured, path, digest

    return synthesize
