from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Upstream adapters import dashscope at module import time; tests stub it.
if "dashscope" not in sys.modules:
    dashscope = types.ModuleType("dashscope")

    class _Dummy:
        def __init__(self, *args, **kwargs):
            pass

        def call(self, *args, **kwargs):
            return b""

        def get_last_request_id(self):
            return "stub"

        def get_first_package_delay(self):
            return 0

    dashscope.VideoSynthesis = _Dummy
    dashscope.ImageSynthesis = _Dummy
    dashscope.MultiModalConversation = types.SimpleNamespace(call=lambda **kwargs: None)
    dashscope.api_key = None
    sys.modules["dashscope"] = dashscope
    sys.modules["dashscope.audio"] = types.ModuleType("dashscope.audio")
    tts_v2 = types.ModuleType("dashscope.audio.tts_v2")
    tts_v2.SpeechSynthesizer = _Dummy
    sys.modules["dashscope.audio.tts_v2"] = tts_v2
