"""Search job for the web app — query all wrestler tagged data.

Every wrestler's
compiled CSV is loaded via ``LoadAllWrestlerData`` and filtered through a
``QueryBuilder`` chain. The matching sequences are returned as JSON or
exported as CSV.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

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
    QueryBuilder,
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
    "adjusted_net_points",
]


class SearchQuery(BaseModel):
    """Payload for POST /api/search — the filter criteria.

    ``min_points``/``max_points`` bound the adjusted net points range (net
    points plus the pin bonus), matching PCA dot coloring and report stats.
    ``min_raw_points``/``max_raw_points`` bound the raw net points range.
    """

    wrestler: str = ""
    attack_mode: AttackMode = "All"
    tie_up: str = ""
    team_move: str = ""
    opp_move: str = ""
    min_points: int = -20
    max_points: int = 20
    match_result: str = ""
    min_date: str = ""
    max_date: str = ""
    min_raw_points: int = -20
    max_raw_points: int = 20
    min_duration: int | None = None
    max_duration: int | None = None
    sort_by: str = ""
    ascending: bool = True
    limit: int | None = None
    offset: int = 0


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


def _build_query(query: SearchQuery, df: pd.DataFrame) -> pd.DataFrame:
    """Apply all SearchQuery filters to a single wrestler DataFrame via QueryBuilder."""
    qb: QueryBuilder = (
        QueryBuilder(df)
        .attack_filter(query.attack_mode)
        .tie_filter(query.tie_up)
        .team_move_filter(query.team_move)
        .opp_move_filter(query.opp_move)
        .match_result_filter(query.match_result)
        .date_range_filter(query.min_date, query.max_date)
        .adjusted_net_points_filter(query.min_points, query.max_points)
        .net_points_filter(query.min_raw_points, query.max_raw_points)
    )
    if query.min_duration is not None or query.max_duration is not None:
        qb.duration_filter(query.min_duration, query.max_duration)
    sort_col: str = query.sort_by.strip() or None
    return qb.apply(
        sort_by=sort_col,
        ascending=query.ascending,
        limit=query.limit,
        offset=query.offset,
    )


def execute_search(query: SearchQuery) -> list[dict[str, Any]]:
    """Run a search across all compiled wrestler data.

    Args:
        query: The filter criteria.

    Returns:
        A list of match dicts (wrestler, origin, video, times, attacking,
        tie_up, team_moves, opponent_moves, adjusted_net_points).
    """
    # Single-wrestler fast path: load only the requested CSV.
    if query.wrestler and query.wrestler != "All":
        target = csv_dir / f"{query.wrestler}.csv"
        all_data: dict[str, pd.DataFrame] = (
            {query.wrestler: MakeFormattedDataFrame(target)} if target.is_file() else {}
        )
    else:
        all_data = LoadAllWrestlerData(csv_dir)

    rows: list[dict[str, Any]] = []

    for wrestler_name, df in all_data.items():
        subset: pd.DataFrame = _build_query(query, df)

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
