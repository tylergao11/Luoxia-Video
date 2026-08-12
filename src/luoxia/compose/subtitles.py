from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.luoxia.media.geometry import frame_size  # re-exported: callers ask subtitles for it

DEFAULT_STYLE: Dict[str, Any] = {
    "font_name": "Source Han Sans SC",
    "font_size_px": None,
    "bold": True,
    "primary_color_hex": "#FFFFFF",
    "outline_color_hex": "#000000",
    "outline_px": 2,
    "shadow_px": 0,
    "position": "bottom",
    "margin_v_px": None,
    "margin_h_px": None,
    "max_chars_per_line": 16,
    "max_lines_per_cue": 2,
}

# Landscape drama conventions, all relative to the real frame so they survive a
# resolution change: caption height ~5% of frame height, title-safe bottom margin ~6%,
# side margins ~8% of width.
FONT_SIZE_RATIO = 0.05
MARGIN_V_RATIO = 0.06
MARGIN_H_RATIO = 0.08

# CJK glyphs advance about one em; leave headroom for the outline.
GLYPH_ADVANCE_RATIO = 1.05

# ASS v4+ alignment is numpad-style: 2 = bottom centre, 5 = middle, 8 = top.
_ALIGNMENT = {"bottom": 2, "middle": 5, "top": 8}

# Split points that survive as line/cue boundaries, punctuation kept on the left.
_UNIT_BOUNDARY = re.compile(r"(?<=[。！？!?；;，,、：:…\u2014])")


class SubtitleCue(tuple):
    """(start_s, end_s, text) with newlines already inserted."""

    __slots__ = ()

    def __new__(cls, start: float, end: float, text: str) -> "SubtitleCue":
        return super().__new__(cls, (start, end, text))

    @property
    def start(self) -> float:
        return self[0]

    @property
    def end(self) -> float:
        return self[1]

    @property
    def text(self) -> str:
        return self[2]


def resolve_style(timeline: Dict[str, Any]) -> Dict[str, Any]:
    """Merge global.subtitle_style over defaults. Geometry stays unresolved."""
    style = dict(DEFAULT_STYLE)
    style.update((timeline.get("global") or {}).get("subtitle_style") or {})
    return style


