"""Combine Clips jobs for the web app — build highlight reels with ffmpeg.

A wrestler's tagged sequences are
filtered by starting tie-up, move used, or move defended; each match is
extracted from its source video in vids/taged/; and the segments are
concatenated into a single highlight clip in vids/clips/. Runs inside a
JobManager thread; progress is reported via the JobContext.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from scripts import storage
from scripts.helpers import (
    COL_ADJUSTED_NET_POINTS,
    COL_ATTACKING,
    COL_END_TIME,
    COL_MATCH_RESULT,
    COL_NET_POINTS,
    COL_START_TIME,
    COL_TEAM_MOVES,
    COL_TIE_UP,
    GetVideoCodecs,
    clips_dir,
    taged_dir,
    tmp_dir,
)
from scripts.web.ffmpeg import run_ffmpeg
from scripts.web.jobs import JobContext
from scripts.web.query import QuerySpec, apply_query

_STREAM_COPY_DEFAULT: bool = True

_REEL_RE: re.Pattern[str] = re.compile(
    r"^(?P<base>.+)_\d{8}-\d{6}(?:-\d+)?\.mkv$"
)

_RE_ENCODE_ARGS: list[str] = [
    "-vf", "scale=1280:720,setsar=1",
    "-c:v", "libx264",
    "-preset", "medium",
    "-crf", "23",
    "-c:a", "aac",
    "-b:a", "128k",
]


class FindRequest(QuerySpec):
    """Payload for POST /api/clips/find — list matching sequences."""


class CombineRequest(QuerySpec):
    """Payload for POST /api/combine-clips — build one highlight reel."""

    use_stream_copy: bool = _STREAM_COPY_DEFAULT


class BatchRequest(QuerySpec):
    """Payload for POST /api/combine-clips/batch — build reels per wrestler."""

    use_stream_copy: bool = _STREAM_COPY_DEFAULT


def _sanitize(name: str) -> str:
    """Replace characters that are awkward in file names."""
    return name.replace(" ", "_").replace("/", "_")


def build_extract_cmd(
    input_path: Path,
    start_time: int,
    end_time: int,
    segment_path: Path,
    use_stream_copy: bool = True,
    codecs: dict[str, str] | None = None,
) -> list[str]:
    """Build the ffmpeg command that extracts one sequence segment.

    Args:
        input_path: The source video in vids/taged/.
        start_time: Segment start time in seconds.
        end_time: Segment end time in seconds.
        segment_path: The temporary output segment (.ts).
        use_stream_copy: When True, copy streams when codecs allow it.
        codecs: {stream: codec} map from GetVideoCodecs; only read when
            use_stream_copy is True.

    Returns:
        The ffmpeg command as a list of arguments.
    """
    cmd: list[str] = [
        "ffmpeg",
        "-i", str(input_path),
        "-ss", str(start_time),
        "-to", str(end_time),
    ]

    if use_stream_copy:
        codec_map: dict[str, str] = codecs or {}
        vcodec: str = codec_map.get("video", "")
        acodec: str = codec_map.get("audio", "")
        if vcodec == "h264" and acodec == "aac":
            cmd += ["-c", "copy", "-avoid_negative_ts", "1"]
            logging.debug(f"Using stream copy for {input_path.name}")
        else:
            logging.info(
                f"Codecs ({vcodec}/{acodec}) not compatible with stream copy "
                f"for {input_path.name}; falling back to re-encode"
            )
            cmd += _RE_ENCODE_ARGS
    else:
        cmd += _RE_ENCODE_ARGS

    cmd += ["-y", str(segment_path)]
    return cmd


def build_concat_cmd(list_file: Path, output_path: Path) -> list[str]:
    """Build the ffmpeg command that concatenates extracted segments.

    Args:
        list_file: The concat demuxer list file (mylist.txt).
        output_path: The destination highlight reel in vids/clips/.

    Returns:
        The ffmpeg command as a list of arguments.
    """
    return [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        "-y",
        str(output_path),
    ]


def load_wrestler_frame(wrestler: str) -> pd.DataFrame:
    """Load a wrestler's compiled data from the SQLite store.

    Args:
        wrestler: The wrestler name.

    Returns:
        The formatted sequence DataFrame.

    Raises:
        FileNotFoundError: If no compiled data exists for the wrestler.
        ValueError: If the stored data cannot be read.
    """
    try:
        return storage.load_wrestler_frame(wrestler)
    except FileNotFoundError:
        raise
    except Exception as e:
        raise ValueError(f"Could not load data for {wrestler}: {e}")


def find_with_query(req: QuerySpec, wrestler: str) -> pd.DataFrame:
    """Load a wrestler's compiled data and apply the shared query implementation.

    Args:
        req: The shared filter specification.
        wrestler: The wrestler name.

    Returns:
        The filtered DataFrame (possibly empty).

    Raises:
        FileNotFoundError: If no compiled data exists for the wrestler.
        ValueError: If the stored data cannot be read, or origins are unknown.
    """
    return apply_query(load_wrestler_frame(wrestler), req, wrestler)


def reel_label(req: QuerySpec) -> tuple[str, str]:
    """Return a human-readable (filter type, filter item) label for a reel.

    The label keeps the historical file-name shape for single-filter reels and
    falls back to ``Selection`` / ``reel`` for origin-based or multi-filter
    queries.
    """
    if req.tie_up:
        return ("Starting Tie", req.tie_up)
    if req.team_move:
        return ("Move Used", req.team_move)
    if req.opp_move:
        return ("Move Defended", req.opp_move)
    return ("Selection", "reel")


def matches_to_json(filtered: pd.DataFrame) -> list[dict[str, Any]]:
    """Serialize matching sequences for the find endpoint / sequence list.

    Args:
        filtered: The filtered sequence DataFrame.

    Returns:
        A list of match dicts with video, times, attacking, tie_up, moves,
        net_points, adjusted_net_points, and match_result.
    """
    matches: list[dict[str, Any]] = []
    for index, row in filtered.iterrows():
        moves: Any = row[COL_TEAM_MOVES]
        moves_list: list[str] = moves if isinstance(moves, list) else []
        matches.append({
            "video": str(index).split(":")[0],
            "start_time": int(row[COL_START_TIME]),
            "end_time": int(row[COL_END_TIME]),
            "attacking": bool(row[COL_ATTACKING]),
            "tie_up": str(row[COL_TIE_UP]) or "",
            "moves": moves_list,
            "net_points": int(row[COL_NET_POINTS]),
            "adjusted_net_points": int(row[COL_ADJUSTED_NET_POINTS]),
            "match_result": str(row.get(COL_MATCH_RESULT, "")),
        })
    return matches


def _unique_reel_path(clips_dir: Path, base: str) -> Path:
    """Return a timestamped reel path that does not collide with an existing file."""
    stamp: str = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate: Path = clips_dir / f"{base}_{stamp}.mkv"
    suffix: int = 1
    while candidate.exists():
        candidate = clips_dir / f"{base}_{stamp}-{suffix}.mkv"
        suffix += 1
    return candidate


def _update_latest_pointer(clips_dir: Path, base: str, output: Path) -> str | None:
    """Point ``{base}_latest.mkv`` at ``output``; skip the pointer on failure."""
    latest: Path = clips_dir / f"{base}_latest.mkv"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(output.name)
        return latest.name
    except OSError as e:
        logging.warning(f"Could not create reel latest pointer '{latest}': {e}")
        return None


def list_reels() -> list[dict[str, Any]]:
    """List generated reel versions grouped by their base name.

    Returns:
        A list of groups (sorted by base), each with the ``base`` name, the
        ``latest`` pointer name (or None), and ``versions`` newest first. Every
        version carries its filename, mtime, size, and whether it is the
        current ``_latest`` target.
    """
    if not clips_dir.is_dir():
        return []
    groups: dict[str, list[Path]] = {}
    for path in clips_dir.iterdir():
        match = _REEL_RE.match(path.name)
        if match and path.is_file():
            groups.setdefault(match.group("base"), []).append(path)

    result: list[dict[str, Any]] = []
    for base, files in sorted(groups.items()):
        files.sort(key=lambda p: p.name, reverse=True)
        latest_path: Path = clips_dir / f"{base}_latest.mkv"
        has_latest: bool = latest_path.exists()
        target: str = latest_path.resolve().name if has_latest else ""
        result.append({
            "base": base,
            "latest": latest_path.name if has_latest else None,
            "versions": [
                {
                    "name": path.name,
                    "mtime": path.stat().st_mtime,
                    "size": path.stat().st_size,
                    "current": path.name == target,
                }
                for path in files
            ],
        })
    return result


def _build_reel(
    ctx: JobContext,
    filtered: pd.DataFrame,
    wrestler: str,
    filter_type: str,
    filter_item: str,
    use_stream_copy: bool,
    work_name: str,
) -> tuple[str, int, list[str], str | None] | None:
    """Extract each matching sequence and concatenate it into a highlight reel.

    Args:
        ctx: Job context for progress reporting and cancellation.
        filtered: The filtered sequence DataFrame.
        wrestler: The wrestler name, used in the output file name.
        filter_type: The filter type, used in the output file name.
        filter_item: The filter item, used in the output file name.
        use_stream_copy: Whether to prefer stream-copy extraction.
        work_name: Unique suffix for the temporary clip directory.

    Returns:
        An (output_name, segment_count, errors, latest_alias) tuple, or None
        if cancelled. ``latest_alias`` is None when the symlink cannot be made.

    Raises:
        RuntimeError: If no segments were extracted or concatenation fails.
    """
    clip_dir: Path = tmp_dir / f"clip_gen_{work_name}"
    clip_dir.mkdir(parents=True, exist_ok=True)

    segment_files: list[Path] = []
    has_errors: bool = False
    total: int = len(filtered)

    for i, (index, row) in enumerate(filtered.iterrows()):
        if ctx.cancelled:
            shutil.rmtree(clip_dir, ignore_errors=True)
            return None

        origin_video_name: str = str(index).split(":")[0]
        input_path: Path = taged_dir / origin_video_name
        start_time: int = int(row[COL_START_TIME])
        end_time: int = int(row[COL_END_TIME])

        ctx.report(
            100.0 * (i + 1) / total,
            f"Extracting clip {i + 1}/{total} from {origin_video_name}",
        )

        if not input_path.is_file():
            logging.warning(f"Source video not found: {input_path}")
            has_errors = True
            continue

        segment_path: Path = clip_dir / f"segment_{i}.ts"
        codecs: dict[str, str] = GetVideoCodecs(input_path)
        cmd: list[str] = build_extract_cmd(
            input_path, start_time, end_time, segment_path, use_stream_copy, codecs
        )

        result = run_ffmpeg(cmd, description=f"Extracting {origin_video_name}", cancel_event=ctx.cancel_event)
        if not result.success or not segment_path.is_file():
            has_errors = True
            continue
        segment_files.append(segment_path)

    if not segment_files:
        shutil.rmtree(clip_dir, ignore_errors=True)
        raise RuntimeError("No clips were successfully extracted.")

    ctx.report(100, "Concatenating clips...")
    base: str = (
        f"{_sanitize(wrestler)}_{_sanitize(filter_type)}_{_sanitize(filter_item)}"
    )
    clips_dir.mkdir(parents=True, exist_ok=True)
    output_path: Path = _unique_reel_path(clips_dir, base)

    list_file: Path = clip_dir / "mylist.txt"
    list_file.write_text(
        "".join(f"file '{seg.resolve()}'\n" for seg in segment_files)
    )

    result = run_ffmpeg(build_concat_cmd(list_file, output_path), description="Concatenating clips", cancel_event=ctx.cancel_event)
    shutil.rmtree(clip_dir, ignore_errors=True)

    if not result.success or not output_path.is_file():
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"Concatenation failed: {result.message}")

    errors: list[str] = []
    if has_errors:
        errors.append("Some clips had errors and were skipped.")
    latest: str | None = _update_latest_pointer(clips_dir, base, output_path)
    return (output_path.name, len(segment_files), errors, latest)


def run_combine_clips_job(
    ctx: JobContext,
    wrestler: str,
    spec: QuerySpec,
    use_stream_copy: bool = _STREAM_COPY_DEFAULT,
) -> dict[str, Any]:
    """Build a single highlight reel for one wrestler × query specification.

    Args:
        ctx: Job context for progress reporting and cancellation.
        wrestler: The wrestler name.
        spec: The shared filter specification (or exact ``origins``).
        use_stream_copy: Whether to prefer stream-copy extraction.

    Returns:
        A dict describing the result: ``{"output": name, "segments": n, "errors": [...]}``.

    Raises:
        FileNotFoundError: If the wrestler has no compiled rows.
        ValueError: If the compiled data is unreadable, origins are unknown, or
            no sequences match.
        RuntimeError: If no clips were extracted or concatenation fails.
    """
    filtered: pd.DataFrame = find_with_query(spec, wrestler)
    filter_type, filter_item = reel_label(spec)
    if filtered.empty:
        raise ValueError(
            f"No sequences found for '{wrestler}' / {filter_type} = '{filter_item}'"
        )

    reel: tuple[str, int, list[str], str | None] | None = _build_reel(
        ctx, filtered, wrestler, filter_type, filter_item, use_stream_copy, ctx.job_id
    )
    if reel is None:
        return {
            "output": "", "segments": 0, "errors": ["Cancelled"], "latest": None,
        }

    output_name, segments, errors, latest = reel
    ctx.report(100, f"Created: {output_name}")
    logging.info(f"Created highlight reel for {wrestler}: {output_name}")
    return {
        "output": output_name,
        "segments": segments,
        "errors": errors,
        "latest": latest,
    }


def run_batch_clips_job(
    ctx: JobContext,
    wrestlers: list[str],
    spec: QuerySpec,
    use_stream_copy: bool = _STREAM_COPY_DEFAULT,
) -> dict[str, Any]:
    """Build a highlight reel for every selected wrestler × the query spec.

    Args:
        ctx: Job context for progress reporting and cancellation.
        wrestlers: The wrestler names to process.
        spec: The shared filter specification.
        use_stream_copy: Whether to prefer stream-copy extraction.

    Returns:
        A dict with per-wrestler results and a summary:
        ``{"results": [...], "successes": n, "failures": n, "summary": str}``.
    """
    results: list[dict[str, str | bool | None]] = []
    successes: int = 0
    failures: int = 0
    total: int = len(wrestlers)
    filter_type, filter_item = reel_label(spec)

    for i, wrestler in enumerate(wrestlers):
        if ctx.cancelled:
            break
        ctx.report(100.0 * i / total, f"[{i + 1}/{total}] {wrestler} — {filter_item}")

        try:
            filtered: pd.DataFrame = find_with_query(spec, wrestler)
            if filtered.empty:
                raise ValueError(
                    f"No sequences found for '{wrestler}' / {filter_type} = '{filter_item}'"
                )
            reel: tuple[str, int, list[str], str | None] | None = _build_reel(
                ctx,
                filtered,
                wrestler,
                filter_type,
                filter_item,
                use_stream_copy,
                f"{ctx.job_id}_{i}",
            )
            if reel is None:
                break
            output_name, _, errors, latest = reel
            msg: str = f"Created: {output_name}"
            if errors:
                msg += " (some clips had errors)"
            results.append({
                "wrestler": wrestler,
                "success": True,
                "message": msg,
                "output": output_name,
                "latest": latest,
            })
            successes += 1
        except Exception as e:
            logging.warning(f"Batch clip failed for {wrestler}: {e}")
            results.append({"wrestler": wrestler, "success": False, "message": str(e)})
            failures += 1

    summary: str = f"Batch complete. {successes} succeeded, {failures} failed."
    ctx.report(100, summary)
    logging.info(summary)
    return {
        "results": results,
        "successes": successes,
        "failures": failures,
        "summary": summary,
    }
