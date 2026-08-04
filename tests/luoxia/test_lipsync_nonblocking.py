from __future__ import annotations

from src.luoxia.lipsync.runner import apply_lipsync


def test_lipsync_failure_does_not_raise(tmp_path):
    tl = {
        "shots": [
            {
                "shot_id": "s1",
                "lipsync": {"required": True, "status": "pending"},
                "video": {"local_path": str(tmp_path / "v.mp4")},
                "audio": {"local_path": str(tmp_path / "a.wav")},
            }
        ]
    }
    (tmp_path / "v.mp4").write_bytes(b"x")
    (tmp_path / "a.wav").write_bytes(b"y")

    def boom(v, a, o):
        raise RuntimeError("engine down")

    apply_lipsync(tl, output_root=tmp_path, engine=boom)
    assert tl["shots"][0]["lipsync"]["status"] == "failed"
