"""Search job for the web app — query all wrestler tagged data.

Ports ``SearchWidget`` from scripts/qt_app/search_widget.py: every wrestler's
compiled CSV is loaded via ``LoadAllWrestlerData`` and filtered by wrestler,
attack/defense mode, starting tie-up, team/opponent move substrings, and a net
points range. The matching sequences are returned as JSON or exported as CSV.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

from scripts.helpers import (
    COL_ATTACKING,
    COL_END_TIME,
    COL_NET_POINTS,
    COL_OPPONENT_MOVES,
    COL_START_TIME,
    COL_TEAM_MOVES,
    COL_TIE_UP,
    LoadAllWrestlerData,
    csv_dir,
)

AttackMode = Literal["All", "Attacking", "Defending"]

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
    "net_points",
]


class SearchQuery(BaseModel):
    """Payload for POST /api/search — the filter criteria."""

    wrestler: str = ""
    attack_mode: AttackMode = "All"
    tie_up: str = ""
    team_move: str = ""
    opp_move: str = ""
    min_points: int = -20
    max_points: int = 20


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


def execute_search(query: SearchQuery) -> list[dict[str, Any]]:
    """Run a search across all compiled wrestler data.

    Args:
        query: The filter criteria.

    Returns:
        A list of match dicts (wrestler, origin, video, times, attacking,
        tie_up, team_moves, opponent_moves, net_points).
    """
    all_data: dict[str, pd.DataFrame] = LoadAllWrestlerData(csv_dir)
    team_move: str = query.team_move.strip().lower()
    opp_move: str = query.opp_move.strip().lower()
    rows: list[dict[str, Any]] = []

    for wrestler_name, df in all_data.items():
        if query.wrestler and query.wrestler != "All" and wrestler_name != query.wrestler:
            continue

        subset: pd.DataFrame = df
        if query.attack_mode == "Attacking":
            subset = subset[subset[COL_ATTACKING]]
        elif query.attack_mode == "Defending":
            subset = subset[~subset[COL_ATTACKING]]

        if query.tie_up.strip():
            subset = subset[subset[COL_TIE_UP] == query.tie_up.strip()]

        if team_move:
            subset = subset[subset[COL_TEAM_MOVES].apply(
                lambda moves: isinstance(moves, list)
                and any(team_move in str(move).lower() for move in moves)
            )]

        if opp_move:
            subset = subset[subset[COL_OPPONENT_MOVES].apply(
                lambda moves: isinstance(moves, list)
                and any(opp_move in str(move).lower() for move in moves)
            )]

        subset = subset[
            (subset[COL_NET_POINTS] >= query.min_points)
            & (subset[COL_NET_POINTS] <= query.max_points)
        ]

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
                "net_points": int(row[COL_NET_POINTS]),
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
