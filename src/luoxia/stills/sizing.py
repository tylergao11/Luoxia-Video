from __future__ import annotations

# Wanx / DashScope size strings. Prefer portrait for short-drama default 9:16.
ASPECT_TO_SIZE = {
    "9:16": "720*1280",
    "16:9": "1280*720",
    "1:1": "1024*1024",
    "4:3": "1280*960",
    "3:4": "960*1280",
    "3:2": "1280*854",
    "2:3": "854*1280",
}


def size_for_aspect(aspect_ratio: str) -> str:
    return ASPECT_TO_SIZE.get(aspect_ratio, "720*1280")
