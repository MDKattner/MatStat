from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from scripts.helpers import tmp_dir
from scripts.web.ffmpeg import FfmpegResult, run_ffmpeg

# Preview MP4s are cached under tmp/preview/ (tmp/* is gitignored).
PREVIEW_DIR: Path = tmp_dir / "preview"


def preview_cache_path(source: Path) -> Path:
    """The on-disk location of the cached preview MP4 for a source video."""
    return PREVIEW_DIR / f"{source.name}.mp4"


def preview_is_fresh(source: Path, cache: Path) -> bool:
    """True if a preview cache exists and is newer than its source video."""
    if not source.exists() or not cache.exists():
        return False
    return cache.stat().st_mtime >= source.stat().st_mtime


def build_transcode_cmd(source: Path, output: Path) -> list[str]:
    """Build an ffmpeg command that produces a browser-playable preview MP4.

    Uses libx264 + yuv420p (required for browser playback) and AAC audio,
    with ``+faststart`` so playback begins before the file is fully received.

    Args:
        source: The source video (e.g. an .mkv with embedded chapters).
        output: The destination .mp4 preview file.

    Returns:
        The ffmpeg command as a list of arguments.
    """
    return [
        "ffmpeg",
        "-y",
        "-i", str(source),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(output),
    ]


def ensure_preview(
    source: Path,
    on_progress: Callable[[int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Return a browser-playable preview MP4 for ``source``, transcoding if needed.

    If a fresh cache exists it is returned immediately. Otherwise the source is
    transcoded with ffmpeg (progress reported via ``on_progress``) and cached.

    Args:
        source: Path to the source video.
        on_progress: Optional (elapsed_seconds, description) progress callback.
        cancel_event: Optional event; when set, the transcode is aborted.

    Returns:
        The path to the ready preview MP4.

    Raises:
        RuntimeError: If the transcode fails or the cache cannot be produced.
    """
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    cache: Path = preview_cache_path(source)
    if preview_is_fresh(source, cache):
        logging.debug(f"Preview cache hit: {cache}")
        return cache

    logging.info(f"Transcoding preview for '{source.name}'")
    result: FfmpegResult = run_ffmpeg(
        build_transcode_cmd(source, cache),
        description=f"Preparing preview: {source.name}",
        on_progress=on_progress,
        cancel_event=cancel_event,
    )

    if not result.success or not cache.exists():
        cache.unlink(missing_ok=True)
        raise RuntimeError(f"Preview transcode failed: {result.message}")

    logging.info(f"Preview ready: {cache}")
    return cache
