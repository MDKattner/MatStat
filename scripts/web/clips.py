"""Combine Clips jobs for the web app — build highlight reels with ffmpeg.

A wrestler's tagged sequences are
filtered by starting tie-up, move used, or move defended; each match is
extracted from its source video in vids/taged/; and the segments are
concatenated into a single highlight clip in vids/clips/. Runs inside a
JobManager thread; progress is reported via the JobContext.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

from scripts.helpers import (
    COL_ATTACKING,
    COL_END_TIME,
    COL_OPPONENT_MOVES,
    COL_START_TIME,
    COL_TEAM_MOVES,
    COL_TIE_UP,
    GetVideoCodecs,
    MakeFormattedDataFrame,
    clips_dir,
    csv_dir,
    taged_dir,
    tmp_dir,
)
from scripts.web.ffmpeg import run_ffmpeg
from scripts.web.jobs import JobContext

FilterType = Literal["Starting Tie", "Move Used", "Move Defended"]

_STREAM_COPY_DEFAULT: bool = True

_RE_ENCODE_ARGS: list[str] = [
    "-vf", "scale=1280:720,setsar=1",
    "-c:v", "libx264",
    "-preset", "medium",
    "-crf", "23",
    "-c:a", "aac",
    "-b:a", "128k",
]


class FindRequest(BaseModel):
    """Payload for POST /api/clips/find — list matching sequences."""

    wrestler: str
    filter_type: FilterType
    filter_item: str


class CombineRequest(FindRequest):
    """Payload for POST /api/combine-clips — build one highlight reel."""

    use_stream_copy: bool = _STREAM_COPY_DEFAULT


class BatchRequest(BaseModel):
    """Payload for POST /api/combine-clips/batch — build reels per wrestler."""

    wrestlers: list[str]
    filter_type: FilterType
    filter_item: str
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


def filter_dataframe(
    df: pd.DataFrame,
    filter_type: FilterType,
    filter_item: str,
) -> pd.DataFrame:
    """Filter a wrestler's sequence DataFrame by a tie, move used, or defended move.

    Args:
        df: The wrestler DataFrame loaded via MakeFormattedDataFrame.
        filter_type: One of "Starting Tie", "Move Used", "Move Defended".
        filter_item: The value to match against the appropriate column.

    Returns:
        The filtered DataFrame (possibly empty).
    """
    if filter_type == "Starting Tie":
        # Dual-wrestler sequences store the pair "yours:theirs"; filter on the
        # tagged wrestler's own tie (the element before the colon).
        return df[df[COL_TIE_UP].str.split(":").str[0] == filter_item]
    if filter_type == "Move Used":
        return df[df[COL_TEAM_MOVES].apply(lambda moves: filter_item in moves)]
    return df[df[COL_OPPONENT_MOVES].apply(lambda moves: filter_item in moves)]


def load_filtered_dataframe(
    wrestler: str,
    filter_type: FilterType,
    filter_item: str,
) -> pd.DataFrame:
    """Load a wrestler's CSV and filter its sequences for clip generation.

    Args:
        wrestler: The wrestler name (also the CSV stem).
        filter_type: One of "Starting Tie", "Move Used", "Move Defended".
        filter_item: The value to filter by.

    Returns:
        The filtered DataFrame (possibly empty).

    Raises:
        FileNotFoundError: If the wrestler's compiled CSV does not exist.
        ValueError: If the CSV exists but cannot be parsed.
    """
    csv_path: Path = csv_dir / f"{wrestler}.csv"
    if not csv_path.is_file():
        raise FileNotFoundError(
            f"No compiled data for {wrestler}. Run Compile Stats first."
        )
    try:
        df: pd.DataFrame = MakeFormattedDataFrame(csv_path)
    except Exception as e:
        raise ValueError(f"Could not load data for {wrestler}: {e}")
    return filter_dataframe(df, filter_type, filter_item)


def matches_to_json(filtered: pd.DataFrame) -> list[dict[str, Any]]:
    """Serialize matching sequences for the find endpoint / sequence list.

    Args:
        filtered: The filtered sequence DataFrame.

    Returns:
        A list of match dicts with video, times, attacking, tie_up, and moves.
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
        })
    return matches


