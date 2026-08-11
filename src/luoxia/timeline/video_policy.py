from __future__ import annotations

from typing import Any, Dict


# One default policy, serialized into every new timeline. Runtime code reads the
# timeline snapshot instead of carrying a second set of hidden thresholds.
DEFAULT_VIDEO_ACCEPTANCE_POLICY: Dict[str, Any] = {
    "checker": "ffmpeg_freezedetect",
    "duration_tolerance_s": 0.04,
    "freeze_noise_db": -35.0,
    "freeze_min_duration_s": 0.5,
    "max_freeze_segment_s": 1.0,
}

ACTION_ARC_DURATION_TOLERANCE_S = 0.05


def default_video_acceptance_policy() -> Dict[str, Any]:
    """Return a fresh serializable copy for a timeline or standalone task."""
    return dict(DEFAULT_VIDEO_ACCEPTANCE_POLICY)


def validate_video_acceptance_policy(policy: Any) -> Dict[str, Any]:
    if not isinstance(policy, dict):
        raise ValueError("video acceptance policy must be an object")
    missing = [key for key in DEFAULT_VIDEO_ACCEPTANCE_POLICY if policy.get(key) is None]
    if missing:
        raise ValueError("video acceptance policy missing: " + ", ".join(missing))
    if policy.get("checker") != "ffmpeg_freezedetect":
        raise ValueError(
            f"unsupported video acceptance checker: {policy.get('checker')!r}"
        )
    if float(policy["duration_tolerance_s"]) < 0:
        raise ValueError("duration_tolerance_s must be >= 0")
    if float(policy["freeze_min_duration_s"]) <= 0:
        raise ValueError("freeze_min_duration_s must be > 0")
    if float(policy["max_freeze_segment_s"]) < float(policy["freeze_min_duration_s"]):
        raise ValueError(
            "max_freeze_segment_s must be >= freeze_min_duration_s so violations are observable"
        )
    return dict(policy)


def video_acceptance_policy(timeline: Dict[str, Any]) -> Dict[str, Any]:
    """Read the policy from the timeline, failing instead of inventing defaults."""
    policy = (timeline.get("global") or {}).get("video_acceptance")
    if policy is None:
        raise ValueError("global.video_acceptance missing; refuse uncontracted video review")
    return validate_video_acceptance_policy(policy)
