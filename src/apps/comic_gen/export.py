import os
from typing import Dict, Any, List
from .models import Script
from ...output_contract import OUTPUT
from ...utils import get_logger

logger = get_logger(__name__)

class ExportManager:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.output_dir = self.config.get('output_dir', str(OUTPUT.exports))
        os.makedirs(self.output_dir, exist_ok=True)

    def render_project(self, script: Script, options: Dict[str, Any]) -> str:
        raise NotImplementedError(
            "Legacy ExportManager has no real renderer and is disabled. "
            "Use ComicGenPipeline.merge_videos so selected accepted takes are verified."
        )

    def _stitch_video(self, frames: List[Any], output_path: str):
        # TODO: Implement FFmpeg stitching
        pass

    def _mix_audio(self, audio_tracks: List[Any], output_path: str):
        # TODO: Implement FFmpeg audio mixing
        pass

    def _add_subtitles(self, video_path: str, subtitles: List[Any]):
        # TODO: Implement FFmpeg subtitle burning
        pass
