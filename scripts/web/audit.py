"""Auditing job for the web app — review tagged videos and re-tag them.

Every tagged video is probed for its wrestler name and embedded sequences so
they can be reviewed. Re-tagging re-embeds new chapter metadata directly into
the tagged file (the original was deleted when it was first tagged), then
recompiles stats. Runs inside a JobManager thread; progress is reported via
the JobContext.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from scripts.helpers import (
    BuildTagTitle,
    ChapterSequence,
    GetVidDuration,
    MakeNameAndCSV,
    ParseTaggedName,
    ValidateMatchDate,
    taged_dir,
    tmp_dir,
)
from scripts.web import configs, stats, tag
from scripts.web.ffmpeg import FfmpegResult, run_ffmpeg
from scripts.web.jobs import JobContext
from scripts.web.tag import TagSequence


def build_mkvpropedit_cmd(
    path: Path, title: str, match_date: str = ""
) -> list[str]:
    """Build the metadata-only edit command for ``mkvpropedit``.

    Args:
        path: The tagged video to edit in place.
        title: The new format title tag.
        match_date: The new ``matchdate`` tag ("YYYY-MM-DD", "" to clear it).

    Returns:
        The mkvpropedit command as a list of arguments.
    """
    cmd: list[str] = [
        "mkvpropedit", str(path),
        "--edit", "info", "--set", f"title={title}",
    ]
    if match_date:
        cmd += ["--edit", "tags", "--set", f"matchdate={match_date}"]
    else:
        cmd += ["--edit", "tags", "--delete", "matchdate"]
    return cmd


def _run_mkvpropedit(cmd: list[str]) -> tuple[bool, str]:
    """Run mkvpropedit, returning success and a human-readable message."""
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )
    except OSError as e:
        return (False, str(e))
    if completed.returncode != 0:
        detail: str = (completed.stderr or completed.stdout or "").strip()
        return (False, detail or f"mkvpropedit exited {completed.returncode}")
    return (True, "ok")


def _existing_chapters(path: Path) -> list[ChapterSequence]:
    """Read a tagged video's current chapters back for a stream-copy fallback."""
    _name, _date, csv_data = MakeNameAndCSV(path)
    chapters: list[ChapterSequence] = []
    for line in csv_data.splitlines():
        if not line.strip():
            continue
        chap: ChapterSequence = ChapterSequence.FromCSVRow(line)
        if chap.start_time < chap.end_time:
            chapters.append(chap)
    return chapters


