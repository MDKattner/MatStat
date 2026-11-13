"""Shared query specification for Search, Find, Export, Reel, PCA, and Trends.

Every endpoint that filters wrestler sequences accepts the same ``QuerySpec``
and runs it through ``apply_query``, so the paths cannot drift apart. The
model forbids unknown fields: the retired ``filter_type``/``filter_item`` and
single ``wrestler`` shapes fail validation with a ``422`` instead of being
silently accepted.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from scripts.helpers import QueryBuilder


AttackMode = Literal["All", "Attacking", "Defending"]
MoveMatch = Literal["substring", "exact"]


class QuerySpec(BaseModel):
    """One serializable filter specification shared by every query endpoint.

    ``wrestlers`` selects the compiled wrestler data to search (empty means
    "all"); ``origins`` optionally pins an exact result set (used by export and
    reel generation). ``move_match`` controls whether move filters are substring
    (default) or exact; tie filters are always exact.
    """

    model_config = ConfigDict(extra="forbid")

    wrestlers: list[str] = []
    origins: list[str] | None = None
    attack_mode: AttackMode = "All"
    tie_up: str = ""
    opp_tie: str = ""
    team_move: str = ""
    opp_move: str = ""
    move_match: MoveMatch = "substring"
    match_result: str = ""
    min_date: str = ""
    max_date: str = ""
    min_points: int = -20
    max_points: int = 20
    min_raw_points: int = -20
    max_raw_points: int = 20
    min_duration: int | None = None
    max_duration: int | None = None
    sort_by: str = ""
    ascending: bool = True
    limit: int | None = None
    offset: int = 0


def selected_wrestlers(spec: QuerySpec) -> list[str]:
    """Return the non-empty wrestler names in ``spec`` (``"All"`` means none)."""
    return [
        name.strip()
        for name in spec.wrestlers
        if name.strip() and name.strip() != "All"
    ]


def apply_query(
    df: pd.DataFrame, spec: QuerySpec, wrestler: str = ""
) -> pd.DataFrame:
    """Apply one ``QuerySpec`` to a single wrestler's sequence DataFrame.

    This is the only filter implementation used by Search and Find. When
    ``spec.origins`` is set the exact rows are returned in the given order and
    every origin must exist; otherwise the QueryBuilder filters run.

    Args:
        df: A formatted sequence DataFrame (via ``storage.load_wrestler_frame``).
        spec: The shared filter specification.
        wrestler: Optional wrestler name for origin-validation messages.

    Returns:
        The filtered DataFrame.

    Raises:
        ValueError: If ``spec.origins`` contains an origin absent from ``df``.
    """
    if spec.origins is not None:
        missing: list[str] = [
            origin for origin in spec.origins if origin not in df.index
        ]
        if missing:
            label: str = f" for '{wrestler}'" if wrestler else ""
            raise ValueError(
                f"Unknown origin(s){label}: {', '.join(missing)}"
            )
        return df.loc[list(spec.origins)]

    qb: QueryBuilder = (
        QueryBuilder(df)
        .attack_filter(spec.attack_mode)
        .tie_filter(spec.tie_up)
        .opp_tie_filter(spec.opp_tie)
        .team_move_filter(spec.team_move, exact=spec.move_match == "exact")
        .opp_move_filter(spec.opp_move, exact=spec.move_match == "exact")
        .match_result_filter(spec.match_result)
        .date_range_filter(spec.min_date, spec.max_date)
        .adjusted_net_points_filter(spec.min_points, spec.max_points)
        .net_points_filter(spec.min_raw_points, spec.max_raw_points)
    )
    if spec.min_duration is not None or spec.max_duration is not None:
        qb.duration_filter(spec.min_duration, spec.max_duration)
    sort_col: str | None = spec.sort_by.strip() or None
    return qb.apply(
        sort_by=sort_col,
        ascending=spec.ascending,
        limit=spec.limit,
        offset=spec.offset,
    )
