from .duration import require_request_duration

__all__ = ["require_request_duration", "render_timeline_videos"]


def __getattr__(name: str):
    if name == "render_timeline_videos":
        from .runner import render_timeline_videos

        return render_timeline_videos
    raise AttributeError(name)
