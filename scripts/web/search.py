"""Search job for the web app — query all wrestler tagged data.

Every wrestler's compiled CSV is loaded via ``LoadAllWrestlerData`` and filtered
through the shared ``apply_query`` implementation. The matching sequences are
returned as JSON or exported as CSV.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import pandas as pd

from scripts.helpers import (
    COL_ADJUSTED_NET_POINTS,
    COL_ATTACKING,
    COL_END_TIME,
    COL_NET_POINTS,
    COL_OPPONENT_MOVES,
    COL_START_TIME,
    COL_TEAM_MOVES,
    COL_TIE_UP,
    LoadAllWrestlerData,
    MakeFormattedDataFrame,
    csv_dir,
)
from scripts.web.query import QuerySpec, apply_query, selected_wrestlers

# Kept as an alias for existing imports; QuerySpec is the only request shape.
SearchQuery = QuerySpec

_CSV_FIELDS: list[str] = [
    "wrestler",
    "origin",
    "video",
    "start_time",
    "end_time",
    "attacking",
    "tie_up",
    "team_moves",
    "opponent_moves",
    "adjusted_net_points",
]


def list_wrestlers() -> list[str]:
    """List wrestlers with compiled CSV data, excluding the UNKNOWN sentinel.

    Returns:
        The sorted CSV stems under csv_dir, minus UNKNOWN.
    """
    return sorted(
        p.stem for p in csv_dir.glob("*.csv") if p.name != "UNKNOWN.csv"
    )


def _join_list(value: Any) -> str:
    """Join a list column into a display string, tolerating non-list values."""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return ""


def _load_frames(spec: QuerySpec) -> dict[str, pd.DataFrame]:
    """Load the CSV frames selected by ``spec`` (all when none are named)."""
    names: list[str] = selected_wrestlers(spec)
    if not names:
        return LoadAllWrestlerData(csv_dir)
    frames: dict[str, pd.DataFrame] = {}
    for name in names:
        target = csv_dir / f"{name}.csv"
        if target.is_file():
            frames[name] = MakeFormattedDataFrame(target)
    return frames


def execute_search(query: QuerySpec) -> list[dict[str, Any]]:
    """Run a search across the selected compiled wrestler data.

    Args:
        query: The shared filter specification.

    Returns:
        A list of match dicts (wrestler, origin, video, times, attacking,
        tie_up, team_moves, opponent_moves, adjusted_net_points).
    """
    all_data: dict[str, pd.DataFrame] = _load_frames(query)
    rows: list[dict[str, Any]] = []

    for wrestler_name, df in all_data.items():
        subset: pd.DataFrame = apply_query(df, query, wrestler_name)

        for index, row in subset.iterrows():
            rows.append({
                "wrestler": wrestler_name,
                "origin": str(index),
                "video": str(index).split(":")[0],
                "start_time": int(row[COL_START_TIME]),
                "end_time": int(row[COL_END_TIME]),
                "attacking": "A" if bool(row[COL_ATTACKING]) else "D",
                "tie_up": str(row[COL_TIE_UP]) or "",
                "team_moves": _join_list(row[COL_TEAM_MOVES]),
                "opponent_moves": _join_list(row[COL_OPPONENT_MOVES]),
                "adjusted_net_points": int(row[COL_ADJUSTED_NET_POINTS]),
            })

    return rows


def search_to_csv(rows: list[dict[str, Any]]) -> str:
    """Serialize search matches as CSV text for the export endpoint.

    Args:
        rows: The match dicts from execute_search.

    Returns:
        The CSV content (with header).
    """
    buffer: io.StringIO = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()
