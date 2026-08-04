"""MuseTalk 1.5 subprocess adapter.

MuseTalk owns face synthesis; Luoxia owns the timeline, input paths and final output
location.  The adapter therefore exposes the runner's narrow
``(video, audio, output) -> output`` contract and keeps vendor CLI details here.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class MuseTalkEngine:
    def __init__(
        self,
        *,
        runtime_root: Optional[str | Path] = None,
        python_path: Optional[str | Path] = None,
        batch_size: int = 4,
        timeout_s: float = 900.0,
    ) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        self.runtime_root = Path(
            runtime_root
            or os.getenv("MUSETALK_RUNTIME_DIR")
            or repo_root / "output" / "runtime" / "musetalk"
        )
        default_python = (
            self.runtime_root / ".venv" / "Scripts" / "python.exe"
            if os.name == "nt"
            else self.runtime_root / ".venv" / "bin" / "python"
        )
        self.python_path = Path(python_path or os.getenv("MUSETALK_PYTHON") or default_python)
        self.batch_size = max(1, int(batch_size))
        self.timeout_s = float(timeout_s)

    def available(self) -> bool:
        required = (
            self.python_path,
            self.runtime_root / "scripts" / "inference.py",
            self.runtime_root / "models" / "musetalkV15" / "unet.pth",
            self.runtime_root / "models" / "musetalkV15" / "musetalk.json",
            self.runtime_root / "models" / "sd-vae" / "diffusion_pytorch_model.bin",
            self.runtime_root / "models" / "whisper" / "pytorch_model.bin",
            self.runtime_root / "models" / "face-parse-bisent" / "79999_iter.pth",
        )
        return all(path.is_file() for path in required)

    def _require_runtime(self) -> None:
        if self.available():
            return
        raise RuntimeError(
            "MuseTalk 1.5 runtime is incomplete. Set MUSETALK_RUNTIME_DIR to an "
            "installed official MuseTalk checkout with v1.5, VAE, Whisper and face-parser weights."
        )

    def __call__(self, video_path: str, audio_path: str, out_path: str) -> str:
        from src.utils.system_check import get_ffmpeg_path

        self._require_runtime()
        video = Path(video_path).resolve()
        audio = Path(audio_path).resolve()
        out = Path(out_path).resolve()
        if not video.is_file():
            raise FileNotFoundError(f"lipsync video not found: {video}")
        if not audio.is_file():
            raise FileNotFoundError(f"lipsync audio not found: {audio}")
        ffmpeg = get_ffmpeg_path()
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required for MuseTalk")

        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{out.stem}-musetalk-", dir=out.parent) as tmp:
            work = Path(tmp)
            config_path = work / "inference.json"
            config_path.write_text(
                json.dumps(
                    {
                        "luoxia_shot": {
                            "video_path": video.as_posix(),
                            "audio_path": audio.as_posix(),
                            "result_name": out.name,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
                newline="\n",
            )
            command = [
                str(self.python_path),
                "-m",
                "scripts.inference",
                "--inference_config",
                str(config_path),
                "--result_dir",
                str(work),
                "--unet_model_path",
                str(self.runtime_root / "models" / "musetalkV15" / "unet.pth"),
                "--unet_config",
                str(self.runtime_root / "models" / "musetalkV15" / "musetalk.json"),
                "--whisper_dir",
                str(self.runtime_root / "models" / "whisper"),
                "--version",
                "v15",
                "--ffmpeg_path",
                str(Path(ffmpeg).parent),
                "--use_float16",
                "--batch_size",
                str(self.batch_size),
                "--extra_margin",
                "10",
                "--parsing_mode",
                "jaw",
                "--left_cheek_width",
                "90",
                "--right_cheek_width",
                "90",
            ]
            env = dict(os.environ)
            env.setdefault("PYTHONIOENCODING", "utf-8")
            run = subprocess.run(
                command,
                cwd=self.runtime_root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
            )
            generated = work / "v15" / out.name
            # Upstream catches task exceptions and may still return code 0, so the file
            # is the success contract, not the process code alone.
            if run.returncode != 0 or not generated.is_file() or generated.stat().st_size == 0:
                detail = (run.stderr + "\n" + run.stdout)[-1600:]
                raise RuntimeError(f"MuseTalk failed: {detail}")

            handle = tempfile.NamedTemporaryFile(
                prefix=f".{out.stem}-", suffix=out.suffix, dir=out.parent, delete=False
            )
            staged = Path(handle.name)
            handle.close()
            try:
                shutil.copy2(generated, staged)
                staged.replace(out)
            finally:
                staged.unlink(missing_ok=True)
        return str(out)


def resolve_musetalk_engine() -> MuseTalkEngine:
    requested = (os.getenv("LUOXIA_LIPSYNC_ENGINE") or "musetalk").strip().lower()
    if requested not in {"musetalk", "musetalk-v1.5", "musetalk1.5"}:
        raise RuntimeError(
            f"unsupported LUOXIA_LIPSYNC_ENGINE={requested!r}; configured engine is MuseTalk 1.5"
        )
    return MuseTalkEngine()
