from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

# Adapters register pricing here. Business code never hardcodes vendor rates.
# Signature: (resolution: str) -> (per_second_usd, per_image_usd)
PricingFn = Callable[[str], Tuple[float, float]]

_REGISTRY: Dict[str, PricingFn] = {}


def register_pricing(provider: str, fn: PricingFn) -> None:
    key = (provider or "").strip().lower()
    if not key:
        raise ValueError("provider is required")
    _REGISTRY[key] = fn


def get_pricing(provider: str) -> PricingFn:
    key = (provider or "").strip().lower()
    if key not in _REGISTRY:
        # Lazy import adapters that self-register.
        if key == "xai":
            from src.models import grok as _grok  # noqa: F401
        if key not in _REGISTRY:
            raise KeyError(f"no pricing registered for provider '{provider}'")
    return _REGISTRY[key]


def estimate_shot_video_cost_usd(
    *,
    provider: Optional[str],
    resolution: str,
    request_duration_s: int,
    has_input_image: bool,
) -> float:
    if not provider:
        raise ValueError("video.provider is required for cost estimation")
    per_second, per_image = get_pricing(provider)(resolution)
    cost = request_duration_s * per_second
    if has_input_image:
        cost += per_image
    return cost