class RetagRequest(BaseModel):
    """Payload for POST /api/audit/{file}/retag."""

    wrestler: str
    opponent: str = ""
    match_result: str = ""
    match_date: str = ""
    sequences: list[TagSequence] = Field(default_factory=list)
    recompile: bool = False


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
        A list of ``{"name", "wrestler", "opponent", "result", "match_date",
        "sequences"}`` dicts, sorted by video name. Videos that fail to probe
        are listed with no sequences.
    """
    videos: list[dict[str, Any]] = []
    if not taged_dir.exists():
        return videos
    for video_path in sorted(taged_dir.iterdir()):
        if not video_path.is_file() or video_path.name.startswith("."):
            continue
        name: str
        match_date: str
        csv_data: str
        name, match_date, csv_data = MakeNameAndCSV(video_path)
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
            "match_date": match_date,
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
    match_date: str = "",
    recompile: bool = False,
) -> dict[str, Any]:
    """Re-embed chapter metadata into an already-tagged video, then recompile.

    With sequences, the tagged file is re-muxed with new metadata (stream copy,
    so no quality loss) and the old chapters are replaced in place. With no
    sequences, only the title/date/result are edited: ``mkvpropedit`` performs a
    true metadata-only edit when it is installed, otherwise the job falls back
    to the same stream-copy remux, preserving the existing chapters. When
    ``recompile`` is True, compile stats runs over every tagged video so the
    CSVs reflect the edits; otherwise tabulation is deferred to the
    Tabulate & Report tab.

    Args:
        ctx: Job context for progress reporting.
        video: The tagged video file name in vids/taged/.
        wrestler: The wrestler name to embed as the title tag.
        sequences: The real sequences in chronological order; empty for a
            metadata-only edit.
        opponent: The opponent's name for dual-wrestler mode, else "".
        match_result: The tagged wrestler's result ("", "W", or "L").
        match_date: The match date ("YYYY-MM-DD" or "").
        recompile: Whether to re-tabulate all sequences after re-tagging.

    Returns:
        A dict describing the result, e.g.
        ``{"output": "match.mkv", "mode": "mkvpropedit",
        "elapsed_seconds": 0.01, "compile": {...}}``. ``mode`` is
        ``"mkvpropedit"`` or ``"ffmpeg_copy"``; ``compile`` is None when
        recompiling is skipped (either not requested or no roster configured).

    Raises:
        ValueError: If the payload is invalid (missing name, bad times, overlaps).
        FileNotFoundError: If the tagged video does not exist.
        RuntimeError: If the video duration can't be read or ffmpeg fails.
    """
    wrestler_clean: str = wrestler.strip()
    if not wrestler_clean:
        raise ValueError("Wrestler name is required")

    date_clean: str = ValidateMatchDate(match_date)

    input_path: Path = taged_dir / video
    if not input_path.is_file():
        raise FileNotFoundError(f"Video not found: {video}")

    title: str = BuildTagTitle(wrestler_clean, opponent, match_result)
    metadata_only: bool = not sequences
    duration: int = 0
    chapters: list[ChapterSequence] = []

    if not metadata_only:
        tag.validate_sequences(sequences)
        duration = GetVidDuration(input_path)
        if duration <= 0:
            raise RuntimeError(f"Could not determine duration for '{video}'")
        tag.validate_sequences(sequences, duration=duration)
        chapters = tag.build_chapters(sequences, duration)

    output_path: Path = taged_dir / Path(video).with_suffix(".mkv").name
    tmp_output: Path = tmp_dir / f"retag-{ctx.job_id}.mkv"

    def _remux(swap_chapters: list[ChapterSequence]) -> None:
        """Write a metadata file and stream-copy remux into place."""
        ctx.report(50, "Re-tagging video...")
        metadata_path: Path = tmp_dir / f"retag-metadata-{ctx.job_id}.txt"
        try:
            metadata_path.write_text(
                tag.build_tag_metadata(swap_chapters, title, date_clean)
            )
        except OSError as e:
            raise RuntimeError(f"Could not write metadata file: {e}")

        def _on_progress(elapsed: int, desc: str) -> None:
            pct: float = (
                min(99.0, 50.0 + 49.0 * elapsed / max(1, duration))
                if duration > 0 else 50.0
            )
            ctx.report(pct, desc)

        try:
            result: FfmpegResult = run_ffmpeg(
                tag.build_tag_cmd(input_path, metadata_path, tmp_output),
                description=f"Re-tagging {video}",
                on_progress=_on_progress,
                cancel_event=ctx.cancel_event,
            )
        finally:
            metadata_path.unlink(missing_ok=True)

        if not result.success or not tmp_output.exists():
            tmp_output.unlink(missing_ok=True)
            raise RuntimeError(f"Re-tagging failed: {result.message}")

        tmp_output.replace(output_path)

    start: float = time.monotonic()
    mode: str = "ffmpeg_copy"
    if metadata_only:
        tool: str | None = shutil.which("mkvpropedit")
        if tool:
            ctx.report(30, "Editing metadata (mkvpropedit)...")
            cmd: list[str] = build_mkvpropedit_cmd(input_path, title, date_clean)
            cmd[0] = tool
            ok, message = _run_mkvpropedit(cmd)
            if ok:
                mode = "mkvpropedit"
            else:
                logging.warning(
                    f"mkvpropedit failed for '{video}', falling back to ffmpeg: "
                    f"{message}"
                )
        if mode != "mkvpropedit":
            _remux(_existing_chapters(input_path))
    else:
        _remux(chapters)

    elapsed: float = round(time.monotonic() - start, 2)

    compile_result: dict[str, Any] | None = None
    if recompile:
        ctx.report(90, "Recompiling stats...")
        wrestler_names: list[str] = []
        try:
            wrestler_names = configs.load_wrestler_names()
        except Exception as e:
            logging.warning(f"Could not load wrestler roster for recompile: {e}")
        compile_result = (
            stats.run_compile_stats_job(ctx, wrestler_names) if wrestler_names else None
        )

    ctx.report(100, f"Re-tagged {video} ({mode})")
    logging.info(
        f"Re-tagged '{video}' for {wrestler_clean} via {mode} in {elapsed}s"
    )
    return {
        "output": output_path.name,
        "mode": mode,
        "elapsed_seconds": elapsed,
        "compile": compile_result,
    }
