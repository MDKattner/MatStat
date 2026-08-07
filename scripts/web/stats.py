"""Compile Stats job for the web app — extract chapter data into wrestler CSVs.

Ports ``CompileStatsWorker`` + ``CompileStatsWidget`` from scripts/qt_app/:
every tagged video in vids/taged/ is probed for its wrestler name and chapter
rows, rows are aggregated per wrestler, and the results are written to
stats/wrestler_data/{name}.csv. Runs inside a JobManager thread; progress is
reported via the JobContext.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from scripts.helpers import MakeNameAndCSV, csv_dir, taged_dir
from scripts.web.jobs import JobContext


def _process_video_safely(vid_path: Path) -> tuple[str | None, str]:
    """Extract a wrestler name and CSV data from a tagged video.

    Mirrors ``workers._ProcessVideoSafely`` from the Qt app so the web layer
    never imports PyQt6. Returns (name, data) on success or (None, error).

    Args:
        vid_path: The path to the tagged video file.

    Returns:
        A (name, data) tuple, or (None, error_message) on failure.
    """
    try:
        name: str
        data: str
        name, data = MakeNameAndCSV(vid_path)
        return (name, data)
    except Exception as e:
        error_msg: str = f"Failed to process video '{vid_path.name}': {e}"
        logging.error(error_msg)
        return (None, error_msg)


def run_compile_stats_job(
    ctx: JobContext,
    wrestler_names: list[str],
) -> dict[str, Any]:
    """Compile all tagged videos into per-wrestler CSV files.

    Args:
        ctx: Job context for progress reporting and cancellation.
        wrestler_names: Wrestler names from Wrestlers.config, in order.

    Returns:
        A dict with per-wrestler sequence counts and any processing errors:
        ``{"videos_processed": int, "wrestlers": {name: count}, "errors": [...]}``.
    """
    wrestler_to_rows: dict[str, list[str]] = {name: [] for name in wrestler_names}
    errors: list[str] = []

    try:
        video_paths: list[Path] = sorted(
            p for p in taged_dir.iterdir()
            if p.is_file() and not p.name.startswith(".")
        )
    except FileNotFoundError:
        logging.warning(f"Tagged videos directory not found: {taged_dir}")
        video_paths = []

    total: int = len(video_paths)
    if total == 0:
        ctx.report(100, "No tagged videos found.")
        return {
            "videos_processed": 0,
            "wrestlers": {name: 0 for name in wrestler_names},
            "errors": [],
        }

    for i, vid_path in enumerate(video_paths):
        if ctx.cancelled:
            return {"videos_processed": i, "wrestlers": {}, "errors": errors}
        name: str | None
        data: str
        name, data = _process_video_safely(vid_path)
        if name is None:
            errors.append(data)
        elif name not in wrestler_to_rows:
            error_msg: str = (
                f"Wrestler '{name}' from {vid_path.name} not found in "
                f"Wrestlers.config. Skipping data."
            )
            logging.warning(error_msg)
            errors.append(error_msg)
        elif data:
            wrestler_to_rows[name].append(data)
        ctx.report(100.0 * (i + 1) / total, f"Processing {vid_path.name}")

    wrestler_counts: dict[str, int] = {}
    for name, rows in wrestler_to_rows.items():
        wrestler_counts[name] = sum(len(entry.split("\n")) for entry in rows)
        if not rows:
            continue
        output_path: Path = (csv_dir / name).with_suffix(".csv")
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("\n".join(rows))
        except OSError as e:
            error_msg = f"Failed to write data to '{output_path}': {e}"
            logging.error(error_msg)
            errors.append(error_msg)

    ctx.report(100, f"Compiled {total} videos.")
    logging.info(
        f"Compiled stats: {total} videos, {len(wrestler_counts)} wrestler files."
    )
    return {"videos_processed": total, "wrestlers": wrestler_counts, "errors": errors}
