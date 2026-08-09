"""Progressive HLS previews for the web app.

Browsers cannot play MatStat's .mkv sources, so the web app transcodes them
to HLS. Instead of producing one full-file MP4 (which forced the UI to wait
for the *entire* transcode before playing anything), ffmpeg emits a live
playlist plus ~4-second segments. Playback can begin as soon as the first
segment exists while ffmpeg keeps encoding the rest in the background.

Layout under tmp/preview/hls/{source.name}/:

    prog.m3u8                 live playlist (stub until segments appear)
    segments/seg_%05d.ts      4-second MPEG-TS segments

When a source uses h264 + AAC the stream is copied with ``-c copy`` (fast);
anything else is re-encoded to a compact 720p libx264 + AAC stream. An
in-flight map deduplicates concurrent transcode requests for the same source.
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import Callable

from scripts.helpers import GetVideoCodecs, tmp_dir
from scripts.web.ffmpeg import FfmpegResult, run_ffmpeg

# Preview HLS playlists/segments are cached under tmp/preview/ (tmp/* is gitignored).
PREVIEW_DIR: Path = tmp_dir / "preview"

# A bare manifest with no segments. ffmpeg overwrites it as encoding proceeds;
# hls.js treats a manifest without #EXT-X-ENDLIST as live and polls for updates.
_STUB_PLAYLIST: str = "#EXTM3U\n#EXT-X-VERSION:3\n"

# Appended by transcode_hls once ffmpeg has finished writing all segments.
_ENDLIST_MARKER: str = "#EXT-X-ENDLIST"

# source Path -> job_id of the transcode currently producing its preview.
_IN_FLIGHT: dict[Path, str] = {}
_LOCK: threading.Lock = threading.Lock()


def hls_dir_path(source: Path) -> Path:
    """The directory holding a source video's HLS output."""
    return PREVIEW_DIR / "hls" / source.name


def playlist_path(source: Path) -> Path:
    """The on-disk location of a source video's HLS playlist."""
    return hls_dir_path(source) / "prog.m3u8"


def segments_dir(source: Path) -> Path:
    """The directory holding a source video's HLS segment files."""
    return hls_dir_path(source) / "segments"


def hls_is_ready(source: Path) -> bool:
    """True if a playable HLS preview exists for ``source``.

    A playlist is playable once it lists at least one segment (mid-transcode)
    or is marked finished with #EXT-X-ENDLIST. A finished playlist is
    considered stale (and therefore not ready) if it is older than the source,
    e.g. after a video with the same name is re-tagged.
    """
    playlist: Path = playlist_path(source)
    if not playlist.is_file() or not source.is_file():
        return False
    try:
        content: str = playlist.read_text()
    except OSError:
        return False
    if "#EXTINF" not in content and _ENDLIST_MARKER not in content:
        return False
    if _ENDLIST_MARKER in content:
        try:
            if playlist.stat().st_mtime < source.stat().st_mtime:
                return False
        except OSError:
            return False
    return True


def build_hls_cmd(
    source: Path,
    playlist: Path,
    segments: Path,
    use_stream_copy: bool = False,
) -> list[str]:
    """Build an ffmpeg command that emits an HLS stream for ``source``.

    Uses ``-hls_list_size 0`` so the playlist keeps every segment (no rolling
    window) and omits #EXT-X-ENDLIST until the process ends, which lets a
    player start while encoding is still running. With ``use_stream_copy`` the
    streams are copied verbatim; otherwise the video is re-encoded to a
    compact 720p libx264 stream that any browser can play.

    Args:
        source: The source video (e.g. an .mkv with embedded chapters).
        playlist: The destination .m3u8 playlist file.
        segments: The directory where segment files are written.
        use_stream_copy: True to copy streams (h264+AAC sources only).

    Returns:
        The ffmpeg command as a list of arguments.
    """
    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-i", str(source),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-f", "hls",
        "-hls_time", "4",
        "-hls_list_size", "0",
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", str(segments / "seg_%05d.ts"),
    ]
    if use_stream_copy:
        cmd += ["-c", "copy"]
    else:
        # The backslash escapes the comma inside ffmpeg's filter expression.
        cmd += [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-vf", "scale=-2:min(720\\,ih)",
            "-c:a", "aac",
        ]
    cmd.append(str(playlist))
    return cmd


