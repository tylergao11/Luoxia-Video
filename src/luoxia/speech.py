"""The single Luoxia dialogue-to-audio boundary.

Every entry point (pipeline, CLI and desktop API) must use this factory so provider,
performance compilation, cache identity and output layout cannot drift apart.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.audio.doubao_tts import DoubaoTTS, VOICES as DOUBAO_VOICES
from src.audio.doubao_tts import voices_for_gender as doubao_voices_for_gender
from src.audio.qwen3_tts import Qwen3TTS, VOICE_PROFILES as QWEN3_VOICES
from src.audio.qwen3_tts import voices_for_gender as qwen3_voices_for_gender
from src.audio.xai_tts import XaiTTS, VOICES as XAI_VOICES
from src.audio.xai_tts import voices_for_gender as xai_voices_for_gender


_PROVIDER_ALIASES = {
    "doubao": "doubao",
    "doubao.tts": "doubao",
    "doubao-tts": "doubao",
    "seed-tts-2.0": "doubao",
    "volcengine": "doubao",
    "qwen3": "qwen3",
    "qwen3.tts": "qwen3",
    "qwen3-tts": "qwen3",
    "xai": "xai",
    "xai.tts": "xai",
    "xai-tts": "xai",
}


def configured_tts_provider() -> str:
    """Return the one configured Luoxia speech provider; Doubao is the product default."""
    declared = (os.getenv("LUOXIA_TTS_PROVIDER") or "doubao").strip().lower()
    provider = _PROVIDER_ALIASES.get(declared)
    if provider is None:
        raise ValueError(
            f"unsupported LUOXIA_TTS_PROVIDER {declared!r}; choose doubao, xai or qwen3"
        )
    return provider


def provider_for_voice(voice_id: Optional[str], declared: Optional[str] = None) -> str:
    """Resolve a provider without allowing its declaration and voice catalog to disagree."""
    voice = (voice_id or "").strip()
    inferred = (
        "doubao"
        if voice in DOUBAO_VOICES
        else "xai"
        if voice in XAI_VOICES
        else "qwen3"
        if voice in QWEN3_VOICES
        else None
    )
    if declared:
        provider = _PROVIDER_ALIASES.get(str(declared).strip().lower())
        if provider is None:
            raise ValueError(
                f"unsupported TTS provider {declared!r}; choose doubao, xai or qwen3"
            )
        if inferred and inferred != provider:
            raise ValueError(
                f"voice_id {voice!r} belongs to {inferred}, but the timeline declares {provider}"
            )
        return provider
    return inferred or configured_tts_provider()


def voices_for_gender(gender: Optional[str]) -> List[str]:
    """Casting catalog owned by the same provider selection used for synthesis."""
    provider = configured_tts_provider()
    if provider == "doubao":
        return doubao_voices_for_gender(gender)
    if provider == "qwen3":
        return qwen3_voices_for_gender(gender)
    return xai_voices_for_gender(gender)


def make_tts_synthesize(
    episode_dir: Path,
    timeline: Dict[str, Any],
    *,
    tts: Optional[Any] = None,
):
    cast_audio = {
        item.get("character_id"): item
        for item in (timeline.get("cast") or [])
        if item.get("character_id")
    }
    engines: Dict[str, Any] = {}

    def resolve_engine(provider: str):
        if tts is not None:
            return tts
        if provider not in engines:
            if provider == "doubao":
                engines[provider] = DoubaoTTS()
            elif provider == "qwen3":
                engines[provider] = Qwen3TTS()
            else:
                engines[provider] = XaiTTS()
        return engines[provider]

    def synthesize(shot: Dict[str, Any], speed: float):
        dialogue = shot.get("dialogue") or {}
        audio = shot.setdefault("audio", {})
        text = dialogue.get("text") or ""
        cast_entry = cast_audio.get(dialogue.get("character_id")) or {}
        voice = audio.get("voice_id") or cast_entry.get("voice_id")
        if not voice:
            raise ValueError(f"{shot.get('shot_id')}: no voice_id in audio or cast")

        declared_provider = audio.get("provider") or cast_entry.get("tts_provider")
        provider = provider_for_voice(voice, declared_provider)
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
