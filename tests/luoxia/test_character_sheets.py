from __future__ import annotations

import pytest

from src.luoxia.stills.characters import (
    CharacterSheetError,
    ensure_character_sheets,
    reference_map,
    refs_for_shot,
)
from src.luoxia.stills.runner import render_timeline_stills


def _recorder(tmp_path):
    calls = []

    def generate(prompt, output_path, *, size, negative_prompt=None, ref_image_paths=None):
        calls.append(
            {
                "prompt": prompt,
                "output": output_path,
                "size": size,
                "refs": list(ref_image_paths or []),
            }
        )
        from pathlib import Path

        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"png")
        return str(p)

    return generate, calls


def _cast():
    return [
        {"character_id": "lin_wan", "display_name": "林晚", "appearance": "黑发挽起，灰呢外套"},
        {"character_id": "shen_ce", "display_name": "沈策", "appearance": "深色西装，短发"},
    ]


def test_one_sheet_per_character_and_path_written_back(tmp_path):
    gen, calls = _recorder(tmp_path)
    cast = _cast()
    sheets = ensure_character_sheets(cast, output_root=tmp_path, generate=gen)

    assert set(sheets) == {"lin_wan", "shen_ce"}
    assert len(calls) == 2
    assert all(c["appearance"] in call["prompt"] for c, call in zip(cast, calls))
    assert cast[0]["reference_image_path"] == sheets["lin_wan"]


def test_sheets_are_cached_across_runs(tmp_path):
    gen, calls = _recorder(tmp_path)
    cast = _cast()
    ensure_character_sheets(cast, output_root=tmp_path, generate=gen)
    ensure_character_sheets(_cast(), output_root=tmp_path, generate=gen)
    assert len(calls) == 2, "second run must reuse the cached portraits"


def test_changed_appearance_invalidates_the_cache(tmp_path):
    gen, calls = _recorder(tmp_path)
    ensure_character_sheets(_cast(), output_root=tmp_path, generate=gen)
    changed = _cast()
    changed[0]["appearance"] = "白发，黑色风衣"
    ensure_character_sheets(changed, output_root=tmp_path, generate=gen)
    assert len(calls) == 3


def test_missing_appearance_can_be_made_fatal(tmp_path):
    gen, _ = _recorder(tmp_path)
    cast = [{"character_id": "x", "display_name": "X", "appearance": None}]
    assert ensure_character_sheets(cast, output_root=tmp_path, generate=gen) == {}
    with pytest.raises(CharacterSheetError):
        ensure_character_sheets(cast, output_root=tmp_path, generate=gen, require_appearance=True)


def test_stills_pass_each_speaker_portrait_as_reference(tmp_path):
    gen, calls = _recorder(tmp_path)
    cast = _cast()
    ensure_character_sheets(cast, output_root=tmp_path, generate=gen)

    timeline = {
        "global": {"aspect_ratio": "9:16"},
        "cast": [
            {
                "character_id": c["character_id"],
                "display_name": c["display_name"],
                "voice_id": "v",
                "reference_image_asset_id": c["reference_image_path"],
            }
            for c in cast
        ],
        "shots": [
            {"shot_id": "s1", "characters": ["lin_wan"], "still": {"prompt": "近景"}},
            {"shot_id": "s2", "characters": ["shen_ce", "lin_wan"], "still": {"prompt": "对峙"}},
            {"shot_id": "s3", "characters": [], "still": {"prompt": "空镜"}},
        ],
    }
    render_timeline_stills(timeline, output_root=tmp_path, generate=gen)

    shots = {s["shot_id"]: s for s in timeline["shots"]}
    assert shots["s1"]["still"]["reference_image_paths"] == [cast[0]["reference_image_path"]]
    assert len(shots["s2"]["still"]["reference_image_paths"]) == 2
    assert "reference_image_paths" not in shots["s3"]["still"]
    assert all(s["still"]["status"] == "ready" for s in timeline["shots"])

    by_out = {c["output"]: c for c in calls}
    s1_call = next(c for out, c in by_out.items() if out.endswith("s1.png"))
    assert s1_call["refs"] == [cast[0]["reference_image_path"]]


def test_reference_map_ignores_missing_files(tmp_path):
    timeline = {
        "cast": [
            {"character_id": "a", "reference_image_asset_id": str(tmp_path / "nope.png")},
            {"character_id": "b", "reference_image_asset_id": None},
        ]
    }
    assert reference_map(timeline) == {}


def test_refs_for_shot_dedupes_and_caps():
    refs = {"a": "/a.png", "b": "/b.png", "c": "/c.png", "d": "/d.png"}
    shot = {"characters": ["a", "a", "b", "c", "d"]}
    assert refs_for_shot(shot, refs) == ["/a.png", "/b.png", "/c.png"]
