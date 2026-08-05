from __future__ import annotations

import math
import wave
from pathlib import Path


def measure_wav_duration_s(path: str | Path) -> float:
    """Read exact PCM duration from the WAV container's frame count."""
    media = Path(path)
    with wave.open(str(media), "rb") as source:
        frame_rate = source.getframerate()
        frame_count = source.getnframes()
    if frame_rate <= 0:
        raise ValueError(f"invalid WAV frame rate for {media}")
    return frame_count / frame_rate


def trim_wav_file(
    source_path: str | Path,
    output_path: str | Path,
    *,
    start_s: float,
    end_s: float,
) -> float:
    """Copy an exact PCM frame window and return the actual retained start time."""
    source_file = Path(source_path)
    output_file = Path(output_path)
    with wave.open(str(source_file), "rb") as source:
        if source.getcomptype() != "NONE":
            raise ValueError(f"compressed WAV is unsupported: {source_file}")
        frame_rate = source.getframerate()
        frame_count = source.getnframes()
        if frame_rate <= 0 or frame_count <= 0:
            raise ValueError(f"invalid WAV stream: {source_file}")

        first_frame = max(0, min(frame_count - 1, int(float(start_s) * frame_rate)))
        final_frame = max(
            first_frame + 1,
            min(frame_count, math.ceil(float(end_s) * frame_rate)),
        )
        source.setpos(first_frame)
        frames = source.readframes(final_frame - first_frame)
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        compression = source.getcomptype()
        compression_name = source.getcompname()

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_file), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(sample_width)
        target.setframerate(frame_rate)
        target.setcomptype(compression, compression_name)
        target.writeframes(frames)
    return first_frame / frame_rate
