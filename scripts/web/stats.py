"""Compile Stats job for the web app — extract chapter data into wrestler CSVs.

Every tagged video in vids/taged/ is probed for its wrestler name and chapter
rows, rows are aggregated per wrestler, and the results are written to
stats/wrestler_data/{name}.csv. Runs inside a JobManager thread; progress is
reported via the JobContext.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.helpers import (
    COL_OPPONENT_SCORES,
    COL_TEAM_SCORES,
    AdjustedNetPoints,
    CalculateNetPoints,
    MakeNameAndCSV,
    ParseTaggedName,
    ReloadScoringMap,
    SwapPerspectiveCSV,
    csv_dir,
    taged_dir,
)
from scripts.web.jobs import JobContext


def _process_video_safely(vid_path: Path) -> tuple[str | None, str]:
    """Extract a wrestler name and CSV data from a tagged video.

    Returns (name, data) on success or (None, error).

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


def _enrich_csv_rows(rows: list[str], result: str = "") -> list[str]:
    """Append Net Points, Adjusted Net Points, and the match result to rows.

    Rows are parsed with the same 9-field schema MakeFormattedDataFrame reads
    (origin, start, end, attacking, tie, team moves, opponent moves, team
    scores, opponent scores); rows that fail to parse are passed through
    unchanged so no data is ever lost. The scoring map is refreshed from the
    active ruleset so the appended values match what MakeFormattedDataFrame
    recomputes on load. ``result`` ("", "W", or "L") is the match result for
    the video these rows came from, stored as the trailing ``W/L`` column.

    Args:
        rows: Raw CSV data rows from MakeNameAndCSV.
        result: The tagged wrestler's match result for this video.

    Returns:
        The rows with `,net,adjusted,result` appended where parseable.
    """
    ReloadScoringMap()
    enriched: list[str] = []
    for row in rows:
        parts: list[str] = row.split(",")
        if len(parts) != 9:
            enriched.append(row)
            continue
        scores: pd.Series = pd.Series({
            COL_TEAM_SCORES: [s for s in parts[7].split(":") if s],
            COL_OPPONENT_SCORES: [s for s in parts[8].split(":") if s],
        })
        net: np.int16 = CalculateNetPoints(scores)
        adjusted: np.int16 = AdjustedNetPoints(scores)
        enriched.append(f"{row},{net},{adjusted},{result}")
    return enriched


def run_compile_stats_job(
    ctx: JobContext,
    wrestler_names: list[str],
) -> dict[str, Any]:
    """Compile all tagged videos into per-wrestler CSV files.

    Every tagged video is probed for its format title and chapter rows. The
    title is parsed into (wrestler, opponent, result); rows are enriched with
    the match result and bucketed under the wrestler, and in dual mode a
    perspective-swapped copy is bucketed under the opponent (when the opponent
    is also in the roster). Results are written to
    stats/wrestler_data/{name}.csv.

    Args:
        ctx: Job context for progress reporting and cancellation.
        wrestler_names: Wrestler names from the roster (Wrestlers.json), in order.

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
            ctx.report(100.0 * (i + 1) / total, f"Processing {vid_path.name}")
            continue
        wrestler: str
        opponent: str | None
        result: str
        wrestler, opponent, result = ParseTaggedName(name)
        if wrestler not in wrestler_to_rows:
            error_msg: str = (
                f"Wrestler '{wrestler}' from {vid_path.name} not found in "
                f"the roster (Wrestlers.json). Skipping data."
            )
            logging.warning(error_msg)
            errors.append(error_msg)
            ctx.report(100.0 * (i + 1) / total, f"Processing {vid_path.name}")
            continue
        lines: list[str] = data.split("\n") if data else []
        if lines:
            enriched: list[str] = _enrich_csv_rows(lines, result)
            wrestler_to_rows[wrestler].extend(enriched)
            if opponent:
                if opponent not in wrestler_to_rows:
                    error_msg = (
                        f"Opponent '{opponent}' from {vid_path.name} not found "
                        f"in the roster (Wrestlers.json). Skipping opponent data."
                    )
                    logging.warning(error_msg)
                    errors.append(error_msg)
                else:
                    swapped: str = SwapPerspectiveCSV("\n".join(enriched))
                    wrestler_to_rows[opponent].extend(swapped.split("\n"))
        ctx.report(100.0 * (i + 1) / total, f"Processing {vid_path.name}")

    wrestler_counts: dict[str, int] = {}
    for name, rows in wrestler_to_rows.items():
        wrestler_counts[name] = len(rows)
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
