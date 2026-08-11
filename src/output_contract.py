from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "output"

_INVALID_SEGMENT_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_segment(value: str, *, label: str) -> str:
    """Validate one user/model supplied directory segment.

    Output identities are directory names, never paths. Refusing separators here
    keeps every producer inside the output contract instead of relying on each
    caller to remember its own traversal check.
    """

    candidate = str(value or "").strip()
    stem = candidate.split(".", 1)[0].upper()
    if (
        not candidate
        or candidate in {".", ".."}
        or _INVALID_SEGMENT_CHARS.search(candidate)
        or candidate.endswith((" ", "."))
        or stem in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError(
            f"invalid {label}={value!r}; expected one filesystem-safe directory name"
        )
    return candidate


@dataclass(frozen=True)
class OutputLayout:
    """Single directory contract for every locally managed artifact."""

    root: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "OutputLayout":
        return cls(Path(root).expanduser().resolve())

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    @property
    def episodes(self) -> Path:
        return self.root / "episodes"

    @property
    def studio(self) -> Path:
        return self.root / "studio"

    @property
    def state(self) -> Path:
        return self.studio / "state"

    @property
    def media(self) -> Path:
        return self.studio / "media"

    @property
    def assets(self) -> Path:
        return self.media / "assets"

    @property
    def storyboard(self) -> Path:
        return self.media / "storyboard"

    @property
    def audio(self) -> Path:
        return self.media / "audio"

    @property
    def video(self) -> Path:
        return self.media / "video"

    @property
    def exports(self) -> Path:
        return self.media / "exports"

    @property
    def uploads(self) -> Path:
        return self.media / "uploads"

    @property
    def presets(self) -> Path:
        return self.studio / "presets"

    @property
    def cache(self) -> Path:
        return self.studio / "cache"

    @property
    def work(self) -> Path:
        return self.studio / "work"

    @property
    def playground(self) -> Path:
        return self.studio / "playground"

    @property
    def playground_images(self) -> Path:
        return self.playground / "images"

    @property
    def playground_videos(self) -> Path:
        return self.playground / "videos"

    @property
    def samples(self) -> Path:
        return self.root / "samples"

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    def project_dir(self, project_id: str) -> Path:
        return self.projects / safe_segment(project_id, label="project_id")

    def episode_dir(self, episode_id: str) -> Path:
        return self.episodes / safe_segment(episode_id, label="episode_id")

    def sample_dir(self, sample_id: str) -> Path:
        return self.samples / safe_segment(sample_id, label="sample_id")

    def relative(self, path: str | Path) -> Path:
        """Return a contract-relative path or fail when *path* escapes root."""

        resolved = Path(path).expanduser().resolve()
        try:
            return resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path is outside output root: {resolved}") from exc

    def relative_posix(self, path: str | Path) -> str:
        return self.relative(path).as_posix()

    def resolve(self, reference: str | Path) -> Path:
        """Resolve one local media reference under this output root, fail closed."""

        raw = str(reference or "").strip()
        if not raw:
            raise ValueError("empty output reference")
        if raw.startswith("/files/"):
            raw = raw[len("/files/") :]
        candidate = Path(raw)
        resolved = candidate.expanduser().resolve() if candidate.is_absolute() else (self.root / candidate).resolve()
        self.relative(resolved)
        return resolved

    def public_url(self, path: str | Path) -> str:
        return f"/files/{self.relative_posix(path)}"

    def ensure_studio_dirs(self) -> None:
        """Create only directories owned by the Studio process."""

        for directory in (
            self.state,
            self.assets,
            self.storyboard,
            self.audio,
            self.video,
            self.exports,
            self.uploads,
            self.cache,
            self.work,
            self.playground_images,
            self.playground_videos,
        ):
            directory.mkdir(parents=True, exist_ok=True)


OUTPUT = OutputLayout.from_root(DEFAULT_OUTPUT_ROOT)


__all__ = [
    "DEFAULT_OUTPUT_ROOT",
    "OUTPUT",
    "OutputLayout",
    "REPO_ROOT",
    "safe_segment",
]