def _supports_stream_copy(source: Path) -> bool:
    """True if a source's video+audio can be remuxed into TS verbatim."""
    codecs: dict[str, str] = GetVideoCodecs(source)
    return codecs.get("video") == "h264" and codecs.get("audio") == "aac"


def prepare_hls(source: Path) -> Path:
    """Create a fresh (empty) HLS workspace for ``source`` and return its playlist.

    Wipes any previous segments and resets the playlist to a bare stub so a
    transcode can restart cleanly. Only called by the app when starting a new
    transcode for a source that is not currently in flight.
    """
    hls_dir: Path = hls_dir_path(source)
    hls_dir.mkdir(parents=True, exist_ok=True)
    segments: Path = segments_dir(source)
    shutil.rmtree(segments, ignore_errors=True)
    segments.mkdir(parents=True, exist_ok=True)
    playlist: Path = playlist_path(source)
    playlist.write_text(_STUB_PLAYLIST)
    return playlist


def mark_in_flight(source: Path, job_id: str) -> None:
    """Record that ``job_id`` is transcoding a preview for ``source``."""
    with _LOCK:
        _IN_FLIGHT[source] = job_id


def clear_in_flight(source: Path, job_id: str) -> None:
    """Remove the in-flight entry for ``source`` if it belongs to ``job_id``."""
    with _LOCK:
        if _IN_FLIGHT.get(source) == job_id:
            del _IN_FLIGHT[source]


def in_flight_job(source: Path) -> str | None:
    """Return the job id transcoding ``source``, or None if not in flight."""
    with _LOCK:
        return _IN_FLIGHT.get(source)


def transcode_hls(
    source: Path,
    job_id: str,
    on_progress: Callable[[int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Run the HLS transcode for ``source`` (call from a job thread).

    Stream-copies when possible, re-encodes otherwise, appends the
    #EXT-X-ENDLIST marker on success, and cleans up the HLS workspace on
    failure. Always releases the in-flight entry (via ``job_id``) in a
    finally block.

    Args:
        source: Path to the source video.
        job_id: The JobManager id for this transcode (used to release in-flight).
        on_progress: Optional (elapsed_seconds, description) progress callback.
        cancel_event: Optional event; when set, the transcode is aborted.

    Raises:
        RuntimeError: If ffmpeg fails or produces no playlist.
    """
    playlist: Path = playlist_path(source)
    segments: Path = segments_dir(source)
    segments.mkdir(parents=True, exist_ok=True)

    use_stream_copy: bool = _supports_stream_copy(source)
    cmd: list[str] = build_hls_cmd(source, playlist, segments, use_stream_copy)
    try:
        logging.info(
            f"Transcoding preview for '{source.name}' "
            f"({'stream copy' if use_stream_copy else 're-encode'})"
        )
        result: FfmpegResult = run_ffmpeg(
            cmd,
            description=f"Preparing preview: {source.name}",
            on_progress=on_progress,
            cancel_event=cancel_event,
        )
        if not result.success or not playlist.is_file():
            raise RuntimeError(f"Preview transcode failed: {result.message}")
        _append_endlist(playlist)
        logging.info(f"Preview ready: {playlist}")
    except Exception:
        shutil.rmtree(hls_dir_path(source), ignore_errors=True)
        raise
    finally:
        clear_in_flight(source, job_id)


def _append_endlist(playlist: Path) -> None:
    """Mark the playlist as finished so players treat it as VOD."""
    try:
        content: str = playlist.read_text()
    except OSError:
        content = ""
    if _ENDLIST_MARKER not in content:
        with open(playlist, "a") as f:
            f.write(f"\n{_ENDLIST_MARKER}\n")
