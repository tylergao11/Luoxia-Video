"""Canonical speech-performance planning for Luoxia dialogue.

The dialogue text stays the only text truth.  A performance plan points at character
spans in that text and is compiled into provider markup only at the TTS boundary.
This keeps acting direction out of subtitles, lipsync text and duration accounting.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

STYLE_TAGS = frozenset(
    {
        "soft",
        "whisper",
        "loud",
        "build-intensity",
        "decrease-intensity",
        "higher-pitch",
        "lower-pitch",
        "slow",
        "fast",
        "sing-song",
        "singing",
        "laugh-speak",
        "emphasis",
    }
)

INLINE_TAGS = frozenset(
    {
        "pause",
        "long-pause",
        "hum-tune",
        "laugh",
        "chuckle",
        "giggle",
        "cry",
        "tsk",
        "tongue-click",
        "lip-smack",
        "breath",
        "inhale",
        "exhale",
        "sigh",
    }
)

# Ordered from the most specific/forceful reading to the broadest.  A span gets one
# style only: stacked whole-line tags made the model vocalise control markup and erased
# the contrast that dramatic speech depends on.
_STYLE_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("whisper", ("耳语", "低语", "悄声", "压低声音", "声音极轻")),
    ("laugh-speak", ("笑着说", "带笑", "笑道")),
    ("loud", ("大声", "怒吼", "咆哮", "厉声", "嘶喊", "喊道", "怒不可遏", "吼")),
    ("build-intensity", ("渐强", "越来越激动", "逐渐激动", "情绪上涨", "爆发")),
    ("decrease-intensity", ("渐弱", "平息", "声音低下去")),
    ("higher-pitch", ("尖锐", "拔高", "兴奋")),
    (
        "emphasis",
        ("强调", "加重", "咬字", "咬死", "咬牙", "字字", "一字一句", "一字一顿", "决绝", "轻蔑", "不容置疑"),
    ),
    ("lower-pitch", ("低沉", "沉声", "压抑", "阴冷", "冷静", "冷漠", "冷冷", "更冷", "冷下去")),
    ("slow", ("缓慢", "迟疑", "犹豫", "慢条斯理", "拉长音")),
    ("fast", ("急促", "焦急", "慌乱", "飞快")),
    ("soft", ("温柔", "柔和", "轻柔", "平静", "淡淡", "很轻", "轻声", "克制", "哽咽", "声音发颤", "含泪")),
)

# Deliberately conservative.  In particular, generic 哽咽 is acting direction rather
# than an instruction to insert a long synthetic [cry] performance before the line.
_EVENT_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("sigh", ("叹息", "叹气", "长叹")),
    ("chuckle", ("轻笑", "低笑", "冷笑")),
    ("inhale", ("深吸", "吸气")),
    ("exhale", ("呼气", "吐气")),
    ("breath", ("喘息", "喘着", "气息不稳")),
    ("long-pause", ("长久沉默", "久久停顿")),
    ("pause", ("停顿", "顿一下", "顿了", "短暂停顿", "沉默片刻")),
)

_CLAUSE_RE = re.compile(r"[^。！？!?；;]+[。！？!?；;]?\s*")
_QUOTED_RE = re.compile(r"[“\"『「](.+?)[”\"』」]")
_CALLED_RE = re.compile(r"(?:叫|喊|说到|说出)([^，。！？；;\s]{1,6})时")


def text_sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def normalize_performance(text: str, raw: Any) -> Optional[Dict[str, Any]]:
    """Normalize LLM output into stable character spans.

    The prompt asks the model for exact substrings because models are poor offset
    counters.  Only this function computes offsets, and the duplicated substring is
    discarded before persistence.
    """
    if not isinstance(raw, dict) or not text:
        return None

    intent = str(raw.get("intent") or "").strip() or None
    candidates: List[Dict[str, Any]] = []
    search_from = 0
    event_used = False

    for item in raw.get("segments") or []:
        if not isinstance(item, dict) or len(candidates) >= 4:
            continue
        style = item.get("style") if item.get("style") in STYLE_TAGS else None
        event = item.get("event_before") if item.get("event_before") in INLINE_TAGS else None
        if event_used:
            event = None

        start: Optional[int] = None
        end: Optional[int] = None
        needle = str(item.get("text") or "")
        if needle:
            # Keep exact punctuation in the span.  A second occurrence is searched only
            # after the preceding selected segment, which makes repeated phrases stable.
            start = text.find(needle, search_from)
            if start < 0:
                start = text.find(needle)
            if start >= 0:
                end = start + len(needle)
        else:
            try:
                start = int(item.get("start_char"))
                end = int(item.get("end_char"))
            except (TypeError, ValueError):
                start = end = None

        if start is None or end is None or start < 0 or end <= start or end > len(text):
            continue
        if candidates and start < candidates[-1]["end_char"]:
            continue
        if not style and not event:
            continue

        segment = {
            "start_char": start,
            "end_char": end,
            "style": style,
            "event_before": event,
        }
        candidates.append(segment)
        search_from = end
        event_used = event_used or bool(event)

    if not candidates:
        return None
    return {
        "text_sha256": text_sha256(text),
        "intent": intent,
        "segments": candidates,
    }


def performance_from_direction(text: str, direction: Optional[str]) -> Optional[Dict[str, Any]]:
    """Convert legacy free-form delivery prose into a conservative span plan.

    This is the compatibility path for existing projects.  Direction clauses with an
    anchor (开头/最后一句/质问/quoted words/叫某人时) are localised; an unanchored
    direction may shape the whole line, but still receives only one style and one event.
    """
    note = (direction or "").strip()
    if not text or not note:
        return None

    clauses = _clauses(text)
    directives = [part.strip() for part in re.split(r"[；;。\n]+", note) if part.strip()]
    raw_segments: List[Dict[str, Any]] = []
    for directive in directives:
        style = _infer_style(directive)
        event = _infer_event(directive)
        if not style and not event:
            continue
        start, end = _target_span(text, clauses, directive)
        raw_segments.append(
            {
                "start_char": start,
                "end_char": end,
                "style": style,
                "event_before": event,
            }
        )

    # Sort, drop overlaps, and keep one inline event.  Prefer a localised plan over a
    # whole-line catch-all when both describe the same acting arc.
    raw_segments.sort(key=lambda item: (item["start_char"], item["end_char"] - item["start_char"]))
    selected: List[Dict[str, Any]] = []
    event_used = False
    for item in raw_segments:
        if len(selected) >= 4:
            break
        event = item["event_before"] if not event_used else None
        item = {**item, "event_before": event}
        if selected and item["start_char"] < selected[-1]["end_char"]:
            if item["end_char"] > selected[-1]["end_char"] and item.get("style"):
                # Two meaningful arcs can share a sentence.  Preserve the earlier,
                # narrower opening and start the later arc at its boundary instead of
                # dropping it or nesting incompatible tags.
                item["start_char"] = selected[-1]["end_char"]
                selected.append(item)
                event_used = event_used or bool(event)
                continue
            # Preserve an event at its exact word even when a broad style covers the
            # line. Split that style around the event; moving “叫妈时停顿” to the line's
            # beginning changes the acting beat completely.
            previous = selected[-1]
            if (
                event
                and not previous.get("event_before")
                and item["end_char"] <= previous["end_char"]
            ):
                selected.pop()
                if previous["start_char"] < item["start_char"]:
                    selected.append({**previous, "end_char": item["start_char"]})
                selected.append(
                    {
                        **item,
                        "style": item.get("style") or previous.get("style"),
                        "event_before": event,
                    }
                )
                if item["end_char"] < previous["end_char"]:
                    selected.append({**previous, "start_char": item["end_char"]})
                selected = selected[:4]
                event_used = True
            continue
        selected.append(item)
        event_used = event_used or bool(event)

    if not selected:
        return None
    return {
        "text_sha256": text_sha256(text),
        "intent": note,
        "segments": selected,
    }


def compile_performance(
    text: str,
    performance: Any = None,
    legacy_direction: Optional[str] = None,
) -> Tuple[str, List[str], Optional[Dict[str, Any]]]:
    """Compile one canonical plan into xAI markup without nesting styles."""
    plan = normalize_performance(text, performance)
    if isinstance(performance, dict) and performance.get("text_sha256"):
        if performance.get("text_sha256") != text_sha256(text):
            plan = None
    if plan is None:
        plan = performance_from_direction(text, legacy_direction)
    if plan is None:
        return text, [], None

    cursor = 0
    chunks: List[str] = []
    applied: List[str] = []
    for segment in plan["segments"]:
        start = segment["start_char"]
        end = segment["end_char"]
        chunks.append(text[cursor:start])
        event = segment.get("event_before")
        style = segment.get("style")
        if event:
            chunks.append(f"[{event}]")
            applied.append(f"[{event}]@{start}")
        phrase = text[start:end]
        if style:
            chunks.append(f"<{style}>{phrase}</{style}>")
            applied.append(f"<{style}>@{start}:{end}")
        else:
            chunks.append(phrase)
        cursor = end
    chunks.append(text[cursor:])
    return "".join(chunks), applied, plan


def validate_performance(text: str, performance: Any) -> List[str]:
    """Return semantic contract errors that JSON Schema cannot express."""
    if performance is None:
        return []
    if not isinstance(performance, dict):
        return ["performance must be an object or null"]

    errors: List[str] = []
    expected_hash = text_sha256(text)
    if performance.get("text_sha256") != expected_hash:
        errors.append("text_sha256 does not match the current dialogue text")
    previous_end = 0
    event_count = 0
    for index, segment in enumerate(performance.get("segments") or []):
        if not isinstance(segment, dict):
            continue
        start = segment.get("start_char")
        end = segment.get("end_char")
        if not isinstance(start, int) or not isinstance(end, int):
            errors.append(f"segments[{index}] requires integer character offsets")
            continue
        if start < 0 or end <= start or end > len(text):
            errors.append(f"segments[{index}] range [{start}, {end}) is outside text length {len(text)}")
        if start < previous_end:
            errors.append(f"segments[{index}] overlaps or precedes the previous segment")
        previous_end = max(previous_end, end)
        if not segment.get("style") and not segment.get("event_before"):
            errors.append(f"segments[{index}] has neither style nor event_before")
        if segment.get("event_before"):
            event_count += 1
    if event_count > 1:
        errors.append("a line may contain at most one inline event")
    return errors


def clean_audio_timestamps(timings: Any, expected_text: str) -> Optional[Dict[str, Any]]:
    """Remove provider control markup from xAI graph timestamps.

    xAI returns a timestamp entry for tag characters as well as spoken text.  Subtitle
    timing must expose only the dialogue string.  We first strip known markup and then
    align the remaining sequence to the exact source text; a failed alignment is not
    persisted as if it were trustworthy.
    """
    if not isinstance(timings, dict):
        return None
    chars = timings.get("graph_chars")
    times = timings.get("graph_times")
    if not isinstance(chars, list) or not isinstance(times, list) or len(chars) != len(times):
        return None
    if any(not isinstance(ch, str) or len(ch) != 1 for ch in chars):
        return None

    joined = "".join(chars)
    excluded: set[int] = set()
    for match in _control_markup_re().finditer(joined):
        excluded.update(range(match.start(), match.end()))
    filtered: Sequence[Tuple[str, Any]] = [
        (ch, timing) for index, (ch, timing) in enumerate(zip(chars, times)) if index not in excluded
    ]

    aligned_chars: List[str] = []
    aligned_times: List[Any] = []
    cursor = 0
    for wanted in expected_text:
        while cursor < len(filtered) and filtered[cursor][0] != wanted:
            cursor += 1
        if cursor >= len(filtered):
            return None
        aligned_chars.append(wanted)
        aligned_times.append(filtered[cursor][1])
        cursor += 1

    cleaned = dict(timings)
    cleaned["graph_chars"] = aligned_chars
    cleaned["graph_times"] = aligned_times
    return cleaned


def _clauses(text: str) -> List[Tuple[int, int, str]]:
    found = [(m.start(), m.end(), m.group(0)) for m in _CLAUSE_RE.finditer(text)]
    return found or [(0, len(text), text)]


def _infer_style(note: str) -> Optional[str]:
    for style, keywords in _STYLE_KEYWORDS:
        if any(keyword in note for keyword in keywords):
            return style
    return None


def _infer_event(note: str) -> Optional[str]:
    for event, keywords in _EVENT_KEYWORDS:
        if any(keyword in note for keyword in keywords):
            return event
    return None


def _target_span(
    text: str,
    clauses: Sequence[Tuple[int, int, str]],
    directive: str,
) -> Tuple[int, int]:
    quoted = _QUOTED_RE.search(directive)
    if quoted:
        start = text.find(quoted.group(1))
        if start >= 0:
            return start, start + len(quoted.group(1))

    called = _CALLED_RE.search(directive)
    if called:
        target = called.group(1)
        start = text.find(target)
        if start >= 0:
            return start, start + len(target)

    if any(word in directive for word in ("开头", "起初", "一开始", "前半句")):
        first_start, first_end, first_text = clauses[0]
        opening = re.search(r"^.*?[，,：:]", first_text)
        return first_start, first_start + opening.end() if opening else first_end
    if any(word in directive for word in ("最后一句", "最后", "结尾", "收尾", "后半句")):
        return clauses[-1][0], clauses[-1][1]
    if any(word in directive for word in ("质问", "反问", "问句")):
        for start, end, clause in clauses:
            if "？" in clause or "?" in clause:
                return start, end
    return 0, len(text)


def _control_markup_re() -> re.Pattern[str]:
    styles = "|".join(sorted((re.escape(tag) for tag in STYLE_TAGS), key=len, reverse=True))
    events = "|".join(sorted((re.escape(tag) for tag in INLINE_TAGS), key=len, reverse=True))
    return re.compile(rf"(?:</?(?:{styles})>|\[(?:{events})\])")