def _build_reel(
    ctx: JobContext,
    filtered: pd.DataFrame,
    wrestler: str,
    filter_type: FilterType,
    filter_item: str,
    use_stream_copy: bool,
    work_name: str,
) -> tuple[str, int, list[str]] | None:
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
        A (output_name, segment_count, errors) tuple, or None if cancelled.

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
    output_path: Path = (
        clips_dir
        / f"{_sanitize(wrestler)}_{_sanitize(filter_type)}_{_sanitize(filter_item)}.mkv"
    )
    clips_dir.mkdir(parents=True, exist_ok=True)

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
    return (output_path.name, len(segment_files), errors)


def run_combine_clips_job(
    ctx: JobContext,
    wrestler: str,
    filter_type: FilterType,
    filter_item: str,
    use_stream_copy: bool = _STREAM_COPY_DEFAULT,
) -> dict[str, Any]:
    """Build a single highlight reel for one wrestler × filter combination.

    Args:
        ctx: Job context for progress reporting and cancellation.
        wrestler: The wrestler name.
        filter_type: One of "Starting Tie", "Move Used", "Move Defended".
        filter_item: The value to filter by.
        use_stream_copy: Whether to prefer stream-copy extraction.

    Returns:
        A dict describing the result: ``{"output": name, "segments": n, "errors": [...]}``.

    Raises:
        FileNotFoundError: If the wrestler's compiled CSV does not exist.
        ValueError: If the CSV is unreadable or no sequences match.
        RuntimeError: If no clips were extracted or concatenation fails.
    """
    filtered: pd.DataFrame = load_filtered_dataframe(wrestler, filter_type, filter_item)
    if filtered.empty:
        raise ValueError(
            f"No sequences found for '{wrestler}' / {filter_type} = '{filter_item}'"
        )

    reel: tuple[str, int, list[str]] | None = _build_reel(
        ctx, filtered, wrestler, filter_type, filter_item, use_stream_copy, ctx.job_id
    )
    if reel is None:
        return {"output": "", "segments": 0, "errors": ["Cancelled"]}

    output_name, segments, errors = reel
    ctx.report(100, f"Created: {output_name}")
    logging.info(f"Created highlight reel for {wrestler}: {output_name}")
    return {"output": output_name, "segments": segments, "errors": errors}


def run_batch_clips_job(
    ctx: JobContext,
    wrestlers: list[str],
    filter_type: FilterType,
    filter_item: str,
    use_stream_copy: bool = _STREAM_COPY_DEFAULT,
) -> dict[str, Any]:
    """Build a highlight reel for every selected wrestler × the given filter.

    Args:
        ctx: Job context for progress reporting and cancellation.
        wrestlers: The wrestler names to process.
        filter_type: One of "Starting Tie", "Move Used", "Move Defended".
        filter_item: The value to filter by.
        use_stream_copy: Whether to prefer stream-copy extraction.

    Returns:
        A dict with per-wrestler results and a summary:
        ``{"results": [...], "successes": n, "failures": n, "summary": str}``.
    """
    results: list[dict[str, str | bool]] = []
    successes: int = 0
    failures: int = 0
    total: int = len(wrestlers)

    for i, wrestler in enumerate(wrestlers):
        if ctx.cancelled:
            break
        ctx.report(100.0 * i / total, f"[{i + 1}/{total}] {wrestler} — {filter_item}")

        try:
            filtered: pd.DataFrame = load_filtered_dataframe(
                wrestler, filter_type, filter_item
            )
            if filtered.empty:
                raise ValueError(
                    f"No sequences found for '{wrestler}' / {filter_type} = '{filter_item}'"
                )
            reel: tuple[str, int, list[str]] | None = _build_reel(
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
            output_name, _, errors = reel
            msg: str = f"Created: {output_name}"
            if errors:
                msg += " (some clips had errors)"
            results.append({"wrestler": wrestler, "success": True, "message": msg})
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
