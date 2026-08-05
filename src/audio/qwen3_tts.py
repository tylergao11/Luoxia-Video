"""Local Qwen3-TTS VoiceDesign adapter for dramatic dialogue.

The application process intentionally does not import PyTorch.  Inference runs in an
isolated environment under ``output/runtime/qwen3-tts`` (or paths supplied through
environment variables), so the desktop/backend dependency set stays small.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.audio.performance import normalize_performance, text_sha256

logger = logging.getLogger(__name__)

VOICE_PROFILES: Dict[str, Dict[str, str]] = {
    "qwen3-young-male": {
        "name": "少年男主",
        "gender": "male",
        "prompt": (
            "二十岁左右的中国青年男声，自然男声中音区，松弛、清晰，略有少年感，"
            "不是童声，也不刻意压低或拔高音调"
        ),
    },
    "qwen3-cold-male": {
        "name": "清冷青年",
        "gender": "male",
        "prompt": "二十多岁的中国青年男声，声线清冷克制，咬字利落，冷静中藏着锋芒",
    },
    "qwen3-mature-male": {
        "name": "沉稳男性",
        "gender": "male",
        "prompt": "三十到四十岁的中国男性声线，温厚偏低，沉稳有威信，但不故作深沉",
    },
    "qwen3-young-female": {
        "name": "青年女主",
        "gender": "female",
        "prompt": "十八到二十五岁的中国青年女声，清亮自然，情感敏锐，有真实的呼吸和力量",
    },
    "qwen3-cold-female": {
        "name": "清冷女性",
        "gender": "female",
        "prompt": "二十多岁的中国女性声线，清冷克制、吐字干净，疏离感下保留真实情绪",
    },
    "qwen3-mature-female": {
        "name": "成熟女性",
        "gender": "female",
        "prompt": "三十到四十岁的中国女性声线，沉着从容、质感温润，情绪表达细腻而坚定",
    },
}

_PREFERRED_BY_GENDER = {
    "male": ("qwen3-young-male", "qwen3-cold-male", "qwen3-mature-male"),
    "female": ("qwen3-young-female", "qwen3-cold-female", "qwen3-mature-female"),
}

_STYLE_DIRECTION = {
    "soft": "收住力度，保持自然说话声",
    "whisper": "压低为近距离耳语",
    "loud": "提高力度并爆发，但不喊破",
    "build-intensity": "情绪逐渐增强，但保持句子连贯",
    "decrease-intensity": "逐渐收住情绪和音量",
    "higher-pitch": "音高自然上扬",
    "lower-pitch": "压低声线并保持清晰",
    "slow": "放慢语速并留出真实呼吸",
    "fast": "加快语速但每个字仍清楚",
    "sing-song": "带轻微旋律感但仍是对白",
    "singing": "按歌唱语气表达",
    "laugh-speak": "带着自然笑意说出",
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
    "cry": "带真实哭腔但不能失去咬字",
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
        return list(VOICE_PROFILES)
    return list(_PREFERRED_BY_GENDER[want])


def voice_records() -> List[Dict[str, Any]]:
    return [
        {
            "id": voice_id,
            "name": profile["name"],
            "gender": "Female" if profile["gender"] == "female" else "Male",
            "model": "Qwen3-TTS-1.7B-VoiceDesign",
            "family": "qwen3",
            "supports_instruction": True,
            "dialect": None,
            "lang_primary": None,
            "origin": "system",
        }
        for voice_id, profile in VOICE_PROFILES.items()
    ]


class Qwen3TTS:
    """Synthesize one dramatic line and measure the bytes actually written."""

    def __init__(
        self,
        *,
        runtime_root: Optional[str | Path] = None,
        python_path: Optional[str | Path] = None,
        model_path: Optional[str | Path] = None,
        timeout_s: float = 600.0,
    ) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        self.runtime_root = Path(
            runtime_root
            or os.getenv("QWEN3_TTS_RUNTIME_DIR")
            or repo_root / "output" / "runtime" / "qwen3-tts"
        )
        default_python = (
            self.runtime_root / ".venv" / "Scripts" / "python.exe"
            if os.name == "nt"
            else self.runtime_root / ".venv" / "bin" / "python"
        )
        self.python_path = Path(python_path or os.getenv("QWEN3_TTS_PYTHON") or default_python)
        self.model_path = Path(
            model_path
            or os.getenv("QWEN3_TTS_MODEL_DIR")
            or self.runtime_root / "models" / "Qwen3-TTS-12Hz-1.7B-VoiceDesign"
        )
        self.repo_root = repo_root
        self.timeout_s = float(timeout_s)

    def available(self) -> bool:
        return self.python_path.is_file() and self.model_path.is_dir()

    def _require_runtime(self) -> None:
        missing = []
        if not self.python_path.is_file():
            missing.append(f"python={self.python_path}")
        if not self.model_path.is_dir():
            missing.append(f"model={self.model_path}")
        if missing:
            raise RuntimeError(
                "Qwen3-TTS runtime is not installed (" + ", ".join(missing) + "). "
                "Set QWEN3_TTS_RUNTIME_DIR/QWEN3_TTS_MODEL_DIR to the local GitHub runtime."
            )

    @staticmethod
    def _resolve_voice(voice: Optional[str]) -> Tuple[str, Dict[str, str]]:
        voice_id = (voice or "").strip()
        if voice_id not in VOICE_PROFILES:
            raise ValueError(
                f"unknown Qwen3 voice_id {voice_id!r}; choose one of "
                f"{', '.join(VOICE_PROFILES)}"
            )
        return voice_id, VOICE_PROFILES[voice_id]

    @staticmethod
    def _performance_direction(text: str, performance: Any) -> str:
        if not isinstance(performance, dict):
            return ""
        if performance.get("text_sha256") not in {None, text_sha256(text)}:
            return ""
        plan = normalize_performance(text, performance)
        if not plan:
            return ""
        directions = []
        for segment in plan.get("segments") or []:
            phrase = text[segment["start_char"] : segment["end_char"]]
            actions = []
            event = _EVENT_DIRECTION.get(segment.get("event_before"))
            style = _STYLE_DIRECTION.get(segment.get("style"))
            if event:
                actions.append(event)
            if style:
                actions.append(style)
            if actions:
                if segment["start_char"] == 0 and segment["end_char"] == len(text):
                    where = "整句"
                elif segment["start_char"] == 0:
                    where = "开头"
                elif segment["end_char"] == len(text):
                    where = "结尾"
                elif len(phrase) <= 8:
                    where = f"说到‘{phrase}’时"
                else:
                    where = "中段"
                directions.append(where + "，" + "，".join(actions))
        return "；".join(directions)

    def _instruction(
        self,
        *,
        text: str,
        profile: Dict[str, str],
        instructions: Optional[str],
        performance: Any,
        speech_rate: float,
    ) -> str:
        pieces = [
            profile["prompt"],
            "这是人物当面对话，不是旁白、朗诵或预告片台词",
        ]
        if instructions:
            pieces.append(str(instructions).strip())
        local = self._performance_direction(text, performance)
        if local:
            pieces.append(local)
        if speech_rate >= 1.06:
            pieces.append("整体语速紧凑向前，但不能吞字")
        elif speech_rate <= 0.95:
            pieces.append("整体略慢，停顿有戏但不能拖腔")
        pieces.append("保持自然口语节奏，句尾及时收住，避免播音腔和朗诵腔")
        return "。".join(piece.rstrip("。") for piece in pieces if piece) + "。"

    @staticmethod
    def content_sha256(
        text: str,
        voice_id: str,
        instruction: str,
        speed: float,
        take_id: Optional[str],
    ) -> str:
        payload = (
            f"qwen3-tts-1.7b-voicedesign-v2\0{text}\0{voice_id}\0{instruction}\0"
            f"{float(speed):.6f}\0{take_id or ''}"
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
        from src.luoxia.media.ffprobe import measure_media_duration_s
        from src.utils.system_check import get_ffmpeg_path

        line = (text or "").strip()
        if not line:
            raise ValueError("refusing to synthesize an empty line")
        if not 0.5 <= float(speech_rate) <= 2.0:
            raise ValueError("speech_rate must be in [0.5, 2.0]")
        self._require_runtime()
        voice_id, profile = self._resolve_voice(voice)
        instruction = self._instruction(
            text=line,
            profile=profile,
            instructions=instructions,
            performance=performance,
            speech_rate=float(speech_rate),
        )
        digest = self.content_sha256(line, voice_id, instruction, speech_rate, take_id)
        out = Path(output_path)
        if out.suffix.lower() != ".wav":
            raise ValueError("Qwen3-TTS output_path must end in .wav")
        meta_path = Path(str(out) + ".sha256")
        if out.is_file() and meta_path.is_file():
            if meta_path.read_text(encoding="utf-8").strip() == digest:
                return str(out), measure_media_duration_s(out), digest

        out.parent.mkdir(parents=True, exist_ok=True)
        raw_handle = tempfile.NamedTemporaryFile(
            prefix=f"{out.stem}.qwen-", suffix=".wav", dir=out.parent, delete=False
        )
        raw_path = Path(raw_handle.name)
        raw_handle.close()
        mastered_handle = tempfile.NamedTemporaryFile(
            prefix=f"{out.stem}.master-", suffix=".wav", dir=out.parent, delete=False
        )
        mastered_path = Path(mastered_handle.name)
        mastered_handle.close()
        seed = int(digest.split(":", 1)[1][:8], 16) & 0x7FFFFFFF
        request = {
            "text": line,
            "instruction": instruction,
            "output_path": str(raw_path),
            "model_path": str(self.model_path),
            "seed": seed,
        }
        try:
            run = subprocess.run(
                [str(self.python_path), "-X", "utf8", "-m", "src.audio.qwen3_tts", "--worker"],
                input=json.dumps(request, ensure_ascii=False),
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
            )
            if run.returncode != 0 or not raw_path.is_file() or raw_path.stat().st_size == 0:
                detail = (run.stderr + "\n" + run.stdout)[-1200:]
                raise RuntimeError(f"Qwen3-TTS worker failed: {detail}")

            ffmpeg = get_ffmpeg_path()
            if not ffmpeg:
                raise RuntimeError("ffmpeg not found; cannot master Qwen3-TTS output")
            filters = []
            if abs(float(speech_rate) - 1.0) > 1e-6:
                filters.append(f"atempo={float(speech_rate):.6f}")
            filters.extend(("highpass=f=65", "loudnorm=I=-14:LRA=9:TP=-1"))
            master = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(raw_path),
                    "-af",
                    ",".join(filters),
                    "-ar",
                    "48000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s24le",
                    str(mastered_path),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if master.returncode != 0:
                raise RuntimeError(f"Qwen3-TTS mastering failed: {master.stderr[-800:]}")
            measured = measure_media_duration_s(mastered_path)
            if measured <= 0:
                raise RuntimeError("Qwen3-TTS produced unplayable audio")
            mastered_path.replace(out)
            Path(str(out) + ".timings.json").unlink(missing_ok=True)
            meta_path.write_text(digest + "\n", encoding="utf-8", newline="\n")
            logger.info(
                "qwen3 tts %s voice=%s speed=%.2f -> %.3fs",
                out.name,
                voice_id,
                speech_rate,
                measured,
            )
            return str(out), measured, digest
        finally:
            raw_path.unlink(missing_ok=True)
            mastered_path.unlink(missing_ok=True)


def _worker_main() -> int:
    request = json.loads(sys.stdin.read())
    import soundfile as sf
    import torch
    from transformers import set_seed

    from qwen_tts import Qwen3TTSModel

    use_cuda = torch.cuda.is_available()
    device = "cuda:0" if use_cuda else "cpu"
    dtype = torch.bfloat16 if use_cuda else torch.float32
    set_seed(int(request["seed"]))
    model = Qwen3TTSModel.from_pretrained(
        request["model_path"],
        device_map=device,
        dtype=dtype,
        attn_implementation="sdpa",
    )
    wavs, sample_rate = model.generate_voice_design(
        text=request["text"],
        language="Chinese",
        instruct=request["instruction"],
        non_streaming_mode=True,
        max_new_tokens=2048,
    )
    sf.write(request["output_path"], wavs[0], sample_rate, subtype="PCM_16")
    return 0


if __name__ == "__main__":
    if "--worker" not in sys.argv:
        raise SystemExit("qwen3_tts is an internal worker module")
    raise SystemExit(_worker_main())
