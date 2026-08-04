from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def resolve_video_duration_bounds(
    model_id: Optional[str],
    *,
    catalog: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int]:
    """Read [min, max] integer-second bounds from the generated model catalog.

    Bounds come only from catalog YAML. Unknown models fail closed.
    """
    if not model_id:
        raise ValueError("model_id is required to resolve duration bounds from catalog")

    if catalog is None:
        from src.utils.model_catalog import load_generated_model_catalog

        catalog = load_generated_model_catalog()

    entry = _lookup_catalog_entry(catalog, model_id)
    if entry is None:
        raise KeyError(f"model '{model_id}' not found in model catalog")

    bounds = _duration_bounds(entry.get("duration"))
    if bounds is None:
        raise ValueError(f"model '{model_id}' has no duration slider in catalog")
    return bounds


def _lookup_catalog_entry(catalog: Dict[str, Any], model_id: str) -> Optional[Dict[str, Any]]:
    models = catalog.get("models") or {}
    modes = catalog.get("modes") or {}
    compat = (catalog.get("compat") or {}).get("legacy_model_ids") or {}

    if model_id in models:
        return models[model_id]
    if model_id in modes:
        return modes[model_id]

    canonical = compat.get(model_id)
    if canonical and canonical in modes:
        return modes[canonical]

    # Match api model id / legacy prefix (e.g. grok-imagine-video-1.5 -> *-i2v)
    for payload in list(models.values()) + list(modes.values()):
        legacy = str(payload.get("legacy_model_id") or payload.get("id") or "")
        if legacy == model_id or legacy.startswith(model_id + "-") or legacy.startswith(model_id + "#"):
            if _duration_bounds(payload.get("duration")) is not None:
                return payload
        runtime = payload.get("runtime") or {}
        for backend_payload in runtime.values():
            if isinstance(backend_payload, dict) and backend_payload.get("api_model_id") == model_id:
                if _duration_bounds(payload.get("duration")) is not None:
                    return payload
    return None


def _duration_bounds(duration: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(duration, dict):
        return None
    try:
        mn = int(duration["min"])
        mx = int(duration["max"])
    except (KeyError, TypeError, ValueError):
        return None
    if mn < 1 or mx < mn:
        return None
    return mn, mx


def find_shot_video_model(shot: Dict[str, Any], timeline: Dict[str, Any]) -> Optional[str]:
    video = shot.get("video") or {}
    if video.get("model"):
        return video["model"]
    for candidate in timeline.get("shots") or []:
        model = (candidate.get("video") or {}).get("model")
        if model:
            return model
    return None
