from __future__ import annotations

# Wanx / DashScope size strings. Landscape 16:9 is the short-drama default.
ASPECT_TO_SIZE = {
    "16:9": "1280*720",
    "9:16": "720*1280",
    "1:1": "1024*1024",
    "4:3": "1280*960",
    "3:4": "960*1280",
    "3:2": "1280*854",
    "2:3": "854*1280",
}


def size_for_aspect(aspect_ratio: str) -> str:
    return ASPECT_TO_SIZE.get(aspect_ratio, "1280*720")
