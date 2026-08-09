"""Tagging job for the web app — embed chapter metadata into an untagged video.

Real sequences are padded with empty chapters, metadata is written to tmp/,
ffmpeg embeds it with ``-codec copy``, and the tagged copy lands in vids/taged/.
The original untagged video is deleted once the tagged copy exists (it is never
hidden). Runs inside a JobManager thread; progress is reported via the
JobContext.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from scripts.helpers import (
    BuildTagTitle,
    BuildTieEntry,
    ChapterSequence,
    GetVidDuration,
    taged_dir,
    tmp_dir,
    untaged_dir,
)
from scripts.web.ffmpeg import FfmpegResult, run_ffmpeg
from scripts.web.jobs import JobContext


class TagSequence(BaseModel):
    """One real (non-filler) sequence to embed as an ffmpeg chapter."""

    start_time: int
    end_time: int
    attack_defend: bool = True
    tie_up: str = ""
    opp_tie: str = ""
    team_moves: list[str] = Field(default_factory=list)
    op_moves: list[str] = Field(default_factory=list)
    team_scores: list[str] = Field(default_factory=list)
    op_scores: list[str] = Field(default_factory=list)


class TagRequest(BaseModel):
    """Payload for POST /api/tag."""

    video: str
    wrestler: str
    opponent: str = ""
    match_result: str = ""
    sequences: list[TagSequence] = Field(default_factory=list)


def build_tag_metadata(chapters: list[ChapterSequence], title: str) -> str:
    """Build the ffmpeg metadata file content for a list of chapters.

    Args:
        chapters: The chapter list (filler and real), in timeline order.
        title: The wrestler name to store in the format ``title`` tag.

    Returns:
        The metadata file content (``;FFMETADATA1`` header + chapters).
    """
    content: str = f";FFMETADATA1\ntitle={title.strip()}\n\n"
    for chap in chapters:
        content += chap.ToMetadata()
    return content


def build_tag_cmd(input_path: Path, metadata_path: Path, output_path: Path) -> list[str]:
    """Build the ffmpeg command that embeds metadata with stream copy.

    Args:
        input_path: The untagged source video.
        metadata_path: The metadata file written by build_tag_metadata.
        output_path: The destination video in vids/taged/.

    Returns:
        The ffmpeg command as a list of arguments.
    """
    return [
        "ffmpeg",
        "-i", str(input_path),
        "-i", str(metadata_path),
        "-map_metadata", "1",
        "-codec", "copy",
        "-y",
        str(output_path),
    ]


def validate_sequences(sequences: list[TagSequence]) -> None:
    """Validate a list of real sequences: non-negative, ordered, non-overlapping.

    Args:
        sequences: The real sequences, in chronological order.

    Raises:
        ValueError: If any sequence has bad times or overlaps the previous one.
    """
    prev_end: int = 0
    for seq in sequences:
        if seq.start_time < 0 or seq.end_time < 0:
            raise ValueError("Sequence times must be non-negative")
        if seq.start_time >= seq.end_time:
            raise ValueError(
                f"Start time must be before end time ({seq.start_time} >= {seq.end_time})"
            )
        if seq.start_time < prev_end:
            raise ValueError(
                f"Sequence at {seq.start_time}s overlaps previous end ({prev_end}s)"
            )
        prev_end = seq.end_time


def build_chapters(sequences: list[TagSequence], duration: int) -> list[ChapterSequence]:
    """Build the padded chapter list from real sequences.

    Args:
        sequences: The real sequences, in chronological order.
        duration: The video duration in seconds (for the trailing filler).

    Returns:
        The chapter list (filler + real), in timeline order.
    """
    chapters: list[ChapterSequence] = []
    last_end: int = 0
    for seq in sequences:
        chapters.append(ChapterSequence.MakeEmptyChap(last_end, seq.start_time))
        chapters.append(
            ChapterSequence(
                start_time=seq.start_time,
                end_time=seq.end_time,
                attack_defend=seq.attack_defend,
                tie_up=BuildTieEntry(seq.tie_up, seq.opp_tie),
                team_moves=seq.team_moves,
                op_moves=seq.op_moves,
                team_scores=seq.team_scores,
                op_scores=seq.op_scores,
            )
        )
        last_end = seq.end_time
    chapters.append(ChapterSequence.MakeEmptyChap(last_end, duration))
    return chapters


def run_tag_job(
    ctx: JobContext,
    video: str,
    wrestler: str,
    sequences: list[TagSequence],
    opponent: str = "",
    match_result: str = "",
) -> dict[str, Any]:
    """Tag an untagged video by embedding chapter metadata.

    Args:
        ctx: Job context for progress reporting.
        video: The untagged video file name.
        wrestler: The wrestler name to embed as the title tag.
        sequences: The real sequences, in chronological order.
        opponent: The opponent's name for dual-wrestler mode, else "".
        match_result: The tagged wrestler's result ("", "W", or "L").

    Returns:
        A dict describing the result, e.g. ``{"output": "match.mkv"}``.

    Raises:
        ValueError: If the payload is invalid (missing name/sequences, bad times, overlaps).
        FileNotFoundError: If the source video does not exist.
        RuntimeError: If the video duration can't be read or ffmpeg fails.
    """
    wrestler_clean: str = wrestler.strip()
    if not wrestler_clean:
        raise ValueError("Wrestler name is required")

    if not sequences:
        raise ValueError("At least one sequence is required")

    input_path: Path = untaged_dir / video
    if not input_path.is_file():
        raise FileNotFoundError(f"Video not found: {video}")

    validate_sequences(sequences)

    duration: int = GetVidDuration(input_path)
    if duration <= 0:
        raise RuntimeError(f"Could not determine duration for '{video}'")

    chapters: list[ChapterSequence] = build_chapters(sequences, duration)
    title: str = BuildTagTitle(wrestler_clean, opponent, match_result)

    ctx.report(10, "Writing metadata...")
    metadata_path: Path = tmp_dir / f"metadata-{ctx.job_id}.txt"
    try:
        metadata_path.write_text(build_tag_metadata(chapters, title))
    except OSError as e:
        raise RuntimeError(f"Could not write metadata file: {e}")

    output_name: str = Path(video).with_suffix(".mkv").name
    output_path: Path = taged_dir / output_name

    ctx.report(50, "Tagging video...")

    def _on_progress(elapsed: int, desc: str) -> None:
        pct: float = min(99.0, 50.0 + 49.0 * elapsed / max(1, duration))
        ctx.report(pct, desc)

    result: FfmpegResult = run_ffmpeg(
        build_tag_cmd(input_path, metadata_path, output_path),
        description=f"Tagging {video}",
        on_progress=_on_progress,
        cancel_event=None,
    )

    metadata_path.unlink(missing_ok=True)

    if not result.success or not output_path.exists():
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"Tagging failed: {result.message}")

    input_path.unlink(missing_ok=True)

    ctx.report(100, f"Tagged {output_name}")
    logging.info(f"Tagged '{video}' for {wrestler_clean} -> {output_path}")
    return {"output": output_name}
