"""Doubao Seed-TTS 2.0 adapter for Chinese dramatic dialogue.

The adapter uses the current Volcengine speech-console API-key contract and the V3
HTTP Chunked endpoint.  Voice ids, resource ownership, prompt compilation, cache
identity and audio conversion all live here so callers cannot invent a second truth.

Docs:
https://www.volcengine.com/docs/6561/1598757?lang=zh
https://www.volcengine.com/docs/6561/1257544?lang=zh
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.audio.performance import (
    SPEECH_RENDER_CONTRACT,
    clean_audio_timestamps,
    normalize_performance,
    text_sha256,
)

logger = logging.getLogger(__name__)

_API_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
_RESOURCE_ID = "seed-tts-2.0"
_SAMPLE_RATE = 24000
_PCM_SAMPLE_BYTES = 2
_HEAD_ROOM_S = 0.03
_TAIL_ROOM_S = 0.04

# Curated from the current official Seed-TTS 2.0 catalog.  Keep this intentionally
# small: it is the product casting list, not a stale copy of every vendor voice.
VOICES: Dict[str, Dict[str, str]] = {
    "zh_male_qingcang_uranus_bigtts": {
        "name": "擎苍 2.0",
        "gender": "male",
        "scene": "角色扮演",
        "provenance": "番茄小说/豆包/抖音/剪映同款",
    },
    "zh_male_ruyaqingnian_uranus_bigtts": {
        "name": "儒雅青年 2.0",
        "gender": "male",
        "scene": "通用场景",
        "provenance": "番茄小说/豆包/剪映同款",
    },
    "saturn_zh_male_bujiqingnian_tob": {
        "name": "不羁青年 2.0",
        "gender": "male",
        "scene": "角色扮演",
        "provenance": "指令音色",
    },
    "saturn_zh_male_fengfashaonian_tob": {
        "name": "风发少年 2.0",
        "gender": "male",
        "scene": "角色扮演",
        "provenance": "指令音色",
    },
    "saturn_zh_male_aomanshaoye_tob": {
        "name": "傲慢少爷 2.0",
        "gender": "male",
        "scene": "角色扮演",
        "provenance": "指令音色",
    },
    "zh_male_shaonianzixin_uranus_bigtts": {
        "name": "少年梓辛 2.0",
        "gender": "male",
        "scene": "通用场景",
        "provenance": "Seed-TTS 2.0",
    },
    "zh_male_aojiaobazong_uranus_bigtts": {
        "name": "傲娇霸总 2.0",
        "gender": "male",
        "scene": "通用场景",
        "provenance": "Seed-TTS 2.0",
    },
    "zh_male_dayi_uranus_bigtts": {
        "name": "大壹 2.0",
        "gender": "male",
        "scene": "视频配音",
        "provenance": "Seed-TTS 2.0",
    },
    "zh_female_wenroushunv_uranus_bigtts": {
        "name": "温柔淑女 2.0",
        "gender": "female",
        "scene": "通用场景",
        "provenance": "番茄小说/豆包/剪映同款",
    },
    "zh_female_gaolengyujie_uranus_bigtts": {
        "name": "高冷御姐 2.0",
        "gender": "female",
        "scene": "通用场景",
        "provenance": "Seed-TTS 2.0",
    },
    "zh_female_meilinvyou_uranus_bigtts": {
        "name": "魅力女友 2.0",
        "gender": "female",
        "scene": "通用场景",
        "provenance": "Seed-TTS 2.0",
    },
    "zh_female_zhishuaiyingzi_uranus_bigtts": {
        "name": "直率英子 2.0",
        "gender": "female",
        "scene": "角色扮演",
        "provenance": "抖音/豆包/剪映同款",
    },
    "zh_female_vv_uranus_bigtts": {
        "name": "Vivi 2.0",
        "gender": "female",
        "scene": "通用场景",
        "provenance": "Seed-TTS 2.0",
    },
}

_PREFERRED_BY_GENDER = {
    "male": (
        "zh_male_qingcang_uranus_bigtts",
        "zh_male_ruyaqingnian_uranus_bigtts",
        "saturn_zh_male_bujiqingnian_tob",
        "saturn_zh_male_fengfashaonian_tob",
        "saturn_zh_male_aomanshaoye_tob",
        "zh_male_shaonianzixin_uranus_bigtts",
        "zh_male_aojiaobazong_uranus_bigtts",
        "zh_male_dayi_uranus_bigtts",
    ),
    "female": (
        "zh_female_wenroushunv_uranus_bigtts",
        "zh_female_gaolengyujie_uranus_bigtts",
        "zh_female_meilinvyou_uranus_bigtts",
        "zh_female_zhishuaiyingzi_uranus_bigtts",
        "zh_female_vv_uranus_bigtts",
    ),
}

_STYLE_DIRECTION = {
    "soft": "收住力度，像真实人物轻声说话",
    "whisper": "压低为近距离耳语",
    "loud": "提高力度，但不要喊破",
    "build-intensity": "情绪和气势逐步抬高",
    "decrease-intensity": "情绪逐渐收住",
    "higher-pitch": "音高自然上扬",
    "lower-pitch": "压低语气，但不要故作深沉",
    "slow": "自然放慢并留出呼吸",
    "fast": "加快语速但保持清楚",
    "sing-song": "带一点旋律感但仍是对白",
    "singing": "按歌唱语气表达",
    "laugh-speak": "带着自然笑意说",
    "emphasis": "自然加重重点，不要逐字顿开",
}

_EVENT_DIRECTION = {
    "pause": "说前短暂停顿",
    "long-pause": "说前明显停顿",
    "breath": "说前带一次自然呼吸",
    "inhale": "说前短促吸气",
    "exhale": "说前呼出压住的情绪",
    "sigh": "说前自然叹息",
    "chuckle": "说前短促轻笑",
    "laugh": "说前自然笑一下",
    "giggle": "说前轻轻笑一下",
    "cry": "带真实哭腔但保持咬字",
    "tsk": "说前轻啧一声",
    "tongue-click": "说前轻弹舌",
    "lip-smack": "说前自然抿唇",
}


def voices_for_gender(gender: Optional[str]) -> List[str]:
    want = (gender or "").strip().lower()
    if want in {"female", "女", "f"}:
        want = "female"
    elif want in {"male", "男", "m"}:
        want = "male"
    else:
        return list(VOICES)
    return list(_PREFERRED_BY_GENDER[want])


def voice_records() -> List[Dict[str, Any]]:
    return [
        {
            "id": voice_id,
            "name": meta["name"],
            "gender": "Female" if meta["gender"] == "female" else "Male",
            "model": "Doubao Seed-TTS 2.0",
            "family": "doubao",
            "supports_instruction": True,
            "dialect": None,
            "lang_primary": "zh-CN",
            "origin": "system",
            "scene": meta["scene"],
            "provenance": meta["provenance"],
        }
        for voice_id, meta in VOICES.items()
    ]


class DoubaoTTS:
    """Synthesize one line through Seed-TTS 2.0 and measure the written WAV."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_s: float = 120.0,
    ) -> None:
        self._api_key_value = (api_key or "").strip() or None
        self.base_url = (
            (base_url or os.getenv("VOLCENGINE_TTS_ENDPOINT") or _API_URL).strip().rstrip("/")
        )
        self.timeout_s = float(timeout_s)

    def available(self) -> bool:
        return bool(self._api_key_value or os.getenv("VOLCENGINE_TTS_API_KEY"))

    def _api_key(self) -> str:
        key = self._api_key_value or (os.getenv("VOLCENGINE_TTS_API_KEY") or "").strip()
        if not key:
            raise RuntimeError(
                "VOLCENGINE_TTS_API_KEY is not configured; create it in the Doubao speech console"
            )
        return key

    @staticmethod
    def _resolve_voice(voice: Optional[str]) -> Tuple[str, Dict[str, str]]:
        voice_id = (voice or "").strip()
        if voice_id not in VOICES:
            raise ValueError(
                f"unknown Doubao voice_id {voice_id!r}; choose one of {', '.join(VOICES)}"
            )
        return voice_id, VOICES[voice_id]

    @staticmethod
    def _provider_rate(requested_speed: float) -> int:
        speed = float(requested_speed)
        if not 0.5 <= speed <= 2.0:
            raise ValueError("speech_rate must be in [0.5, 2.0]")
        return max(-50, min(100, round((speed - 1.0) * 100)))

    @staticmethod
    def _performance_plan(
        text: str,
        performance: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        plan = normalize_performance(text, performance)
        if isinstance(performance, dict):
            declared_hash = performance.get("text_sha256")
            if declared_hash and declared_hash != text_sha256(text):
                return None
        return plan

    @classmethod
    def _context_instruction(
        cls,
        text: str,
        instructions: Optional[str],
        performance: Optional[Dict[str, Any]],
        *,
        span_start: int = 0,
        span_end: Optional[int] = None,
        continuation: bool = False,
    ) -> Optional[str]:
        parts: List[str] = []
        direct = (instructions or "").strip().rstrip("。")
        plan = cls._performance_plan(text, performance)
        end_limit = len(text) if span_end is None else min(len(text), int(span_end))
        if plan:
            intent = str(plan.get("intent") or "").strip().rstrip("。")
            if intent:
                parts.append(intent)
            elif direct:
                parts.append(direct)
            for segment in plan.get("segments") or []:
                start = max(int(segment["start_char"]), int(span_start))
                end = min(int(segment["end_char"]), end_limit)
                if end <= start:
                    continue
                phrase = text[start:end]
                actions = []
                event = _EVENT_DIRECTION.get(segment.get("event_before"))
                style = _STYLE_DIRECTION.get(segment.get("style"))
                if event:
                    actions.append(event)
                if style:
                    actions.append(style)
                if actions:
                    parts.append(f"说到“{phrase}”时，" + "，".join(actions))
        elif direct:
            parts.append(direct)

        if not parts:
            return None
        prefix = (
            "承接上一段，保持同一人物、声线、场景和说话距离。"
            if continuation
            else "请按以下表演意图自然表达。"
        )
        return prefix + "；".join(parts) + "。"

    @classmethod
    def _compile_request_parts(
        cls,
        text: str,
        instructions: Optional[str],
        performance: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Compile one director-authored line into exactly one acoustic take.

        Seed-TTS section history can preserve semantic context, but separate requests
        may still drift in timbre and breathing.  A dialogue line therefore never
        crosses a provider-request boundary; local performance spans stay prompt-only.
        """
        plan = cls._performance_plan(text, performance)
        return [
            {
                "start_char": 0,
                "end_char": len(text),
                "text": text,
                "context": cls._context_instruction(
                    text,
                    instructions,
                    plan,
                    span_start=0,
                    span_end=len(text),
                    continuation=False,
                ),
                "join_s": 0.0,
            }
        ]

    @staticmethod
    def content_sha256(
        text: str,
        voice_id: str,
        provider_rate: int,
        parts: List[Dict[str, Any]],
        take_id: Optional[str],
    ) -> str:
        compiled = json.dumps(
            [
                {
                    "text": part["text"],
                    "context": part.get("context"),
                    "join_s": part.get("join_s"),
                }
                for part in parts
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload = (
            f"{SPEECH_RENDER_CONTRACT}:doubao-v4\0{_RESOURCE_ID}\0{text}\0{voice_id}\0"
            f"{provider_rate}\0{compiled}\0{take_id or ''}"
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _pcm_trim_window(
        audio_bytes: bytes,
        timing: Optional[Dict[str, Any]],
    ) -> Tuple[int, int, float]:
        if len(audio_bytes) % _PCM_SAMPLE_BYTES:
            raise RuntimeError("Doubao TTS returned misaligned 16-bit PCM")
        frame_count = len(audio_bytes) // _PCM_SAMPLE_BYTES
        if frame_count <= 0:
            raise RuntimeError("Doubao TTS returned empty PCM")

        start_s = 0.0
        end_s = frame_count / _SAMPLE_RATE
        graph_times = (timing or {}).get("graph_times") or []
        usable: List[Tuple[float, float]] = []
        for value in graph_times:
            try:
                start, end = float(value[0]), float(value[1])
            except (IndexError, TypeError, ValueError):
                continue
            if end > start >= 0:
                usable.append((start, end))
        if usable:
            start_s = max(0.0, min(item[0] for item in usable) - _HEAD_ROOM_S)
            end_s = min(end_s, max(item[1] for item in usable) + _TAIL_ROOM_S)

        first_frame = max(0, min(frame_count - 1, int(start_s * _SAMPLE_RATE)))
        final_frame = max(
            first_frame + 1,
            min(frame_count, math.ceil(end_s * _SAMPLE_RATE)),
        )
        return first_frame, final_frame, first_frame / _SAMPLE_RATE

    @staticmethod
    def _shift_timing(
        timing: Dict[str, Any],
        *,
        trim_start_s: float,
        output_offset_s: float,
        part_duration_s: float,
    ) -> Dict[str, Any]:
        shifted: List[List[float]] = []
        for start, end in timing["graph_times"]:
            local_start = max(0.0, min(part_duration_s, float(start) - trim_start_s))
            local_end = max(local_start, min(part_duration_s, float(end) - trim_start_s))
            shifted.append(
                [output_offset_s + local_start, output_offset_s + local_end]
            )
        return {
            "graph_chars": list(timing["graph_chars"]),
            "graph_times": shifted,
        }

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
        from src.luoxia.media.ffprobe import measure_media_duration_s
        from src.utils.system_check import get_ffmpeg_path

        line = (text or "").strip()
        if not line:
            raise ValueError("refusing to synthesize an empty line")
        voice_id, _meta = self._resolve_voice(voice)
        provider_rate = self._provider_rate(speech_rate)
        parts = self._compile_request_parts(line, instructions, performance)
        digest = self.content_sha256(line, voice_id, provider_rate, parts, take_id)

        out = Path(output_path)
        if out.suffix.lower() != ".wav":
            raise ValueError("Doubao TTS output_path must end in .wav")
        digest_path = Path(str(out) + ".sha256")
        if out.is_file() and digest_path.is_file():
            if digest_path.read_text(encoding="utf-8").strip() == digest:
                return str(out), measure_media_duration_s(out), digest

        out.parent.mkdir(parents=True, exist_ok=True)
        wav_handle = tempfile.NamedTemporaryFile(
            prefix=f"{out.stem}.master-", suffix=".wav", dir=out.parent, delete=False
        )
        wav_path = Path(wav_handle.name)
        try:
            wav_handle.close()
            combined_pcm = bytearray()
            timing_chars: List[str] = []
            timing_values: List[List[float]] = []
            timing_complete = True
            output_offset_s = 0.0
            usages: List[Dict[str, Any]] = []
            log_ids: List[str] = []

            for part in parts:
                audio_bytes, sentences, usage, log_id = self._request(
                    text=part["text"],
                    voice_id=voice_id,
                    provider_rate=provider_rate,
                    context=part.get("context"),
                    audio_format="pcm",
                )
                if usage:
                    usages.append(usage)
                if log_id:
                    log_ids.append(log_id)

                part_timing = self._timing_payload(sentences, part["text"])
                first_frame, final_frame, trim_start_s = self._pcm_trim_window(
                    audio_bytes,
                    part_timing,
                )
                join_frames = round(float(part["join_s"]) * _SAMPLE_RATE)
                if join_frames:
                    combined_pcm.extend(b"\0" * join_frames * _PCM_SAMPLE_BYTES)
                    output_offset_s += join_frames / _SAMPLE_RATE

                start_byte = first_frame * _PCM_SAMPLE_BYTES
                end_byte = final_frame * _PCM_SAMPLE_BYTES
                clipped = audio_bytes[start_byte:end_byte]
                combined_pcm.extend(clipped)
                part_duration_s = (final_frame - first_frame) / _SAMPLE_RATE

                if part_timing:
                    shifted = self._shift_timing(
                        part_timing,
                        trim_start_s=trim_start_s,
                        output_offset_s=output_offset_s,
                        part_duration_s=part_duration_s,
                    )
                    timing_chars.extend(shifted["graph_chars"])
                    timing_values.extend(shifted["graph_times"])
                else:
                    timing_complete = False
                output_offset_s += part_duration_s

            ffmpeg = get_ffmpeg_path()
            if not ffmpeg:
                raise RuntimeError("ffmpeg not found; cannot convert Doubao audio to WAV")
            convert = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "s16le",
                    "-ar",
                    str(_SAMPLE_RATE),
                    "-ac",
                    "1",
                    "-i",
                    "pipe:0",
                    "-ar",
                    "48000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s24le",
                    str(wav_path),
                ],
                input=bytes(combined_pcm),
                capture_output=True,
                timeout=120,
            )
            if convert.returncode != 0:
                stderr = convert.stderr.decode("utf-8", errors="replace")
                raise RuntimeError(f"Doubao audio conversion failed: {stderr[-800:]}")
            measured = measure_media_duration_s(wav_path)
            if measured <= 0:
                raise RuntimeError("Doubao TTS produced unplayable audio")
            wav_path.replace(out)

            timing = None
            if timing_complete:
                timing = clean_audio_timestamps(
                    {"graph_chars": timing_chars, "graph_times": timing_values},
                    line,
                )
            timing_path = Path(str(out) + ".timings.json")
            if timing:
                timing_path.write_text(
                    json.dumps(timing, ensure_ascii=False), encoding="utf-8", newline="\n"
                )
            else:
                timing_path.unlink(missing_ok=True)
            digest_path.write_text(digest + "\n", encoding="utf-8", newline="\n")
            logger.info(
                "doubao tts %s voice=%s speed=%d parts=%d usage=%s logids=%s -> %.3fs",
                out.name,
                voice_id,
                provider_rate,
                len(parts),
                usages or "-",
                log_ids or "-",
                measured,
            )
            return str(out), measured, digest
        finally:
            if not wav_handle.closed:
                wav_handle.close()
            wav_path.unlink(missing_ok=True)

    def _request(
        self,
        *,
        text: str,
        voice_id: str,
        provider_rate: int,
        context: Optional[str],
        audio_format: str = "pcm",
    ) -> Tuple[bytes, List[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
        import requests

        request_id = str(uuid.uuid4())
        headers = {
            "X-Api-Key": self._api_key(),
            "X-Api-Resource-Id": _RESOURCE_ID,
            "X-Api-Request-Id": request_id,
            "X-Control-Require-Usage-Tokens-Return": "*",
            "Content-Type": "application/json",
        }
        additions: Dict[str, Any] = {"disable_markdown_filter": True}
        if context:
            # The API currently accepts a list but only reads its first item.
            additions["context_texts"] = [context]
        body = {
            "user": {"uid": "luoxia-video"},
            "req_params": {
                "text": text,
                "speaker": voice_id,
                "audio_params": {
                    "format": audio_format,
                    "sample_rate": _SAMPLE_RATE,
                    "speech_rate": provider_rate,
                    "enable_subtitle": True,
                },
                "additions": json.dumps(additions, ensure_ascii=False),
            },
        }
        response = requests.post(
            self.base_url,
            headers=headers,
            json=body,
            timeout=(10, self.timeout_s),
        )
        log_id = response.headers.get("X-Tt-Logid")
        if response.status_code >= 400:
            detail = response.text[:500] or "empty response"
            raise RuntimeError(
                f"Doubao TTS failed HTTP {response.status_code}: {detail}; logid={log_id or '-'}"
            )

        audio: List[bytes] = []
        sentences: List[Dict[str, Any]] = []
        usage: Optional[Dict[str, Any]] = None
        finished = False
        decoder = json.JSONDecoder()
        raw = response.content.decode("utf-8")
        cursor = 0
        while cursor < len(raw):
            while cursor < len(raw) and raw[cursor].isspace():
                cursor += 1
            if cursor >= len(raw):
                break
            try:
                item, cursor = decoder.raw_decode(raw, cursor)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Doubao TTS returned malformed chunked JSON; logid={log_id or '-'}"
                ) from exc
            code = item.get("code")
            if code == 0:
                if item.get("data"):
                    audio.append(base64.b64decode(item["data"]))
                if isinstance(item.get("sentence"), dict):
                    sentences.append(item["sentence"])
                continue
            if code == 20000000:
                finished = True
                usage = item.get("usage") if isinstance(item.get("usage"), dict) else None
                continue
            raise RuntimeError(
                f"Doubao TTS failed code {code}: {item.get('message') or 'unknown error'}; "
                f"logid={log_id or '-'}"
            )

        joined = b"".join(audio)
        if not finished or not joined:
            raise RuntimeError(
                f"Doubao TTS returned no complete audio; logid={log_id or '-'}"
            )
        return joined, sentences, usage, log_id

    @staticmethod
    def _timing_payload(
        sentences: List[Dict[str, Any]], expected_text: str
    ) -> Optional[Dict[str, Any]]:
        chars: List[str] = []
        times: List[List[float]] = []
        for sentence in sentences:
            for item in sentence.get("words") or []:
                token = str(item.get("word") or "")
                if not token:
                    continue
                try:
                    start = float(item["startTime"])
                    end = float(item["endTime"])
                except (KeyError, TypeError, ValueError):
                    continue
                width = max(0.0, end - start) / len(token)
                for index, char in enumerate(token):
                    chars.append(char)
                    times.append([start + width * index, start + width * (index + 1)])
        return clean_audio_timestamps(
            {"graph_chars": chars, "graph_times": times}, expected_text
        )
