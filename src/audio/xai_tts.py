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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_API_BASE = "https://api.x.ai/v1"
_DEFAULT_SAMPLE_RATE = 44100

# Built-in voices from GET /v1/tts/voices. All are multilingual; gender is what casting
# needs, so that is what we keep. Custom cloned voice ids are accepted too (see
# POST /v1/custom-voices), which is why an unknown id is only rejected when it looks like
# a stale voice from another vendor.
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

# Tags wrap the whole line and shape delivery.
STYLE_TAGS = (
    "soft", "whisper", "loud", "build-intensity", "decrease-intensity",
    "higher-pitch", "lower-pitch", "slow", "fast", "sing-song", "singing",
    "laugh-speak", "emphasis",
)
# Tags are discrete events placed in the text.
INLINE_TAGS = (
    "pause", "long-pause", "hum-tune", "laugh", "chuckle", "giggle", "cry", "tsk",
    "tongue-click", "lip-smack", "breath", "inhale", "exhale", "sigh",
)

# beats writes dialogue.emotion as free Chinese prose, so match on keywords. Order matters:
# the first hit wins, so put the specific readings before the general ones.
_STYLE_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("whisper", ("耳语", "低语", "悄声", "轻声", "压低声音", "声音很轻", "声音极轻")),
    ("laugh-speak", ("笑着说", "带笑", "笑道")),
    ("loud", ("大声", "怒吼", "咆哮", "厉声", "嘶喊", "喊道", "吼")),
    ("build-intensity", ("渐强", "越来越激动", "情绪上涨")),
    ("decrease-intensity", ("渐弱", "平息", "声音低下去")),
    ("higher-pitch", ("尖锐", "拔高", "兴奋")),
    ("lower-pitch", ("低沉", "沉声", "压抑")),
    ("slow", ("缓慢", "迟疑", "犹豫", "一字一句")),
    ("fast", ("急促", "焦急", "慌乱", "飞快")),
    ("emphasis", ("强调", "加重", "咬字", "咬死", "咬牙", "字字", "恨意", "决绝")),
    ("soft", ("温柔", "柔和", "轻柔", "平静", "淡淡", "很轻", "克制")),
)
_INLINE_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("sigh", ("叹息", "叹气", "长叹")),
    ("cry", ("哭", "哽咽", "泣")),
    ("chuckle", ("轻笑", "低笑", "冷笑")),
    ("breath", ("喘", "气息不稳")),
    ("inhale", ("深吸", "吸气")),
    ("long-pause", ("长久沉默", "久久")),
    ("pause", ("停顿", "顿了", "沉默")),
)


def apply_emotion(text: str, emotion: Optional[str]) -> Tuple[str, List[str]]:
    """Turn `dialogue.emotion` prose into xAI speech tags.

    Returns the tagged text and the tags applied. An emotion that matches nothing comes
    back with an empty list so the caller can say so instead of pretending it was used.
    """
    note = (emotion or "").strip()
    if not note:
        return text, []

    applied: List[str] = []
    body = text
    for tag, keywords in _INLINE_KEYWORDS:
        if any(k in note for k in keywords):
            body = f"[{tag}]{body}"
            applied.append(f"[{tag}]")
            break
    for tag, keywords in _STYLE_KEYWORDS:
        if any(k in note for k in keywords):
            body = f"<{tag}>{body}</{tag}>"
            applied.append(f"<{tag}>")
            break
    return body, applied


def voices_for_gender(gender: Optional[str]) -> List[str]:
    """Built-in voice ids matching a gender, stable order so casting is reproducible."""
    want = (gender or "").strip().lower()
    if want in {"female", "女", "f"}:
        want = "female"
    elif want in {"male", "男", "m"}:
        want = "male"
    else:
        return sorted(VOICES)
    return sorted(v for v, meta in VOICES.items() if meta["gender"] == want)


class XaiTTS:
    """Synthesizes one line at a time and measures the result from the written file."""

    def __init__(
        self,
        *,
        language: str = "zh",
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_s: float = 120.0,
    ):
        self.language = language
        self.sample_rate = int(sample_rate)
        self.timeout_s = float(timeout_s)
        self._token = (token or "").strip() or None
        self._base_url = (base_url or "").rstrip("/") or None

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

    def content_sha256(self, text: str, voice_id: str, speed: float) -> str:
        """Content hash for idempotent TTS. Includes vendor and language so a re-render
        after switching providers or languages cannot silently reuse the old take."""
        payload = f"xai\0{self.language}\0{text}\0{voice_id}\0{float(speed):.6f}".encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def synthesize_measured(
        self,
        text: str,
        output_path: str,
        voice: Optional[str] = None,
        speech_rate: float = 1.0,
        instructions: Optional[str] = None,
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

        digest = self.content_sha256(line, voice_id, speech_rate)
        meta_path = Path(str(output_path) + ".sha256")
        out = Path(output_path)
        if out.is_file() and meta_path.is_file():
            if meta_path.read_text(encoding="utf-8").strip() == digest:
                measured = measure_media_duration_s(out)
                logger.info("xai tts cache hit %s (%.3fs)", out, measured)
                return str(out), measured, digest

        tagged, applied = apply_emotion(line, instructions)
        if instructions and not applied:
            logger.warning(
                "emotion %r matched no xai speech tag; line delivered without it: %s",
                instructions, line[:20],
            )

        payload = self._request(tagged, voice_id, speech_rate)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(payload["audio"]))

        measured = measure_media_duration_s(out)
        if measured <= 0:
            raise RuntimeError(f"xai tts wrote an unplayable file: {out}")

        timings = payload.get("audio_timestamps")
        if timings:
            # Keep what we already paid for: per-character times let subtitle cues be
            # placed on real timings instead of estimated splits, without re-synthesizing.
            Path(str(output_path) + ".timings.json").write_text(
                json.dumps(timings, ensure_ascii=False), encoding="utf-8", newline="\n"
            )
        meta_path.write_text(digest + "\n", encoding="utf-8", newline="\n")
        logger.info(
            "xai tts %s voice=%s speed=%.2f tags=%s -> %.3fs",
            out.name, voice_id, speech_rate, ",".join(applied) or "-", measured,
        )
        return str(out), measured, digest

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
