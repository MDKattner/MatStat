"""Search job for the web app — query all compiled wrestler data.

Every wrestler's compiled data is loaded from the SQLite store via
``scripts.storage`` and filtered through the shared ``apply_query``
implementation. The matching sequences are returned as JSON or exported as CSV.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import pandas as pd

from scripts import storage
from scripts.helpers import (
    COL_ADJUSTED_NET_POINTS,
    COL_ATTACKING,
    COL_END_TIME,
    COL_OPPONENT_MOVES,
    COL_START_TIME,
    COL_TEAM_MOVES,
    COL_TIE_UP,
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
    """List wrestlers with compiled data, excluding the UNKNOWN sentinel.

    Returns:
        The sorted wrestler names in the database, minus UNKNOWN.
    """
    return storage.list_wrestlers()


def _join_list(value: Any) -> str:
    """Join a list column into a display string, tolerating non-list values."""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return ""


def _load_frames(spec: QuerySpec) -> dict[str, pd.DataFrame]:
    """Load the compiled frames selected by ``spec`` (all when none are named)."""
    names: list[str] = selected_wrestlers(spec)
    if not names:
        return storage.load_all_frames()
    return storage.load_frames(names)


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
        if subset.empty:
            continue
        # Pull whole columns out once instead of building a Series per row via
        # iterrows(), which dominates wide result sets.
        origins: list[str] = [str(origin) for origin in subset.index]
        starts: list[Any] = subset[COL_START_TIME].tolist()
        ends: list[Any] = subset[COL_END_TIME].tolist()
        attacking: list[Any] = subset[COL_ATTACKING].tolist()
        tie_ups: list[Any] = subset[COL_TIE_UP].tolist()
        team_moves: list[Any] = subset[COL_TEAM_MOVES].tolist()
        opponent_moves: list[Any] = subset[COL_OPPONENT_MOVES].tolist()
        points: list[Any] = subset[COL_ADJUSTED_NET_POINTS].tolist()
        for i, origin in enumerate(origins):
            rows.append({
                "wrestler": wrestler_name,
                "origin": origin,
                "video": origin.split(":")[0],
                "start_time": int(starts[i]),
                "end_time": int(ends[i]),
                "attacking": "A" if bool(attacking[i]) else "D",
                "tie_up": str(tie_ups[i]) or "",
                "team_moves": _join_list(team_moves[i]),
                "opponent_moves": _join_list(opponent_moves[i]),
                "adjusted_net_points": int(points[i]),
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
