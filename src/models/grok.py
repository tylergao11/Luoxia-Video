"""xAI video generation adapter.

Vendor-specific fields stay inside this module. Business/timeline code must not
import model ids or response field names from here except via VideoGenModel.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from src.luoxia.pricing import register_pricing
from src.models.base import VideoGenModel
from src.utils.system_check import get_ffmpeg_path

logger = logging.getLogger(__name__)

_API_BASE = "https://api.x.ai/v1"
_DEFAULT_MODEL = "grok-imagine-video-1.5"

# Pricing from xAI Imagine docs; registered for cost module — not for business hardcoding.
_RATE_PER_SECOND = {
    "480p": 0.05,
    "720p": 0.07,
    "1080p": 0.07,
}
_RATE_PER_IMAGE = 0.002


def _pricing(resolution: str) -> Tuple[float, float]:
    per_second = _RATE_PER_SECOND.get(resolution)
    if per_second is None:
        raise ValueError(f"unsupported resolution for xai pricing: {resolution}")
    return per_second, _RATE_PER_IMAGE


register_pricing("xai", _pricing)


class GrokVideoModel(VideoGenModel):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key") or os.getenv("XAI_API_KEY", "")
        params = config.get("params") or {}
        self.model_name = params.get("model_name") or config.get("model_name") or _DEFAULT_MODEL
        self.poll_interval_s = float(config.get("poll_interval_s") or 5)
        self.poll_timeout_s = float(config.get("poll_timeout_s") or 900)
        self.base_url = (config.get("base_url") or _API_BASE).rstrip("/")

    def generate(self, prompt: str, output_path: str, **kwargs) -> Tuple[str, float]:
        started = time.time()
        duration = kwargs.get("duration")
        if duration is None:
            raise ValueError("duration must be supplied from timeline.request_duration_s")
        duration = int(duration)

        resolution = kwargs.get("resolution") or "720p"
        image_url = kwargs.get("image_url") or kwargs.get("image")
        image_url = _coerce_image_url(image_url)
        mode = "i2v" if image_url else "t2v"

        # I2V: omit aspect_ratio so the API does not stretch the source still.
        aspect_ratio = None if mode == "i2v" else kwargs.get("aspect_ratio")

        request_id = kwargs.get("request_id")
        if not request_id:
            request_id = self._submit(
                prompt=prompt,
                duration=duration,
                resolution=resolution,
                image_url=image_url,
                aspect_ratio=aspect_ratio,
            )

        video_meta = self._poll(request_id)
        if video_meta.get("moderation_passed") is False:
            raise GrokGenerationError(
                "content_filtered",
                "generation blocked by moderation; rewrite prompt before retry",
                retryable=False,
            )

        source_url = video_meta["url"]
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        self._download(source_url, output_path)

        # Default output includes an audio track; harness uses its own TTS.
        stripped = self._strip_audio(output_path)
        elapsed = time.time() - started
        logger.info(
            "xai video ready request_id=%s path=%s stripped=%s elapsed=%.1fs",
            request_id,
            stripped,
            True,
            elapsed,
        )
        # Stash vendor ids on instance for runner to persist into timeline.video.*
        self.last_request_id = request_id
        self.last_source_url = source_url
        self.last_has_audio_track = True
        self.last_audio_stripped = True
        self.last_moderation_passed = bool(video_meta.get("moderation_passed", True))
        self.last_cost_usd = _pricing(resolution)[0] * duration + (
            _RATE_PER_IMAGE if image_url else 0.0
        )
        return stripped, elapsed

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise RuntimeError("XAI_API_KEY is not configured")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _submit(
        self,
        *,
        prompt: str,
        duration: int,
        resolution: str,
        image_url: Optional[str],
        aspect_ratio: Optional[str],
    ) -> str:
        body: Dict[str, Any] = {
            "model": self.model_name,
            "prompt": prompt,
            "duration": duration,
            "resolution": resolution,
        }
        if image_url:
            body["image"] = {"url": image_url}
        if aspect_ratio:
            body["aspect_ratio"] = aspect_ratio

        resp = requests.post(
            f"{self.base_url}/videos/generations",
            headers=self._headers(),
            json=body,
            timeout=60,
        )
        if resp.status_code >= 400:
            raise GrokGenerationError(
                "http_error",
                f"submit failed HTTP {resp.status_code}: {resp.text[:500]}",
                retryable=resp.status_code in {429, 500, 502, 503},
            )
        data = resp.json()
        request_id = data.get("request_id")
        if not request_id:
            raise GrokGenerationError("invalid_argument", f"missing request_id: {data}", retryable=False)
        return request_id

    def _poll(self, request_id: str) -> Dict[str, Any]:
        deadline = time.time() + self.poll_timeout_s
        while time.time() < deadline:
            resp = requests.get(
                f"{self.base_url}/videos/{request_id}",
                headers=self._headers(),
                timeout=60,
            )
            if resp.status_code >= 400:
                raise GrokGenerationError(
                    "http_error",
                    f"poll failed HTTP {resp.status_code}: {resp.text[:500]}",
                    retryable=resp.status_code in {429, 500, 502, 503},
                )
            data = resp.json()
            status = data.get("status")
            if status == "done":
                video = data.get("video") or {}
                return {
                    "url": video.get("url"),
                    "moderation_passed": video.get("respect_moderation", True),
                }
            if status == "failed":
                err = data.get("error") or {}
                code = err.get("code") or "internal_error"
                retryable = code in {"service_unavailable", "internal_error"}
                raise GrokGenerationError(code, err.get("message") or "generation failed", retryable=retryable)
            if status == "expired":
                raise GrokGenerationError("expired", "request expired", retryable=True)
            time.sleep(self.poll_interval_s)
        raise TimeoutError(f"xai video poll timed out for {request_id}")

    def _download(self, url: str, output_path: str) -> None:
        with requests.get(url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)

    def _strip_audio(self, path: str) -> str:
        ffmpeg = get_ffmpeg_path()
        if not ffmpeg:
            raise RuntimeError("ffmpeg required to strip default xai audio track")
        tmp = str(Path(path).with_suffix(".silent.mp4"))
        result = subprocess.run(
            [ffmpeg, "-y", "-i", path, "-c:v", "copy", "-an", tmp],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg strip audio failed: {result.stderr[-400:]}")
        Path(path).unlink(missing_ok=True)
        Path(tmp).replace(path)
        return path


class GrokGenerationError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.retryable = retryable


def _coerce_image_url(image_url: Optional[str]) -> Optional[str]:
    """Accept http(s)/data URLs as-is; encode local files as data URIs for xAI."""
    if not image_url:
        return None
    s = str(image_url).strip()
    if s.startswith(("http://", "https://", "data:")):
        return s
    path = Path(s)
    if not path.is_file():
        logger.warning("image path not found, falling back to t2v: %s", s)
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    raw = path.read_bytes()
    # Keep payloads modest; oversized stills fall back to t2v.
    if len(raw) > 6 * 1024 * 1024:
        logger.warning("still larger than 6MB, falling back to t2v: %s", path)
        return None
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"
