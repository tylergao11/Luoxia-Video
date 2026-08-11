from __future__ import annotations

from pathlib import Path

from src.output_contract import DEFAULT_OUTPUT_ROOT, OutputLayout, REPO_ROOT


CONTRACTS_DIR = REPO_ROOT / "contracts"
TIMELINE_SCHEMA_PATH = CONTRACTS_DIR / "timeline.schema.json"
TIMELINE_EXAMPLE_PATH = CONTRACTS_DIR / "examples" / "timeline.example.json"
BEATS_SCHEMA_PATH = CONTRACTS_DIR / "beats.schema.json"
BEATS_EXAMPLE_PATH = CONTRACTS_DIR / "examples" / "beats.example.json"


def output_layout(output_root: Path | str = DEFAULT_OUTPUT_ROOT) -> OutputLayout:
    return OutputLayout.from_root(output_root)


def project_dir(output_root: Path | str, project_id: str) -> Path:
    return output_layout(output_root).project_dir(project_id)


def episode_dir(output_root: Path | str, episode_id: str) -> Path:
    return output_layout(output_root).episode_dir(episode_id)


def timeline_path(output_root: Path | str, episode_id: str) -> Path:
    return episode_dir(output_root, episode_id) / "timeline.json"


def timeline_frozen_path(output_root: Path | str, episode_id: str) -> Path:
    return episode_dir(output_root, episode_id) / "timeline.frozen.json"


def beats_path(output_root: Path | str, work_id: str) -> Path:
    return project_dir(output_root, work_id) / "beats.json"
