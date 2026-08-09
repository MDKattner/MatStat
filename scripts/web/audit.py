"""Auditing job for the web app — review tagged videos and re-tag them.

Every tagged video is probed for its wrestler name and embedded sequences so
they can be reviewed. Re-tagging re-embeds new chapter metadata directly into
the tagged file (the original was deleted when it was first tagged), then
recompiles stats. Runs inside a JobManager thread; progress is reported via
the JobContext.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from scripts.helpers import (
    BuildTagTitle,
    ChapterSequence,
    GetVidDuration,
    MakeNameAndCSV,
    ParseTaggedName,
    taged_dir,
    tmp_dir,
)
from scripts.web import configs, stats, tag
from scripts.web.ffmpeg import FfmpegResult, run_ffmpeg
from scripts.web.jobs import JobContext
from scripts.web.tag import TagSequence


class RetagRequest(BaseModel):
    """Payload for POST /api/audit/{file}/retag."""

    wrestler: str
    opponent: str = ""
    match_result: str = ""
    sequences: list[TagSequence] = Field(default_factory=list)


def _sequence_to_dict(chap: ChapterSequence) -> dict[str, Any]:
    """Convert a ChapterSequence into a TagSequence-shaped JSON dict.

    Dual-wrestler tie-ups are stored as "yours:theirs" pairs; they are split
    back into per-wrestler tie fields for re-editing.

    Args:
        chap: The sequence parsed from a tagged video.

    Returns:
        The sequence fields as a JSON-serializable dict.
    """
    tie_pair: list[str] = chap.tie_up.split(":")
    opp_tie: str = ":".join(tie_pair[1:]) if len(tie_pair) > 1 else ""
    return {
        "start_time": chap.start_time,
        "end_time": chap.end_time,
        "attack_defend": chap.attack_defend,
        "tie_up": tie_pair[0],
        "opp_tie": opp_tie,
        "team_moves": chap.team_moves,
        "op_moves": chap.op_moves,
        "team_scores": chap.team_scores,
        "op_scores": chap.op_scores,
    }


def list_tagged_videos() -> list[dict[str, Any]]:
    """List every tagged video with its embedded wrestler and sequences.

    Returns:
        A list of ``{"name", "wrestler", "opponent", "result", "sequences"}``
        dicts, sorted by video name. Videos that fail to probe are listed with
        no sequences.
    """
    videos: list[dict[str, Any]] = []
    if not taged_dir.exists():
        return videos
    for video_path in sorted(taged_dir.iterdir()):
        if not video_path.is_file() or video_path.name.startswith("."):
            continue
        name: str
        csv_data: str
        name, csv_data = MakeNameAndCSV(video_path)
        wrestler: str
        opponent: str | None
        result: str
        wrestler, opponent, result = ParseTaggedName(name)
        sequences: list[dict[str, Any]] = []
        for line in csv_data.splitlines():
            chap: ChapterSequence = ChapterSequence.FromCSVRow(line)
            if chap.start_time >= chap.end_time:
                logging.warning(
                    f"Skipping malformed chapter in {video_path.name}: {line}"
                )
                continue
            sequences.append(_sequence_to_dict(chap))
        videos.append({
            "name": video_path.name,
            "wrestler": wrestler,
            "opponent": opponent or "",
            "result": result,
            "sequences": sequences,
        })
    return videos


def run_retag_job(
    ctx: JobContext,
    video: str,
    wrestler: str,
    sequences: list[TagSequence],
    opponent: str = "",
    match_result: str = "",
) -> dict[str, Any]:
    """Re-embed chapter metadata into an already-tagged video, then recompile.

    The tagged file is re-muxed with new metadata (stream copy, so no quality
    loss); the old chapters are replaced in place. After the re-mux succeeds,
    compile stats runs over every tagged video so the CSVs reflect the edits.

    Args:
        ctx: Job context for progress reporting.
        video: The tagged video file name in vids/taged/.
        wrestler: The wrestler name to embed as the title tag.
        sequences: The real sequences, in chronological order.
        opponent: The opponent's name for dual-wrestler mode, else "".
        match_result: The tagged wrestler's result ("", "W", or "L").

    Returns:
        A dict describing the result, e.g.
        ``{"output": "match.mkv", "compile": {...}}`` (``compile`` is None when
        no roster is configured to recompile against).

    Raises:
        ValueError: If the payload is invalid (missing name/sequences, bad times, overlaps).
        FileNotFoundError: If the tagged video does not exist.
        RuntimeError: If the video duration can't be read or ffmpeg fails.
    """
    wrestler_clean: str = wrestler.strip()
    if not wrestler_clean:
        raise ValueError("Wrestler name is required")

    if not sequences:
        raise ValueError("At least one sequence is required")

    input_path: Path = taged_dir / video
    if not input_path.is_file():
        raise FileNotFoundError(f"Video not found: {video}")

    tag.validate_sequences(sequences)

    duration: int = GetVidDuration(input_path)
    if duration <= 0:
        raise RuntimeError(f"Could not determine duration for '{video}'")

    chapters: list[ChapterSequence] = tag.build_chapters(sequences, duration)
    title: str = BuildTagTitle(wrestler_clean, opponent, match_result)

    ctx.report(10, "Writing metadata...")
    metadata_path: Path = tmp_dir / f"retag-metadata-{ctx.job_id}.txt"
    try:
        metadata_path.write_text(tag.build_tag_metadata(chapters, title))
    except OSError as e:
        raise RuntimeError(f"Could not write metadata file: {e}")

    output_path: Path = taged_dir / Path(video).with_suffix(".mkv").name
    tmp_output: Path = tmp_dir / f"retag-{ctx.job_id}.mkv"

    ctx.report(50, "Re-tagging video...")

    def _on_progress(elapsed: int, desc: str) -> None:
        pct: float = min(99.0, 50.0 + 49.0 * elapsed / max(1, duration))
        ctx.report(pct, desc)

    result: FfmpegResult = run_ffmpeg(
        tag.build_tag_cmd(input_path, metadata_path, tmp_output),
        description=f"Re-tagging {video}",
        on_progress=_on_progress,
        cancel_event=None,
    )

    metadata_path.unlink(missing_ok=True)

    if not result.success or not tmp_output.exists():
        tmp_output.unlink(missing_ok=True)
        raise RuntimeError(f"Re-tagging failed: {result.message}")

    tmp_output.replace(output_path)

    ctx.report(90, "Recompiling stats...")
    wrestler_names: list[str] = []
    try:
        wrestler_names = configs.load_wrestler_names()
    except Exception as e:
        logging.warning(f"Could not load wrestler roster for recompile: {e}")
    compile_result: dict[str, Any] | None = (
        stats.run_compile_stats_job(ctx, wrestler_names) if wrestler_names else None
    )

    ctx.report(100, f"Re-tagged {video}")
    logging.info(f"Re-tagged '{video}' for {wrestler_clean} -> {output_path}")
    return {"output": output_path.name, "compile": compile_result}
