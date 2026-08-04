"""Still generation via the xAI Imagine image API.

Two endpoints, picked by whether the shot has locked character portraits to honour:
  - no references  -> POST /v1/images/generations
  - 1..3 references -> POST /v1/images/edits

Docs: https://docs.x.ai/developers/model-capabilities/images/generation
      https://docs.x.ai/developers/model-capabilities/images/multi-image-editing
"""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_API_BASE = "https://api.x.ai/v1"
_DEFAULT_MODEL = "grok-imagine-image-quality"

# From the API reference; anything else is rejected rather than quietly becoming "auto".
ASPECT_RATIOS = frozenset({
    "1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2",
    "9:19.5", "19.5:9", "9:20", "20:9", "1:2", "2:1", "auto",
})
RESOLUTIONS = frozenset({"1k", "2k"})

# The API takes at most three source images per edit.
MAX_REFERENCES = 3

_EXT_BY_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}

# One US cent is 100,000,000 ticks.
_TICKS_PER_USD = 10_000_000_000


class XaiImageError(RuntimeError):
    pass


class XaiImageModel:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}
        params = config.get("params") or {}
        self.model_name = params.get("model_name") or config.get("model_name") or _DEFAULT_MODEL
        self.resolution = params.get("resolution") or "1k"
        self.timeout_s = float(config.get("timeout_s") or 180)
        self._token = (config.get("api_key") or "").strip() or None
        self._base_url = (config.get("base_url") or "").rstrip("/") or None
        self.last_cost_usd: Optional[float] = None
        self.last_moderation_passed: bool = True

    @property
    def base_url(self) -> str:
        return self._base_url or _API_BASE

    def _headers(self) -> Dict[str, str]:
        if not self._token:
            from src.models.grok import resolve_xai_token

            token, _kind, base = resolve_xai_token("images")
            self._token = token
            if base and not self._base_url:
                self._base_url = base.rstrip("/")
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def generate(
        self,
        prompt: str,
        output_path: str,
        *,
        aspect_ratio: str = "16:9",
        resolution: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        ref_images: Optional[Sequence[Dict[str, str]]] = None,
        **_ignored: Any,
    ) -> Tuple[str, float]:
        """Render one still; return (written_path, elapsed_s).

        `ref_images` are locked character portraits as `{"display_name", "path"}`, in the
        order they appear on screen. The written path can differ from `output_path` in
        extension: the API chooses the encoding, and a JPEG saved as `.png` would later be
        handed to the video API with the wrong MIME type.
        """
        started = time.time()
        if aspect_ratio not in ASPECT_RATIOS:
            raise XaiImageError(
                f"unsupported aspect_ratio {aspect_ratio!r}; expected one of {sorted(ASPECT_RATIOS)}"
            )
        res = resolution or self.resolution
        if res not in RESOLUTIONS:
            raise XaiImageError(f"unsupported resolution {res!r}; expected 1k or 2k")

        refs = list(ref_images or [])
        if len(refs) > MAX_REFERENCES:
            logger.warning(
                "%d references exceed the xai limit of %d; keeping the first %d on screen",
                len(refs), MAX_REFERENCES, MAX_REFERENCES,
            )
            refs = refs[:MAX_REFERENCES]

        full_prompt = compose_prompt(prompt, negative_prompt=negative_prompt, ref_images=refs)
        body: Dict[str, Any] = {
            "model": self.model_name,
            "prompt": full_prompt,
            "aspect_ratio": aspect_ratio,
            "resolution": res,
            "response_format": "b64_json",
            "n": 1,
        }

        if refs:
            sources = [{"url": _data_uri(r["path"])} for r in refs]
            if len(sources) == 1:
                body["image"] = sources[0]
            else:
                body["images"] = sources
            endpoint = "/images/edits"
        else:
            endpoint = "/images/generations"

        item, usage = self._post(endpoint, body)
        written = _write_image(item, output_path)

        ticks = (usage or {}).get("cost_in_usd_ticks")
        self.last_cost_usd = round(ticks / _TICKS_PER_USD, 6) if ticks else None
        elapsed = time.time() - started
        logger.info(
            "xai still %s model=%s %s %s refs=%d cost=%s elapsed=%.1fs",
            Path(written).name, self.model_name, aspect_ratio, res,
            len(refs), self.last_cost_usd, elapsed,
        )
        return written, elapsed

    def _post(self, endpoint: str, body: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        import requests

        resp = requests.post(
            f"{self.base_url}{endpoint}",
            headers=self._headers(),
            json=body,
            timeout=self.timeout_s,
        )
        if resp.status_code >= 400:
            raise XaiImageError(
                f"{endpoint} failed HTTP {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()
        items = data.get("data") or []
        if not items:
            raise XaiImageError(f"{endpoint} returned no image: {str(data)[:300]}")
        return items[0], data.get("usage") or {}


def compose_prompt(
    prompt: str,
    *,
    negative_prompt: Optional[str] = None,
    ref_images: Optional[Sequence[Dict[str, str]]] = None,
) -> str:
    """Fold reference labels and exclusions into the prompt text.

    Both are prompt-level here because the API has neither a negative-prompt parameter nor
    a way to name a source image — with several references it addresses them positionally
    as <IMAGE_0>, <IMAGE_1>, so the prompt has to say who is who or faces get swapped.
    """
    parts: List[str] = []
    refs = list(ref_images or [])
    if len(refs) == 1:
        parts.append(
            f"参考图是{refs[0].get('display_name') or '画面人物'}的固定长相，"
            "严格保持其五官、发型与服装特征。"
        )
    elif refs:
        who = "，".join(
            f"<IMAGE_{i}> 是{r.get('display_name') or f'人物{i + 1}'}"
            for i, r in enumerate(refs)
        )
        parts.append(f"{who}。严格保持每个人各自的五官、发型与服装特征，不要互相串脸。")

    parts.append(prompt.strip())
    negative = (negative_prompt or "").strip()
    if negative:
        parts.append(f"避免出现：{negative}。")
    return "\n".join(p for p in parts if p)


def _data_uri(path: str | Path) -> str:
    """Inline a local portrait; the API accepts public URLs or base64 data URLs."""
    p = Path(path)
    if not p.is_file():
        raise XaiImageError(f"reference image not found: {p}")
    suffix = p.suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp"}.get(suffix)
    if mime is None:
        raise XaiImageError(f"reference image must be PNG, JPEG or WebP: {p}")
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")


def _write_image(item: Dict[str, Any], output_path: str) -> str:
    payload = item.get("b64_json")
    if not payload:
        raise XaiImageError("response carried no b64_json payload")
    out = Path(output_path)
    ext = _EXT_BY_MIME.get((item.get("mime_type") or "").lower())
    if ext and out.suffix.lower() != ext:
        out = out.with_suffix(ext)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(payload))
    return str(out)
