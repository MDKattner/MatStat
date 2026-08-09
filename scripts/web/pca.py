"""Interactive PCA figures for the web app — sequences as off./def. components.

Implements the Notes.md PCA item: each dot is a sequence (or a match), x is the
first principal component of the moves used BY the wrestler (COL_TEAM_MOVES,
"off."), y is the first principal component of the moves used AGAINST the
wrestler (COL_OPPONENT_MOVES, "def.") — two independent PCAs, NOT a joint
off|def matrix. Dots are colored by adjusted net points (net points plus the
active ruleset's pin bonus; negative red, zero gray, positive green).

Two layouts are supported:
- "sequences": one point per tagged sequence; PCA over the sequence rows.
- "matches": rows are aggregated per (wrestler, video) match before PCA.

Review-mandated mitigations:
- The literal move "nothing" (the config sentinel) is dropped before counting.
- Rare moves are filtered out: a move must appear in at least
  max(3, round(0.05 * n_rows)) rows.
- Only the "matches" layout standardizes (z-scores) the move columns before
  PCA; the "sequences" layout uses covariance PCA (no z-scoring) because column
  z-scoring on sparse near-binary rows over-amplifies rare moves.

Compiling a PCA reel (POST /api/pca/compile) extracts the selected sequences
and concatenates them into a highlight clip, mirroring scripts/web/clips.py.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from pydantic import BaseModel

from scripts.helpers import (
    COL_END_TIME,
    COL_ADJUSTED_NET_POINTS,
    COL_OPPONENT_MOVES,
    COL_START_TIME,
    COL_TEAM_MOVES,
    FirstPrincipalComponent,
    GenerateMoveMatrix,
    GetVideoCodecs,
    MakeFormattedDataFrame,
    clips_dir,
    csv_dir,
    taged_dir,
    tmp_dir,
)
from scripts.web.clips import build_concat_cmd, build_extract_cmd
from scripts.web.configs import load_roster
from scripts.web.ffmpeg import run_ffmpeg
from scripts.web.jobs import JobContext

PcaScope = Literal["all", "team", "wrestler"]
PcaLayout = Literal["sequences", "matches"]

_MIN_OCCURRENCES: int = 3
_MIN_OCCURRENCE_FRACTION: float = 0.05
_STREAM_COPY_DEFAULT: bool = True


def _min_occurrences(n_rows: int) -> int:
    """Compute the rare-move threshold for a given number of rows.

    A move must appear in at least max(3, round(0.05 * n_rows)) rows to be kept.

    Args:
        n_rows: The number of sequence rows in the scope.

    Returns:
        The minimum occurrence count for a move to survive filtering.
    """
    return max(_MIN_OCCURRENCES, round(_MIN_OCCURRENCE_FRACTION * n_rows))


def _sanitize(name: str) -> str:
    """Replace characters that are awkward in file names."""
    return name.replace(" ", "_").replace("/", "_")


def _load_scoped_data(scope: PcaScope, name: str) -> pd.DataFrame:
    """Load and concatenate wrestler CSVs, filtered by the requested scope.

    Args:
        scope: "all" (every wrestler), "team" (a roster team's members), or
            "wrestler" (a single wrestler).
        name: The wrestler or team name (ignored for scope="all").

    Returns:
        A concatenated DataFrame of all matching sequences (index = the Origin
        string "video.mkv:chap", with an added "wrestler" column holding the
        CSV stem). May be empty.
    """
    frames: list[pd.DataFrame] = []
    for csv_path in sorted(csv_dir.glob("*.csv")):
        if csv_path.name == "UNKNOWN.csv":
            continue
        df: pd.DataFrame = MakeFormattedDataFrame(csv_path)
        df["wrestler"] = csv_path.stem
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    data: pd.DataFrame = pd.concat(frames)
    if scope == "wrestler":
        wrestler: str = name.strip()
        data = data[data["wrestler"] == wrestler]
    elif scope == "team":
        roster: Any = load_roster()
        members: set[str] = set(roster.teams.get(name.strip(), []))
        data = data[data["wrestler"].isin(members)]
    return data


def build_pca_figure(scope: PcaScope, name: str, layout: PcaLayout) -> dict[str, Any]:
    """Build the interactive PCA scatter figure for a scope and layout.

    Args:
        scope: "all", "team", or "wrestler".
        name: The wrestler or team name (ignored for scope="all").
        layout: "sequences" (one point per sequence) or "matches" (one point
            per wrestler+video aggregation).

    Returns:
        A dict with the plotly figure JSON and metadata:
        ``{"fig_json", "off_variance", "def_variance", "n_points", "scope",
        "name", "layout"}``.

    Raises:
        ValueError: If the scope has no sequence data or no moves survive the
            rare-move filter.
    """
    df: pd.DataFrame = _load_scoped_data(scope, name)
    if df.empty:
        raise ValueError("No sequence data for the selected scope. Run Compile Stats first.")

    # Move filtering happens at the sequence level for both layouts.
    min_occ: int = _min_occurrences(len(df))
    off_mat: pd.DataFrame
    def_mat: pd.DataFrame
    off_moves: list[str]
    def_moves: list[str]
    off_mat, off_moves = GenerateMoveMatrix(df, COL_TEAM_MOVES, min_occ)
    def_mat, def_moves = GenerateMoveMatrix(df, COL_OPPONENT_MOVES, min_occ)

    videos: pd.Series = df.index.to_series().apply(lambda o: str(o).split(":")[0])

    if layout == "matches":
        keys: pd.DataFrame = pd.DataFrame({"wrestler": df["wrestler"], "video": videos})
        off_mat = off_mat.groupby([keys["wrestler"], keys["video"]], sort=True).sum()
        def_mat = def_mat.groupby([keys["wrestler"], keys["video"]], sort=True).sum()
        net_by_match: pd.Series = (
            df.groupby([keys["wrestler"], keys["video"]])[COL_ADJUSTED_NET_POINTS].sum()
        )
        start_by_match: pd.Series = (
            df.groupby([keys["wrestler"], keys["video"]])[COL_START_TIME].min()
        )
        end_by_match: pd.Series = (
            df.groupby([keys["wrestler"], keys["video"]])[COL_END_TIME].max()
        )
        net_by_match = net_by_match.reindex(off_mat.index)
        start_by_match = start_by_match.reindex(off_mat.index)
        end_by_match = end_by_match.reindex(off_mat.index)
        wrestlers: np.ndarray = off_mat.index.get_level_values("wrestler").to_numpy()
        video_names: np.ndarray = off_mat.index.get_level_values("video").to_numpy()
        starts: np.ndarray = start_by_match.to_numpy(dtype=float)
        ends: np.ndarray = end_by_match.to_numpy(dtype=float)
        net_values: np.ndarray = net_by_match.to_numpy(dtype=float)
    else:
        wrestlers = df["wrestler"].to_numpy()
        video_names = videos.to_numpy()
        starts = df[COL_START_TIME].to_numpy(dtype=float)
        ends = df[COL_END_TIME].to_numpy(dtype=float)
        net_values = df[COL_ADJUSTED_NET_POINTS].to_numpy(dtype=float)

    if off_mat.shape[1] == 0 or def_mat.shape[1] == 0:
        raise ValueError("Not enough move variety in the selected scope after filtering.")

    standardize: bool = layout == "matches"
    off_scores: np.ndarray
    def_scores: np.ndarray
    off_ratio: float
    def_ratio: float
    off_scores, off_ratio = FirstPrincipalComponent(off_mat, standardize)
    def_scores, def_ratio = FirstPrincipalComponent(def_mat, standardize)

    cmax: float = max(1.0, float(np.max(np.abs(net_values))))
    customdata: list[list[Any]] = [
        [str(w), str(v), float(s), float(e), float(n)]
        for w, v, s, e, n in zip(wrestlers, video_names, starts, ends, net_values)
    ]
    fig: go.Figure = go.Figure(
        data=[
            go.Scatter(
                mode="markers",
                x=off_scores,
                y=def_scores,
                customdata=customdata,
                marker={
                    "color": net_values,
                    "colorscale": [
                        [0.0, "rgb(178,34,34)"],
                        [0.5, "rgb(150,150,150)"],
                        [1.0, "rgb(34,139,34)"],
                    ],
                    "cmin": -cmax,
                    "cmax": cmax,
                },
                hovertemplate=(
                    "%{customdata[0]}<br>%{customdata[1]}<br>"
                    "%{customdata[2]}–%{customdata[3]}<br>Adj net: %{customdata[4]}<extra></extra>"
                ),
            )
        ]
    )
    title: str = f"{scope} — {layout}"
    if name:
        title += f" — {name}"
    fig.update_layout(
        title=title,
        xaxis_title=f"off. ({off_ratio * 100:.1f}%)",
        yaxis_title=f"def. ({def_ratio * 100:.1f}%)",
        hovermode="closest",
        dragmode="select",
        template="plotly_white",
    )
    return {
        "fig_json": fig.to_json(),
        "off_variance": float(off_ratio),
        "def_variance": float(def_ratio),
        "n_points": int(len(off_scores)),
        "scope": scope,
        "name": name,
        "layout": layout,
    }


class PcaCompileItem(BaseModel):
    """One sequence to extract into the PCA highlight reel."""

    wrestler: str = ""
    video: str
    start_time: int
    end_time: int


class PcaCompileRequest(BaseModel):
    """Payload for POST /api/pca/compile — extract selected sequences into a reel."""

    name: str = "pca"
    items: list[PcaCompileItem] = []
    use_stream_copy: bool = _STREAM_COPY_DEFAULT


def run_pca_compile_job(ctx: JobContext, req: PcaCompileRequest) -> dict[str, Any]:
    """Extract the requested sequences and concatenate them into a PCA reel.

    Args:
        ctx: Job context for progress reporting and cancellation.
        req: The sequences to extract and the output reel name.

    Returns:
        A dict describing the result: ``{"output": name, "segments": n,
        "errors": [...]}``.

    Raises:
        ValueError: If no sequences are selected or an item's times are invalid.
        RuntimeError: If no segments were extracted or concatenation fails.
    """
    if not req.items:
        raise ValueError("No sequences selected.")
    for item in req.items:
        if item.start_time < 0 or item.start_time >= item.end_time:
            raise ValueError(
                f"Invalid sequence times for {item.video}: {item.start_time}-{item.end_time}"
            )

    work_dir: Path = tmp_dir / f"pca_gen_{ctx.job_id}"
    work_dir.mkdir(parents=True, exist_ok=True)
    segment_files: list[Path] = []
    errors: list[str] = []
    total: int = len(req.items)

    for i, item in enumerate(req.items):
        if ctx.cancelled:
            shutil.rmtree(work_dir, ignore_errors=True)
            return {"output": "", "segments": len(segment_files), "errors": ["Cancelled"]}
        ctx.report(100.0 * (i + 1) / total, f"Extracting {item.video}")
        if "/" in item.video or "\\" in item.video or item.video.startswith("."):
            errors.append(f"Invalid video name: {item.video}")
            continue
        input_path: Path = taged_dir / item.video
        if not input_path.is_file():
            errors.append(f"Source video not found: {item.video}")
            continue
        segment_path: Path = work_dir / f"segment_{i}.ts"
        codecs: dict[str, str] = GetVideoCodecs(input_path)
        cmd: list[str] = build_extract_cmd(
            input_path, item.start_time, item.end_time, segment_path,
            req.use_stream_copy, codecs,
        )
        result = run_ffmpeg(cmd, description=f"Extracting {item.video}", cancel_event=ctx.cancel_event)
        if result.success and segment_path.is_file():
            segment_files.append(segment_path)
        else:
            errors.append(f"Failed to extract {item.video}")

    if not segment_files:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError("No clips were successfully extracted.")

    ctx.report(100, "Concatenating clips...")
    output_path: Path = clips_dir / f"pca_{_sanitize(req.name)}_{ctx.job_id}.mkv"
    clips_dir.mkdir(parents=True, exist_ok=True)
    list_file: Path = work_dir / "mylist.txt"
    list_file.write_text("".join(f"file '{seg.resolve()}'\n" for seg in segment_files))
    result = run_ffmpeg(
        build_concat_cmd(list_file, output_path), description="Concatenating clips",
        cancel_event=ctx.cancel_event,
    )
    shutil.rmtree(work_dir, ignore_errors=True)
    if not result.success or not output_path.is_file():
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"Concatenation failed: {result.message}")

    logging.info(f"Created PCA reel: {output_path.name}")
    return {"output": output_path.name, "segments": len(segment_files), "errors": errors}
