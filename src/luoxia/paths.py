from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "contracts"
TIMELINE_SCHEMA_PATH = CONTRACTS_DIR / "timeline.schema.json"
TIMELINE_EXAMPLE_PATH = CONTRACTS_DIR / "examples" / "timeline.example.json"
BEATS_SCHEMA_PATH = CONTRACTS_DIR / "beats.schema.json"
BEATS_EXAMPLE_PATH = CONTRACTS_DIR / "examples" / "beats.example.json"


def episode_dir(output_root: Path | str, episode_id: str) -> Path:
    return Path(output_root) / episode_id


def timeline_path(output_root: Path | str, episode_id: str) -> Path:
    return episode_dir(output_root, episode_id) / "timeline.json"


def timeline_frozen_path(output_root: Path | str, episode_id: str) -> Path:
    return episode_dir(output_root, episode_id) / "timeline.frozen.json"


def beats_path(output_root: Path | str, work_id: str) -> Path:
    return Path(output_root) / work_id / "beats.json"
