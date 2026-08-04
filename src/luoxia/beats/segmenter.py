from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Paragraph:
    """A deterministic slice of the source with offsets computed by code, not guessed."""

    index: int
    start_char: int
    end_char: int
    text: str


# LLMs cannot count characters. They can point at a numbered line.
# So the model only ever returns paragraph indices and we own every offset.
_SPLIT_RE = re.compile(r"\n\s*\n+")
_SENTENCE_RE = re.compile(r"(?<=[。！？!?…])")
MAX_PARAGRAPH_CHARS = 320


def split_paragraphs(text: str, *, max_chars: int = MAX_PARAGRAPH_CHARS) -> List[Paragraph]:
    """Split source text into addressable units covering every character exactly once."""
    if not text:
        return []
    out: List[Paragraph] = []
    cursor = 0
    for raw in _SPLIT_RE.split(text):
        if not raw:
            continue
        found = text.find(raw, cursor)
        if found < 0:
            continue
        for start, end in _subdivide(raw, found, max_chars):
            if end <= start:
                continue
            out.append(
                Paragraph(index=len(out), start_char=start, end_char=end, text=text[start:end])
            )
        cursor = found + len(raw)

    if not out:
        out = [Paragraph(index=0, start_char=0, end_char=len(text), text=text)]
    return out


def _subdivide(block: str, offset: int, max_chars: int) -> List[Tuple[int, int]]:
    """Break an over-long paragraph on sentence ends so beats can address finer units."""
    if len(block) <= max_chars:
        return [(offset, offset + len(block))]
    spans: List[Tuple[int, int]] = []
    local = 0
    buf_start = 0
    for piece in _SENTENCE_RE.split(block):
        if not piece:
            continue
        local += len(piece)
        if local - buf_start >= max_chars:
            spans.append((offset + buf_start, offset + local))
            buf_start = local
    if buf_start < len(block):
        spans.append((offset + buf_start, offset + len(block)))
    return spans


def render_numbered(paragraphs: Sequence[Paragraph]) -> str:
    """The exact view the model sees: one indexed line per addressable unit."""
    return "\n".join(f"[{p.index}] {p.text.strip()}" for p in paragraphs)


def span_for_range(
    paragraphs: Sequence[Paragraph],
    para_start: int,
    para_end: int,
) -> Optional[Dict[str, int]]:
    """Convert an inclusive paragraph range into exact character offsets."""
    if not paragraphs:
        return None
    by_index = {p.index: p for p in paragraphs}
    lo, hi = sorted((int(para_start), int(para_end)))
    lo = max(lo, min(by_index))
    hi = min(hi, max(by_index))
    if lo > hi or lo not in by_index or hi not in by_index:
        return None
    return {"start_char": by_index[lo].start_char, "end_char": by_index[hi].end_char}


def coverage_gaps(
    paragraphs: Sequence[Paragraph],
    claimed: Sequence[Tuple[int, int]],
) -> List[Tuple[int, int]]:
    """Paragraph ranges the model never assigned to any beat. These become filler beats."""
    if not paragraphs:
        return []
    taken = set()
    for lo, hi in claimed:
        for i in range(min(lo, hi), max(lo, hi) + 1):
            taken.add(i)

    gaps: List[Tuple[int, int]] = []
    run_start: Optional[int] = None
    for p in paragraphs:
        if p.index in taken:
            if run_start is not None:
                gaps.append((run_start, p.index - 1))
                run_start = None
        elif run_start is None:
            run_start = p.index
    if run_start is not None:
        gaps.append((run_start, paragraphs[-1].index))
    return gaps


def chunk_paragraphs(
    paragraphs: Sequence[Paragraph],
    *,
    max_chars: int,
) -> List[List[Paragraph]]:
    """Group paragraphs into LLM-sized batches without ever splitting a paragraph."""
    chunks: List[List[Paragraph]] = []
    current: List[Paragraph] = []
    size = 0
    for p in paragraphs:
        length = p.end_char - p.start_char
        if current and size + length > max_chars:
            chunks.append(current)
            current = []
            size = 0
        current.append(p)
        size += length
    if current:
        chunks.append(current)
    return chunks or [list(paragraphs)]


def excerpt_for(text: str, span: Dict[str, Any], *, limit: int = 60) -> str:
    """Excerpt is always derived from the source, never taken from the model."""
    start = int(span.get("start_char") or 0)
    end = int(span.get("end_char") or start)
    snippet = text[start:end].strip().replace("\n", " ")
    return snippet[:limit]
