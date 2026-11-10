"""Trend chart for the web app — per-match points over a wrestler's season.

Implements the Notes.md trends item: a wrestler's compiled sequences are
grouped into matches (each video is a match), ordered by match date, and their
net (or adjusted) points per match are plotted against a cumulative average and
a rolling average. Dots are colored by points (negative red, zero gray,
positive green), mirroring the PCA tab's colorscale. Only sequences whose match
date is recorded are plotted; rows without a date are skipped and counted.

Args are validated in app.py; the figure builder raises ValueError when there
is no compiled data, no dated rows, or a bad rolling window.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from scripts.helpers import (
    COL_ADJUSTED_NET_POINTS,
    COL_MATCH_DATE,
    COL_NET_POINTS,
    MakeFormattedDataFrame,
    csv_dir,
)

TrendMetric = Literal["net", "adjusted"]


def _video_of(origin: str) -> str:
    """Strip the chapter suffix from an Origin string ("v.mkv:3" -> "v.mkv")."""
    return str(origin).split(":")[0]


def build_trend_figure(
    wrestler: str, metric: TrendMetric = "net", window: int = 5
) -> dict[str, Any]:
    """Build the interactive per-match trend figure for a wrestler.

    Each compiled video is treated as a match; the match's points are the sum
    of the metric column (net or adjusted) over its dated sequences. Matches
    are ordered by match date (YYYY-MM-DD string order) with ties broken by
    video name. The figure shows the per-match points plus a cumulative
    average and a rolling average over the last ``window`` matches.

    Args:
        wrestler: The wrestler name (must match a CSV stem in csv_dir).
        metric: "net" for raw net points or "adjusted" for net plus the active
            ruleset's pin bonus.
        window: The rolling-average window in matches.

    Returns:
        A dict with the plotly figure JSON and metadata:
        ``{"fig_json", "n_matches", "skipped_rows", "wrestler", "metric",
        "window"}``.

    Raises:
        ValueError: If there is no compiled data for the wrestler, no rows
            carry a match date, or the window is smaller than 1.
    """
    name: str = wrestler.strip()
    if window < 1:
        raise ValueError("Rolling window must be at least 1.")

    csv_path: Path = (csv_dir / name).with_suffix(".csv")
    if not csv_path.is_file():
        raise ValueError(
            f"No compiled data for '{name}'. Run Compile Stats first."
        )
    df: pd.DataFrame = MakeFormattedDataFrame(csv_path)

    if COL_MATCH_DATE not in df.columns:
        raise ValueError(
            f"No match dates recorded for '{name}'. Recompile stats after "
            "tagging with a match date."
        )

    dated: pd.DataFrame = df[df[COL_MATCH_DATE].astype(str).str.strip() != ""]
    skipped: int = int(len(df) - len(dated))
    if dated.empty:
        raise ValueError(
            f"No matches with a recorded date for '{name}'. Tag a match date "
            "and recompile stats."
        )

    point_col: str = COL_ADJUSTED_NET_POINTS if metric == "adjusted" else COL_NET_POINTS
    matches: pd.DataFrame = (
        dated.assign(video=dated.index.to_series().apply(_video_of))
        .groupby([COL_MATCH_DATE, "video"], sort=False)[point_col]
        .sum()
        .reset_index()
        .sort_values([COL_MATCH_DATE, "video"], kind="stable")
    )

    dates: np.ndarray = matches[COL_MATCH_DATE].to_numpy()
    videos: np.ndarray = matches["video"].to_numpy()
    points: np.ndarray = matches[point_col].to_numpy(dtype=float)
    n_matches: int = int(len(points))
    match_numbers: np.ndarray = np.arange(1, n_matches + 1)

    cumulative: np.ndarray = (
        np.cumsum(points) / np.arange(1, n_matches + 1)
    )
    rolling: np.ndarray = (
        pd.Series(points).rolling(window=window, min_periods=1).mean().to_numpy()
    )

    cmax: float = max(1.0, float(np.max(np.abs(points))))
    customdata: list[list[Any]] = [
        [str(d), str(v), float(p)]
        for d, v, p in zip(dates, videos, points)
    ]
    fig: go.Figure = go.Figure(
        data=[
            go.Scatter(
                mode="markers",
                x=match_numbers,
                y=points,
                name="Points",
                customdata=customdata,
                marker={
                    "color": points,
                    "colorscale": [
                        [0.0, "rgb(178,34,34)"],
                        [0.5, "rgb(150,150,150)"],
                        [1.0, "rgb(34,139,34)"],
                    ],
                    "cmin": -cmax,
                    "cmax": cmax,
                    "size": 9,
                },
                hovertemplate=(
                    "%{customdata[0]}<br>%{customdata[1]}<br>"
                    f"{metric} points: %{{customdata[2]:.1f}}<extra></extra>"
                ),
            ),
            go.Scatter(
                mode="lines",
                x=match_numbers,
                y=cumulative,
                name="Cumulative avg",
                line={"color": "rgb(30,60,180)", "width": 2},
            ),
            go.Scatter(
                mode="lines",
                x=match_numbers,
                y=rolling,
                name=f"{window}-match avg",
                line={"color": "rgb(200,120,0)", "width": 2},
            ),
        ]
    )
    fig.update_layout(
        title=f"{name} — {metric} points per match",
        xaxis_title="Match (by date)",
        yaxis_title="Points",
        hovermode="closest",
        template="plotly_white",
    )
    return {
        "fig_json": fig.to_json(),
        "n_matches": n_matches,
        "skipped_rows": skipped,
        "wrestler": name,
        "metric": metric,
        "window": window,
    }
