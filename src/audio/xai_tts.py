"""Text-to-speech via the xAI Voice API (`POST /v1/tts`).

Chinese is a first-class language here (`language="zh"`), the response reports its own
duration, and `with_timestamps` returns per-character timings — for Chinese that means one
entry per 汉字, which is what subtitle cue timing wants.

Docs: https://docs.x.ai/developers/rest-api-reference/inference/voice
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.audio.performance import (
    SPEECH_RENDER_CONTRACT,
    clean_audio_timestamps,
    compile_performance,
    direction_is_neutral,
)

logger = logging.getLogger(__name__)

_API_BASE = "https://api.x.ai/v1"
_DEFAULT_SAMPLE_RATE = 44100
_DEFAULT_PRODUCT_SPEED = 0.88
_XAI_MIN_SPEED = 0.7
_XAI_MAX_SPEED = 1.5
_CONTROL_PADDING_THRESHOLD_S = 0.70
_NATURAL_HEAD_PAD_S = 0.28
_NATURAL_TAIL_PAD_S = 0.36

# Built-in voices returned by the live REST `GET /v1/tts/voices` catalog. All are
# multilingual; custom cloned voice ids are accepted too, which is why an unknown id is
# only rejected when it looks like a stale voice from another vendor.
VOICES: Dict[str, Dict[str, str]] = {
    "altair": {"gender": "male"},
    "ara": {"gender": "female"},
    "atlas": {"gender": "male"},
    "carina": {"gender": "female"},
    "castor": {"gender": "male"},
    "celeste": {"gender": "female"},
    "cosmo": {"gender": "male"},
    "eve": {"gender": "female"},
    "helios": {"gender": "male"},
    "helix": {"gender": "male"},
    "iris": {"gender": "female"},
    "kepler": {"gender": "male"},
    "leo": {"gender": "male"},
    "lumen": {"gender": "male"},
    "luna": {"gender": "female"},
    "lux": {"gender": "male"},
    "naksh": {"gender": "male"},
    "orion": {"gender": "male"},
    "perseus": {"gender": "male"},
    "rex": {"gender": "male"},
    "rigel": {"gender": "male"},
    "sal": {"gender": "male"},
    "sirius": {"gender": "male"},
    "ursa": {"gender": "female"},
    "zagan": {"gender": "male"},
    "zenith": {"gender": "male"},
}

# Front-load voices that produced the clearest Mandarin dramatic contrast in the local
# casting pass (quiet opening -> rising confrontation -> restrained beat -> hard close).
# The remainder still stay available in stable alphabetical order.
_PREFERRED_BY_GENDER = {
    "female": ("iris", "celeste", "ara", "eve", "carina", "ursa", "luna"),
    "male": ("orion", "leo", "rex", "sirius", "atlas", "zagan"),
}

def apply_emotion(text: str, emotion: Optional[str]) -> Tuple[str, List[str]]:
    """Compatibility wrapper for callers that still provide free-form direction."""
    compiled, applied, _plan = compile_performance(text, legacy_direction=emotion)
    return compiled, applied


def voices_for_gender(gender: Optional[str]) -> List[str]:
    """Built-in voice ids matching a gender, stable order so casting is reproducible."""
    want = (gender or "").strip().lower()
    if want in {"female", "女", "f"}:
        want = "female"
    elif want in {"male", "男", "m"}:
        want = "male"
    else:
        return sorted(VOICES)
    available = {voice_id for voice_id, meta in VOICES.items() if meta["gender"] == want}
    preferred = [voice_id for voice_id in _PREFERRED_BY_GENDER.get(want, ()) if voice_id in available]
    return preferred + sorted(available.difference(preferred))


def voice_records() -> List[Dict[str, Any]]:
    """Frontend-ready system catalog backed by the same ids synthesis validates."""
    return [
        {
            "id": voice_id,
            "name": voice_id.title(),
            "gender": "Female" if meta["gender"] == "female" else "Male",
            "model": "xai-tts",
            "family": "xai",
            "supports_instruction": True,
            "dialect": None,
            "lang_primary": "multilingual",
            "origin": "system",
        }
        for voice_id, meta in sorted(VOICES.items())
    ]


class XaiTTS:
    """Synthesizes one line at a time and measures the result from the written file."""

    def __init__(
        self,
        *,
        language: str = "zh",
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        product_speed: Optional[float] = None,
        timeout_s: float = 120.0,
    ):
        self.language = language
        self.sample_rate = int(sample_rate)
        self.timeout_s = float(timeout_s)
        self._token = (token or "").strip() or None
        self._base_url = (base_url or "").rstrip("/") or None
        configured_speed = product_speed if product_speed is not None else os.getenv("LUOXIA_XAI_BASE_SPEED")
        self.product_speed = float(configured_speed or _DEFAULT_PRODUCT_SPEED)
        if not _XAI_MIN_SPEED <= self.product_speed <= 1.0:
            raise ValueError("LUOXIA_XAI_BASE_SPEED must be in [0.7, 1.0]")

    def _headers(self) -> Dict[str, str]:
        if not self._token:
            from src.models.grok import resolve_xai_token

            token, _kind, base = resolve_xai_token("tts")
            self._token = token
            if base and not self._base_url:
                self._base_url = base.rstrip("/")
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    @property
    def base_url(self) -> str:
        return self._base_url or os.getenv("XAI_BASE_URL", _API_BASE).rstrip("/")

    def content_sha256(
        self,
        request_text: str,
        voice_id: str,
        speed: float,
        take_id: Optional[str] = None,
    ) -> str:
        """Hash every input that can change the bytes returned by xAI.

        `request_text` is the compiled marked text, not only the clean subtitle.  That
        makes a changed performance plan invalidate the old take.  `take_id` is an
        explicit cache buster for deliberate A/B takes because xAI exposes no seed.
        """
        payload = (
            f"{SPEECH_RENDER_CONTRACT}:xai\0{self.language}\0{self.sample_rate}\0{request_text}\0"
            f"{voice_id}\0{float(speed):.6f}\0{take_id or ''}"
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def synthesize_measured(
        self,
        text: str,
        output_path: str,
        voice: Optional[str] = None,
        speech_rate: float = 1.0,
        instructions: Optional[str] = None,
        performance: Optional[Dict[str, Any]] = None,
        take_id: Optional[str] = None,
        **_ignored: Any,
    ) -> Tuple[str, float, str]:
        """Synthesize one line; return (path, measured_duration_s, content_sha256).

        Duration is probed from the written file, never taken from the API, because the
        timeline's duration authority has to be the bytes we actually concatenate.
        """
        from src.luoxia.media.ffprobe import measure_media_duration_s

        line = (text or "").strip()
        if not line:
            raise ValueError("refusing to synthesize an empty line")
        voice_id = self._resolve_voice(voice)

        tagged, applied, effective_plan = compile_performance(
            line,
            performance=performance,
            legacy_direction=instructions,
        )

        provider_speed = self._provider_speed(speech_rate)
        digest = self.content_sha256(tagged, voice_id, provider_speed, take_id)
        meta_path = Path(str(output_path) + ".sha256")
        out = Path(output_path)
        if out.suffix.lower() != ".wav":
            raise ValueError("xAI TTS returns WAV; output_path must end in .wav")
        if out.is_file() and meta_path.is_file():
            if meta_path.read_text(encoding="utf-8").strip() == digest:
                measured = measure_media_duration_s(out)
                logger.info("xai tts cache hit %s (%.3fs)", out, measured)
                return str(out), measured, digest

        if (instructions or performance) and not applied and not direction_is_neutral(instructions):
            logger.warning(
                "performance direction produced no xai speech tag; line delivered plain: %s",
                line[:20],
            )

        payload = self._request(tagged, voice_id, provider_speed)
        cleaned = clean_audio_timestamps(payload.get("audio_timestamps"), line)
        measured, trim_start = self._write_audio(
            out,
            base64.b64decode(payload["audio"]),
            cleaned,
            effective_plan,
            measure_media_duration_s,
        )

        if cleaned is not None:
            # xAI includes every control-tag character in graph_chars.  Persist only an
            # exact alignment to the clean subtitle text; untrusted timing is worse than
            # falling back to the timeline's measured line boundary.
            timing_path = Path(str(output_path) + ".timings.json")
            if trim_start > 0:
                cleaned["graph_times"] = [
                    [max(0.0, float(pair[0]) - trim_start), max(0.0, float(pair[1]) - trim_start)]
                    for pair in cleaned["graph_times"]
                ]
            timing_path.write_text(
                json.dumps(cleaned, ensure_ascii=False), encoding="utf-8", newline="\n"
            )
        elif payload.get("audio_timestamps"):
            Path(str(output_path) + ".timings.json").unlink(missing_ok=True)
            logger.warning("xai timestamps could not align to clean text: %s", out.name)
        meta_path.write_text(digest + "\n", encoding="utf-8", newline="\n")
        logger.info(
            "xai tts %s voice=%s requested_speed=%.2f provider_speed=%.2f tags=%s plan=%s -> %.3fs",
            out.name,
            voice_id,
            speech_rate,
            provider_speed,
            ",".join(applied) or "-",
            (effective_plan or {}).get("intent") or "-",
            measured,
        )
        return str(out), measured, digest

    def _write_audio(
        self,
        out: Path,
        audio_bytes: bytes,
        cleaned_timings: Optional[Dict[str, Any]],
        performance: Optional[Dict[str, Any]],
        measure,
    ) -> Tuple[float, float]:
        """Atomically write a take and remove provider control-token padding.

        xAI's graph timeline assigns time to markup characters.  The old stacked-tag
        request spent almost ten seconds before the first Chinese character and another
        1.6 seconds after the last one.  When there is no deliberate event at position
        zero, keep a small natural pad around the actual spoken window and discard that
        control-token dead air before it can become timeline authority.  xAI returns
        PCM WAV, so trimming uses exact frame boundaries and has no FFmpeg dependency.
        """
        from src.luoxia.media.wav import trim_wav_file

        out.parent.mkdir(parents=True, exist_ok=True)
        raw_handle = tempfile.NamedTemporaryFile(
            prefix=f"{out.stem}.raw-", suffix=".wav", dir=out.parent, delete=False
        )
        raw_path = Path(raw_handle.name)
        trimmed_path: Optional[Path] = None
        try:
            raw_handle.write(audio_bytes)
            raw_handle.close()
            raw_duration = measure(raw_path)
            if raw_duration <= 0:
                raise RuntimeError(f"xai tts returned unplayable audio for {out}")

            trim_start = 0.0
            trim_end = raw_duration
            if cleaned_timings and cleaned_timings.get("graph_times"):
                first = float(cleaned_timings["graph_times"][0][0])
                last = float(cleaned_timings["graph_times"][-1][1])
                leading_event = any(
                    int(segment.get("start_char") or 0) == 0 and segment.get("event_before")
                    for segment in ((performance or {}).get("segments") or [])
                )
                if not leading_event and first > _CONTROL_PADDING_THRESHOLD_S:
                    trim_start = max(0.0, first - _NATURAL_HEAD_PAD_S)
                if raw_duration - last > _CONTROL_PADDING_THRESHOLD_S:
                    trim_end = min(raw_duration, last + _NATURAL_TAIL_PAD_S)

            candidate = raw_path
            if trim_start > 0.02 or trim_end < raw_duration - 0.02:
                trim_handle = tempfile.NamedTemporaryFile(
                    prefix=f"{out.stem}.trim-", suffix=".wav", dir=out.parent, delete=False
                )
                trimmed_path = Path(trim_handle.name)
                trim_handle.close()
                trim_start = trim_wav_file(
                    raw_path,
                    trimmed_path,
                    start_s=trim_start,
                    end_s=trim_end,
                )
                candidate = trimmed_path
                logger.info(
                    "xai tts trimmed control padding head=%.3fs tail=%.3fs",
                    trim_start,
                    raw_duration - trim_end,
                )

            measured = measure(candidate)
            if measured <= 0:
                raise RuntimeError(f"xai tts wrote an unplayable file: {candidate}")
            candidate.replace(out)
            return measured, trim_start
        finally:
            if not raw_handle.closed:
                raw_handle.close()
            raw_path.unlink(missing_ok=True)
            if trimmed_path is not None:
                trimmed_path.unlink(missing_ok=True)

    def _resolve_voice(self, voice: Optional[str]) -> str:
        """Reject voices this API does not know.

        The API silently falls back to `eve` for an omitted voice, so a stale CosyVoice id
        left in the cast would quietly recast every character as the same default woman.
        """
        voice_id = (voice or "").strip()
        if not voice_id:
            raise ValueError("no voice_id supplied; cast must assign one per character")
        if voice_id in VOICES:
            return voice_id
        if voice_id.startswith(("longxiaochun", "long", "cosyvoice")) or "_v2" in voice_id:
            raise ValueError(
                f"voice_id {voice_id!r} is a DashScope CosyVoice voice; xAI voices are "
                f"{', '.join(sorted(VOICES))}"
            )
        # Anything else may legitimately be a custom cloned voice id.
        logger.info("voice_id %r is not built-in; assuming a custom xai voice", voice_id)
        return voice_id

    def _provider_speed(self, requested_speed: float) -> float:
        """Translate product-relative pace to xAI's calibrated absolute multiplier."""
        requested = float(requested_speed)
        if requested <= 0:
            raise ValueError("speech_rate must be positive")
        calibrated = requested * self.product_speed
        clamped = max(_XAI_MIN_SPEED, min(_XAI_MAX_SPEED, calibrated))
        if abs(clamped - calibrated) > 1e-6:
            logger.warning(
                "xai speech speed %.3f calibrated to %.3f and clamped to %.3f",
                requested,
                calibrated,
                clamped,
            )
        return round(clamped, 3)

    def _request(self, text: str, voice_id: str, speed: float) -> Dict[str, Any]:
        import requests

        body: Dict[str, Any] = {
            "text": text,
            "voice_id": voice_id,
            "language": self.language,
            "output_format": {"codec": "wav", "sample_rate": self.sample_rate},
            "with_timestamps": True,
        }
        if abs(float(speed) - 1.0) > 1e-6:
            body["speed"] = round(float(speed), 3)

        resp = requests.post(
            f"{self.base_url}/tts", headers=self._headers(), json=body, timeout=self.timeout_s
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"xai tts failed HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        if not data.get("audio"):
            raise RuntimeError(f"xai tts returned no audio: {str(data)[:300]}")
        return data