def style_for_frame(style: Dict[str, Any], width: int, height: int) -> Dict[str, Any]:
    """Resolve every size against the real frame, in real pixels.

    max_chars_per_line is also clamped to what physically fits: libass would otherwise
    re-wrap a too-wide line and silently blow past max_lines_per_cue.
    """
    resolved = dict(style)
    if resolved.get("font_size_px") is None:
        resolved["font_size_px"] = max(12, int(round(height * FONT_SIZE_RATIO)))
    if resolved.get("margin_v_px") is None:
        resolved["margin_v_px"] = int(round(height * MARGIN_V_RATIO))
    if resolved.get("margin_h_px") is None:
        resolved["margin_h_px"] = int(round(width * MARGIN_H_RATIO))

    usable = width - 2 * float(resolved["margin_h_px"])
    fitting = int(usable // (float(resolved["font_size_px"]) * GLYPH_ADVANCE_RATIO))
    resolved["max_chars_per_line"] = max(4, min(int(resolved["max_chars_per_line"]), fitting))
    return resolved


def resolve_position(shot: Dict[str, Any], style: Dict[str, Any]) -> str:
    override = (shot.get("subtitle") or {}).get("position")
    return override or str(style.get("position") or "bottom")


def resolve_shot_style(shot: Dict[str, Any], style: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a deliberate shot-local typography exception, normally a title card."""
    resolved = dict(style)
    resolved.update((shot.get("subtitle") or {}).get("style") or {})
    return resolved


def _ass_color(hex_color: str) -> str:
    value = str(hex_color).lstrip("#")
    if len(value) != 6:
        value = "FFFFFF"
    r, g, b = value[0:2], value[2:4], value[4:6]
    return f"&H00{b}{g}{r}".upper()


def _num(value: Any) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _split_units(text: str, max_chars: int) -> List[str]:
    units: List[str] = []
    for raw in _UNIT_BOUNDARY.split(text):
        unit = raw.strip()
        if not unit:
            continue
        while len(unit) > max_chars:
            units.append(unit[:max_chars])
            unit = unit[max_chars:]
        if unit:
            units.append(unit)
    return units


def _pack(units: Sequence[str], max_chars: int, max_lines: int) -> List[List[str]]:
    lines: List[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) > max_chars:
            lines.append(current)
            current = unit
        else:
            current += unit
    if current:
        lines.append(current)
    return [lines[i:i + max_lines] for i in range(0, len(lines), max_lines)]


def build_cues(
    text: str,
    *,
    start: float,
    end: float,
    style: Dict[str, Any],
) -> List[SubtitleCue]:
    """Wrap and split a line into cues, allocating time by character count.

    A whole dialogue as one cue overflows the frame on long lines, so the text is
    split at punctuation and each cue gets its proportional share of the measured
    speech window.
    """
    body = (text or "").strip()
    if not body:
        return []

    start = max(0.0, float(start))
    end = max(start + 0.05, float(end))
    max_chars = int(style["max_chars_per_line"])
    max_lines = int(style["max_lines_per_cue"])

    groups = _pack(_split_units(body, max_chars), max_chars, max_lines)
    if not groups:
        return []

    weights = [max(1, sum(len(line) for line in group)) for group in groups]
    total = float(sum(weights))
    span = end - start

    cues: List[SubtitleCue] = []
    cursor = start
    for i, (group, weight) in enumerate(zip(groups, weights)):
        cue_end = end if i == len(groups) - 1 else cursor + span * (weight / total)
        cues.append(SubtitleCue(cursor, max(cursor + 0.05, cue_end), "\n".join(group)))
        cursor = cue_end
    return cues


def shot_subtitle_window(shot: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """Segment-local [start, end] of the subtitle, or None when the shot has no text.

    Prefers the master-clock window the solver wrote (invariant 15 guarantees it sits
    inside the shot) and falls back to lead_in + measured audio.
    """
    subtitle = shot.get("subtitle") or {}
    if not (subtitle.get("text") or "").strip():
        return None

    timing = shot.get("timing") or {}
    shot_start = timing.get("start_s")
    if subtitle.get("start_s") is not None and subtitle.get("end_s") is not None and shot_start is not None:
        local_start = float(subtitle["start_s"]) - float(shot_start)
        local_end = float(subtitle["end_s"]) - float(shot_start)
        if local_end > local_start >= -1e-6:
            return max(0.0, local_start), local_end

    measured = (shot.get("audio") or {}).get("measured_duration_s")
    if measured is None:
        return None
    lead = float(timing.get("lead_in_s") or 0)
    return lead, lead + float(measured)


def write_ass(
    path: Path,
    cues: Sequence[SubtitleCue],
    *,
    style: Dict[str, Any],
    position: str,
    width: int,
    height: int,
) -> Path:
    """Write an ASS file whose PlayRes matches the frame, so px means real px.

    libass renders SRT against its own default script resolution, which turns every
    margin and font size into a scaled guess. Declaring PlayResX/PlayResY removes that
    indirection. WrapStyle 2 also makes our own line breaks authoritative.
    """
    header = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {int(width)}",
            f"PlayResY: {int(height)}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour,"
            " BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,"
            " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            ",".join(
                [
                    "Style: Default",
                    str(style["font_name"]),
                    _num(style["font_size_px"]),
                    _ass_color(style["primary_color_hex"]),
                    "&H000000FF",
                    _ass_color(style["outline_color_hex"]),
                    "&H00000000",
                    "-1" if style["bold"] else "0",
                    "0", "0", "0",
                    "100", "100", "0", "0",
                    "1",
                    _num(style["outline_px"]),
                    _num(style["shadow_px"]),
                    str(_ALIGNMENT.get(position, 2)),
                    _num(style["margin_h_px"]),
                    _num(style["margin_h_px"]),
                    _num(style["margin_v_px"]),
                    "1",
                ]
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )
    events = [
        f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,"
        + c.text.replace("\n", "\\N")
        for c in cues
    ]
    path.write_text(header + "\n" + "\n".join(events) + "\n", encoding="utf-8")
    return path


def _ass_time(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6_000)
    s, centi = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{centi:02d}"
