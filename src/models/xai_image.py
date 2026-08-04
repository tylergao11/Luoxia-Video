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

        `ref_images` are ordered sources as `{"display_name", "path", "role"?}`:
          - role ``identity`` (default): lock that character's face/hair/costume
          - role ``style``: copy render language only (Hongguo AI-manhua medium), not identity

        The written path can differ from `output_path` in extension: the API chooses the
        encoding, and a JPEG saved as `.png` would later be handed to the video API with
        the wrong MIME type.
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


def _ref_role(ref: Dict[str, Any]) -> str:
    """Normalize reference role. Default is identity (backward compatible)."""
    role = (ref.get("role") or ref.get("ref_role") or "identity").strip().lower()
    if role in {"style", "style_ref", "medium", "look"}:
        return "style"
    return "identity"


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

    Critical role split (IP-Adapter-style practice, text-instructed for xAI edits):
      - style: teach 红果 AI 漫 render language (face polish, porcelain skin, volume light)
        WITHOUT locking the ref character's identity, hair color, costume, or UI chrome
      - identity: lock that character's face / hair / costume across shots
    """
    parts: List[str] = []
    refs = list(ref_images or [])
    if refs:
        parts.append(_compose_ref_instructions(refs))

    parts.append(prompt.strip())
    negative = (negative_prompt or "").strip()
    if negative:
        parts.append(f"避免出现：{negative}。")
    return "\n".join(p for p in parts if p)


def _compose_ref_instructions(refs: Sequence[Dict[str, Any]]) -> str:
    """Build positional role instructions for style vs identity references."""
    multi = len(refs) > 1
    chunks: List[str] = []
    style_idxs: List[int] = []
    identity_idxs: List[int] = []

    for i, ref in enumerate(refs):
        role = _ref_role(ref)
        label = (ref.get("display_name") or "").strip() or (f"参考{i + 1}" if multi else "参考图")
        tag = f"<IMAGE_{i}>" if multi else "参考图"
        if role == "style":
            style_idxs.append(i)
            # Keep "<IMAGE_n> 是…" prefix so multi-ref addressing stays explicit.
            chunks.append(
                f"{tag} 是{label}（风格参考，只学介质）：尽量做到与参考几乎同一套渲染语言——"
                "虚幻引擎级材质密度、锋利骨相与修长脸、窄长眼型与冷高光、瓷光无毛孔皮肤、"
                "发丝丝缕与布料纤维高细节、戏剧体积光与暗部层次、红果封面级精修压迫感。"
                "输出必须是成年向精致3D漫剧角色，不要幼态Q版大圆眼，不要真人写真。"
                "禁止复制该图人物的身份五官、银发白发、红瞳、服装图案、配饰、字幕、UI或备案号。"
            )
        else:
            identity_idxs.append(i)
            # Identity default keeps historical phrasing for single-ref callers/tests.
            if multi:
                chunks.append(
                    f"{tag} 是{label}（角色身份参考）：严格保持其五官、发型与服装特征，不要串脸。"
                )
            else:
                chunks.append(
                    f"参考图是{label}的固定长相，严格保持其五官、发型与服装特征。"
                )

    if style_idxs and not identity_idxs:
        chunks.append(
            "本请求仅有风格参考：材质/脸模锋利度/瓷光/戏剧光必须与风格参考几乎一致（同一风格族），"
            "允许换角色身份与服装；禁止幼态Q版、偶像写真、毛孔写实、2D赛璐璐。"
        )
    elif style_idxs and identity_idxs:
        chunks.append(
            "风格参考只决定介质与光妆；角色身份参考只决定是谁。"
            "禁止把风格参考里的人物当成剧中角色。"
        )
    elif multi and identity_idxs:
        chunks.append("严格保持每个人各自的五官、发型与服装特征，不要互相串脸。")

    return "\n".join(chunks)


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
