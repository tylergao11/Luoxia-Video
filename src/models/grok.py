"""xAI video generation adapter.

Vendor-specific fields stay inside this module. Business/timeline code must not
import model ids or response field names from here except via VideoGenModel.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from src.luoxia.pricing import register_pricing
from src.luoxia.media.ffprobe import has_audio_stream
from src.models.base import VideoGenModel
from src.utils.system_check import get_ffmpeg_path

logger = logging.getLogger(__name__)

_API_BASE = "https://api.x.ai/v1"
_DEFAULT_MODEL = "grok-imagine-video-1.5"

# Pricing from xAI Imagine model pages; vendor rates stay at this adapter boundary.
_MODEL_PRICING = {
    "grok-imagine-video": ({"480p": 0.05, "720p": 0.07}, 0.002),
    "grok-imagine-video-1.5": (
        {"480p": 0.08, "720p": 0.14, "1080p": 0.25},
        0.01,
    ),
}


def _pricing(resolution: str) -> Tuple[float, float]:
    return _model_pricing(_DEFAULT_MODEL, resolution)


def _model_pricing(model_name: str, resolution: str) -> Tuple[float, float]:
    canonical = (
        "grok-imagine-video-1.5"
        if model_name.startswith("grok-imagine-video-1.5")
        else model_name
    )
    if canonical not in _MODEL_PRICING:
        raise ValueError(f"no xai pricing contract for model: {model_name}")
    rates, per_image = _MODEL_PRICING[canonical]
    per_second = rates.get(resolution)
    if per_second is None:
        raise ValueError(
            f"unsupported resolution for xai pricing: model={model_name}, "
            f"resolution={resolution}"
        )
    return per_second, per_image


register_pricing("xai", _pricing)


class GrokVideoModel(VideoGenModel):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        # Prefer explicit config key; otherwise resolve lazily on first request
        # so constructing the adapter does not require login (factory / UI).
        self.api_key = (config.get("api_key") or "").strip()
        self._auth_kind = "api_key" if self.api_key else None
        self._lazy_base: Optional[str] = None
        params = config.get("params") or {}
        self.model_name = params.get("model_name") or config.get("model_name") or _DEFAULT_MODEL
        self.poll_interval_s = float(config.get("poll_interval_s") or 5)
        self.poll_timeout_s = float(config.get("poll_timeout_s") or 900)
        self.base_url = (config.get("base_url") or _API_BASE).rstrip("/")
        self.api_trust_env = bool(config.get("api_trust_env", True))
        self.download_trust_env = bool(config.get("download_trust_env", True))
        self._api_session = requests.Session()
        self._api_session.trust_env = self.api_trust_env

    def generate(self, prompt: str, output_path: str, **kwargs) -> Tuple[str, float]:
        started = time.time()
        duration = kwargs.get("duration")
        if duration is None:
            raise ValueError("duration must be supplied from timeline.request_duration_s")
        duration = int(duration)
        if duration < 1 or duration > 15:
            raise ValueError(f"xai video duration must be within 1..15 seconds; got {duration}")

        resolution = kwargs.get("resolution") or "720p"
        audio_mode = kwargs.get("audio_mode") or "strip"
        if audio_mode not in {"strip", "native_required"}:
            raise ValueError(
                "audio_mode must be 'strip' or 'native_required'; "
                f"got {audio_mode!r}"
            )
        image_url = kwargs.get("image_url") or kwargs.get("image")
        image_url = _coerce_image_url(image_url)
        reference_images = _coerce_reference_images(
            kwargs.get("reference_images") or kwargs.get("reference_image_urls")
        )
        reference_audios = _coerce_reference_audios(
            kwargs.get("reference_audios") or kwargs.get("reference_voice_ids")
        )
        if image_url and (reference_images or reference_audios):
            raise ValueError(
                "xai image-to-video cannot be combined with reference-to-video inputs"
            )
        if reference_images or reference_audios:
            mode = "r2v"
        else:
            mode = "i2v" if image_url else "t2v"
        if mode == "r2v" and resolution == "1080p":
            raise ValueError(
                "xai reference-to-video is capped at 720p; refusing to downgrade 1080p"
            )
        self.last_request_id = None
        self.last_source_url = None
        self.last_mode = mode
        self.last_has_audio_track = False
        self.last_audio_stripped = False
        self.last_audio_mode = audio_mode
        self.last_reference_image_count = len(reference_images)
        self.last_reference_voice_ids = [item["voice_id"] for item in reference_audios]
        self.last_moderation_passed = None
        per_second, per_image = _model_pricing(self.model_name, resolution)
        image_input_count = 1 if image_url else len(reference_images)
        self.last_cost_usd = per_second * duration + (per_image * image_input_count)

        # I2V: omit aspect_ratio so the API does not stretch the source still.
        aspect_ratio = None if mode == "i2v" else kwargs.get("aspect_ratio")
        if mode == "r2v" and not aspect_ratio:
            raise ValueError(
                "aspect_ratio must be explicit for xai reference-to-video"
            )

        request_id = kwargs.get("request_id")
        if not request_id:
            request_id = self._submit(
                prompt=prompt,
                duration=duration,
                resolution=resolution,
                image_url=image_url,
                aspect_ratio=aspect_ratio,
                reference_images=reference_images,
                reference_audios=reference_audios,
            )
        self.last_request_id = request_id

        video_meta = self._poll(request_id)
        if video_meta.get("moderation_passed") is False:
            raise GrokGenerationError(
                "content_filtered",
                "generation blocked by moderation; rewrite prompt before retry",
                retryable=False,
            )

        source_url = video_meta["url"]
        if not source_url:
            raise GrokGenerationError(
                "invalid_response",
                f"provider returned no video URL for request {request_id}",
                retryable=True,
            )
        self.last_source_url = source_url
        self.last_moderation_passed = bool(video_meta.get("moderation_passed", True))
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        self._download(source_url, output_path)

        source_has_audio = has_audio_stream(output_path)
        if audio_mode == "native_required":
            if not source_has_audio:
                raise GrokGenerationError(
                    "missing_native_audio",
                    "provider returned no audio track for native_required request",
                    retryable=True,
                )
            final_path = output_path
            audio_stripped = False
        else:
            final_path = self._strip_audio(output_path) if source_has_audio else output_path
            audio_stripped = source_has_audio
        elapsed = time.time() - started
        logger.info(
            "xai video ready request_id=%s path=%s audio_mode=%s "
            "source_has_audio=%s stripped=%s elapsed=%.1fs",
            request_id,
            final_path,
            audio_mode,
            source_has_audio,
            audio_stripped,
            elapsed,
        )
        # Stash vendor ids on instance for runner to persist into timeline.video.*
        self.last_has_audio_track = source_has_audio
        self.last_audio_stripped = audio_stripped
        return final_path, elapsed

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            token, kind, base = resolve_xai_token("video")
            self.api_key = token
            self._auth_kind = kind
            if base:
                self.base_url = base.rstrip("/")
        if not self.api_key:
            raise RuntimeError(
                "Need login for subscription pool (not an API key). "
                "Use Settings → Auth, or POST /auth/login — "
                "or switch LUOXIA_AUTH_MODE=api_key and set XAI_API_KEY."
            )
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
        reference_images: list[Dict[str, str]],
        reference_audios: list[Dict[str, str]],
    ) -> str:
        body: Dict[str, Any] = {
            "model": self.model_name,
            "prompt": prompt,
            "duration": duration,
            "resolution": resolution,
        }
        if image_url:
            body["image"] = {"url": image_url}
        if reference_images:
            body["reference_images"] = reference_images
        if reference_audios:
            body["reference_audios"] = reference_audios
        if aspect_ratio:
            body["aspect_ratio"] = aspect_ratio

        resp = self._api_session.post(
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
            resp = self._api_session.get(
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
        target = Path(output_path)
        part = Path(f"{output_path}.part")
        part_meta = Path(f"{output_path}.part.json")
        object_url = url.split("?", 1)[0]
        resume_allowed = False
        if part.is_file() and part_meta.is_file():
            try:
                saved = json.loads(part_meta.read_text(encoding="utf-8"))
                resume_allowed = saved.get("object_url") == object_url
            except Exception:
                resume_allowed = False
        if not resume_allowed:
            part.unlink(missing_ok=True)
            part_meta.unlink(missing_ok=True)
        part_meta.write_text(
            json.dumps({"object_url": object_url}, ensure_ascii=False),
            encoding="utf-8",
        )
        expected_total: Optional[int] = None
        last_error: Optional[Exception] = None
        session = requests.Session()
        session.trust_env = self.download_trust_env

        for attempt in range(1, 5):
            offset = part.stat().st_size if part.is_file() else 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            try:
                with session.get(
                    url,
                    headers=headers,
                    stream=True,
                    timeout=(30, 120),
                ) as resp:
                    resp.raise_for_status()
                    append = offset > 0 and resp.status_code == 206
                    if append:
                        content_range = resp.headers.get("Content-Range") or ""
                        if not content_range.startswith(f"bytes {offset}-"):
                            raise RuntimeError(
                                "provider returned an invalid Content-Range for resume: "
                                f"{content_range!r}"
                            )
                        total_text = content_range.rsplit("/", 1)[-1]
                        expected_total = (
                            int(total_text) if total_text.isdigit() else expected_total
                        )
                    else:
                        # Some object stores ignore Range and return 200. Restart this
                        # local part from byte zero using that complete response.
                        offset = 0
                        content_length = resp.headers.get("Content-Length")
                        expected_total = (
                            int(content_length)
                            if content_length and content_length.isdigit()
                            else None
                        )

                    with part.open("ab" if append else "wb") as file:
                        for chunk in resp.iter_content(chunk_size=1024 * 256):
                            if chunk:
                                file.write(chunk)

                actual = part.stat().st_size
                if expected_total is None or actual == expected_total:
                    part.replace(target)
                    part_meta.unlink(missing_ok=True)
                    session.close()
                    return
                if actual > expected_total:
                    raise RuntimeError(
                        f"download exceeded Content-Length: {actual} > {expected_total}"
                    )
                last_error = RuntimeError(
                    f"incomplete download: {actual}/{expected_total} bytes"
                )
            except (OSError, requests.RequestException, RuntimeError) as exc:
                last_error = exc

            if attempt < 4:
                time.sleep(min(2 ** (attempt - 1), 4))

        actual = part.stat().st_size if part.is_file() else 0
        session.close()
        raise RuntimeError(
            f"video download failed after 4 attempts; retained {actual} bytes at {part}: "
            f"{last_error}"
        )

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


def resolve_xai_token(purpose: str = "generation") -> Tuple[str, Optional[str], Optional[str]]:
    """Return (token, kind, base_url) via pluggable auth entry layer.

    Shared by every xAI surface — video, images, TTS — so session login, api_key mode and
    offline all behave identically no matter which one the pipeline reaches first.
    """
    from src.auth.config import load_auth_config
    from src.auth.errors import AuthError, LoginRequiredError
    from src.auth.resolver import resolve_credential

    try:
        cfg = load_auth_config()
    except Exception:
        cfg = None

    # Legacy / unit-test path: explicit XAI_API_KEY always usable when set,
    # unless session mode is active without allowing key fallback.
    env_key = (os.getenv("XAI_API_KEY") or "").strip()

    try:
        if cfg is None:
            if env_key:
                return env_key, "api_key", None
            raise RuntimeError("Need login for subscription pool (not an API key).")

        if cfg.mode == "offline":
            raise RuntimeError(
                f"Auth mode is offline — cloud {purpose} disabled. "
                "Log in or switch to api_key mode."
            )
        if cfg.mode == "api_key":
            if not env_key:
                raise RuntimeError(
                    "API-key mode: set XAI_API_KEY, or switch LUOXIA_AUTH_MODE=session and login."
                )
            return env_key, "api_key", None
        # session mode
        try:
            resolved = resolve_credential(config=cfg, purpose=purpose)
            base = resolved.credential.base_url
            return resolved.credential.token, resolved.credential.kind, base
        except LoginRequiredError:
            raise
    except LoginRequiredError as e:
        raise RuntimeError(str(e)) from e
    except AuthError as e:
        if env_key and cfg and cfg.mode != "session":
            return env_key, "api_key", None
        raise RuntimeError(str(e)) from e
    except RuntimeError:
        raise
    except Exception:
        if env_key:
            return env_key, "api_key", None
        raise


class GrokGenerationError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.retryable = retryable


def _coerce_image_url(image_url: Optional[str]) -> Optional[str]:
    """Accept http(s)/data URLs as-is; encode local files as data URIs for xAI.

    A shot asking for i2v must get i2v. Falling back to t2v when the still looks
    inconvenient loses the locked faces and the framing the storyboard chose, and the run
    still reports success — so an unusable still is an error, and any payload limit is the
    API's to enforce and report rather than ours to guess at.
    """
    if not image_url:
        return None
    s = str(image_url).strip()
    if s.startswith(("http://", "https://", "data:")):
        return s
    path = Path(s)
    if not path.is_file():
        raise FileNotFoundError(f"i2v still not found, refusing to fall back to t2v: {s}")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _coerce_reference_images(values: Any) -> list[Dict[str, str]]:
    if not values:
        return []
    if isinstance(values, (str, Path, dict)):
        values = [values]
    if not isinstance(values, (list, tuple)):
        raise TypeError("reference_images must be a sequence")
    if len(values) > 7:
        raise ValueError("xai reference-to-video accepts at most 7 reference images")

    images: list[Dict[str, str]] = []
    for value in values:
        if isinstance(value, dict):
            if value.get("file_id"):
                images.append({"file_id": str(value["file_id"])})
                continue
            value = value.get("url")
        url = _coerce_image_url(value)
        if not url:
            raise ValueError("reference image must contain url or file_id")
        images.append({"url": url})
    return images


def _coerce_reference_audios(values: Any) -> list[Dict[str, str]]:
    if not values:
        return []
    if isinstance(values, (str, dict)):
        values = [values]
    if not isinstance(values, (list, tuple)):
        raise TypeError("reference_audios must be a sequence")
    if len(values) > 3:
        raise ValueError("xai reference-to-video accepts at most 3 reference voices")

    audios: list[Dict[str, str]] = []
    for value in values:
        voice_id = value.get("voice_id") if isinstance(value, dict) else value
        voice_id = str(voice_id or "").strip()
        if not voice_id:
            raise ValueError("reference audio must contain a non-empty voice_id")
        audios.append({"voice_id": voice_id})
    return audios
