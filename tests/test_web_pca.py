"""Tests for the web PCA flow — move matrices, PCA math, figure building, jobs, routes."""

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.helpers import (
    COL_ORIGIN,
    COL_TEAM_MOVES,
    FirstPrincipalComponent,
    GenerateMoveMatrix,
    Roster,
)
from scripts.web import pca
from scripts.web.ffmpeg import FfmpegResult

ALICE_CSV: str = (
    '"a1.mkv:1",1,10,A,collar tie,"double:high crotch","sprawl","T","None"\n'
    '"a1.mkv:2",11,20,A,standing,"double","sprawl","T","E"\n'
    '"a2.mkv:1",21,30,A,front headlock,"double:high crotch","sprawl:whizzer","N2","None"\n'
    '"a2.mkv:2",31,40,D,regular ride,"high crotch","sprawl","None","T"'
)
BOB_CSV: str = (
    '"b1.mkv:1",1,10,A,collar tie,"double","sprawl","T","None"\n'
    '"b1.mkv:2",11,20,A,standing,"double:high crotch","sprawl","T","None"'
)


class FakeContext:
    """Minimal stand-in for scripts.web.jobs.JobContext."""

    def __init__(self) -> None:
        self.job_id: str = "testjob"
        self.cancel_event: threading.Event = threading.Event()
        self.reports: list[tuple[float, str]] = []

    def report(self, progress: float, message: str = "") -> None:
        self.reports.append((progress, message))

    @property
    def cancelled(self) -> bool:
        return False


def _redirect_dirs(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    """Point the pca module's data dirs at a temp dir."""
    dirs: dict[str, Path] = {
        "csv": tmp_path / "csv",
        "taged": tmp_path / "taged",
        "clips": tmp_path / "clips",
        "tmp": tmp_path / "tmp",
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(pca, "csv_dir", dirs["csv"])
    monkeypatch.setattr(pca, "taged_dir", dirs["taged"])
    monkeypatch.setattr(pca, "clips_dir", dirs["clips"])
    monkeypatch.setattr(pca, "tmp_dir", dirs["tmp"])
    return dirs


def _write_csvs(dirs: dict[str, Path]) -> None:
    """Write Alice (4 sequences / 2 videos) and Bob (2 sequences) CSVs."""
    (dirs["csv"] / "Alice.csv").write_text(ALICE_CSV)
    (dirs["csv"] / "Bob.csv").write_text(BOB_CSV)


def _mock_ffmpeg(monkeypatch, success: bool = True) -> None:
    """Stub pca.run_ffmpeg to write the output file (and segments) on success."""

    def fake(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
        if not success:
            return FfmpegResult(success=False, message="encode error")
        output: Path = Path(cmd[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"ok")
        return FfmpegResult(success=True, message="ok")

    monkeypatch.setattr(pca, "run_ffmpeg", fake)


def _mock_codecs(monkeypatch) -> None:
    monkeypatch.setattr(pca, "GetVideoCodecs", lambda _p: {"video": "h264", "audio": "aac"})


def _wait_for_job(client, job_id: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline: float = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        job: dict[str, Any] = resp.json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not finish in {timeout}s")


class TestGenerateMoveMatrix:
    """Tests for helpers.GenerateMoveMatrix — the move-count matrix builder."""

    def test_counts_duplicates_within_row(self) -> None:
        df: pd.DataFrame = pd.DataFrame(
            {COL_TEAM_MOVES: [["double", "double", "high crotch"]]},
            index=pd.Index(["v.mkv:1"], name=COL_ORIGIN),
        )
        matrix, moves = GenerateMoveMatrix(df, COL_TEAM_MOVES, min_occurrences=1)
        assert moves == ["double", "high crotch"]
        assert int(matrix.loc["v.mkv:1", "double"]) == 2
        assert int(matrix.loc["v.mkv:1", "high crotch"]) == 1

    def test_nothing_literal_never_a_column(self) -> None:
        df: pd.DataFrame = pd.DataFrame(
            {COL_TEAM_MOVES: [["nothing", "nothing", "nothing", "double"]]},
            index=pd.Index(["v.mkv:1"], name=COL_ORIGIN),
        )
        matrix, moves = GenerateMoveMatrix(df, COL_TEAM_MOVES, min_occurrences=1)
        assert "nothing" not in moves
        assert "nothing" not in matrix.columns
        assert moves == ["double"]

    def test_min_occurrences_filters_rare_moves(self, sample_df) -> None:
        matrix, moves = GenerateMoveMatrix(sample_df, COL_TEAM_MOVES, min_occurrences=2)
        assert moves == ["double", "sprawl"]
        assert list(matrix.columns) == ["double", "sprawl"]

    def test_default_min_occurrences_drops_all_singletons(self, sample_df) -> None:
        matrix, moves = GenerateMoveMatrix(sample_df, COL_TEAM_MOVES)
        assert moves == []
        assert matrix.shape == (6, 0)

    def test_all_rows_retained_with_zero_rows(self, sample_df) -> None:
        matrix, moves = GenerateMoveMatrix(sample_df, COL_TEAM_MOVES, min_occurrences=2)
        assert list(matrix.index) == list(sample_df.index)
        assert int(matrix.loc["vid.mkv:3", "double"]) == 0
        assert int(matrix.loc["vid.mkv:3", "sprawl"]) == 0

    def test_columns_sorted(self, sample_df) -> None:
        matrix, moves = GenerateMoveMatrix(sample_df, COL_TEAM_MOVES, min_occurrences=1)
        assert moves == sorted(moves)
        assert list(matrix.columns) == moves

    def test_group_key_counts_distinct_groups(self) -> None:
        df: pd.DataFrame = pd.DataFrame(
            {COL_TEAM_MOVES: [
                ["double"], ["double"], ["double", "high crotch"], ["high crotch"],
            ]},
            index=pd.Index(
                ["a.mkv:1", "a.mkv:2", "b.mkv:1", "b.mkv:2"], name=COL_ORIGIN
            ),
        )
        group_key: list[tuple[str, str]] = [
            ("A", "a.mkv"), ("A", "a.mkv"), ("A", "a.mkv"), ("A", "b.mkv"),
        ]
        matrix, moves = GenerateMoveMatrix(
            df, COL_TEAM_MOVES, min_occurrences=2, group_key=group_key
        )
        assert moves == ["high crotch"]  # "double": 1 distinct match; "high crotch": 2
        assert int(matrix.loc["a.mkv:1", "high crotch"]) == 0
        assert int(matrix.loc["b.mkv:1", "high crotch"]) == 1

    def test_group_key_differs_from_row_counting(self) -> None:
        df: pd.DataFrame = pd.DataFrame(
            {COL_TEAM_MOVES: [
                ["double"], ["double"], ["double", "high crotch"], ["high crotch"],
            ]},
            index=pd.Index(
                ["a.mkv:1", "a.mkv:2", "b.mkv:1", "b.mkv:2"], name=COL_ORIGIN
            ),
        )
        group_key: list[tuple[str, str]] = [
            ("A", "a.mkv"), ("A", "a.mkv"), ("A", "a.mkv"), ("A", "b.mkv"),
        ]
        _, row_moves = GenerateMoveMatrix(df, COL_TEAM_MOVES, min_occurrences=2)
        _, group_moves = GenerateMoveMatrix(
            df, COL_TEAM_MOVES, min_occurrences=2, group_key=group_key
        )
        assert row_moves == ["double", "high crotch"]
        assert group_moves == ["high crotch"]


class TestFirstPrincipalComponent:
    """Tests for helpers.FirstPrincipalComponent — SVD-based PCA scores."""

    def test_single_column_ratio_one(self) -> None:
        matrix: pd.DataFrame = pd.DataFrame({"m": [1.0, 2.0, 3.0, 4.0]})
        scores, ratio = FirstPrincipalComponent(matrix, standardize=False)
        centered: np.ndarray = matrix["m"].to_numpy() - matrix["m"].to_numpy().mean()
        assert ratio == pytest.approx(1.0)
        assert np.allclose(scores, centered) or np.allclose(scores, -centered)

    def test_standardize_changes_scores_keeps_ratio(self) -> None:
        matrix: pd.DataFrame = pd.DataFrame({"m": [1.0, 2.0, 3.0, 4.0]})
        scores_raw, ratio_raw = FirstPrincipalComponent(matrix, standardize=False)
        scores_std, ratio_std = FirstPrincipalComponent(matrix, standardize=True)
        assert ratio_std == pytest.approx(1.0)
        assert np.std(scores_std) == pytest.approx(1.0)
        assert not np.allclose(scores_raw, scores_std)

    def test_constant_matrix_returns_zeros(self) -> None:
        matrix: pd.DataFrame = pd.DataFrame({"m": [5.0, 5.0, 5.0]})
        scores, ratio = FirstPrincipalComponent(matrix, standardize=False)
        assert ratio == 0.0
        assert np.all(scores == 0.0)

    def test_two_columns_one_constant_ratio_one(self) -> None:
        matrix: pd.DataFrame = pd.DataFrame({
            "a": [1.0, 2.0, 3.0, 4.0],
            "b": [7.0, 7.0, 7.0, 7.0],
        })
        scores, ratio = FirstPrincipalComponent(matrix, standardize=False)
        assert ratio == pytest.approx(1.0)
        assert np.std(scores) > 0.0

    def test_row_normalize_compares_proportions(self) -> None:
        matrix: pd.DataFrame = pd.DataFrame({
            "a": [1.0, 0.0, 2.0, 0.0],
            "b": [0.0, 1.0, 0.0, 2.0],
        })
        scores_raw, _ = FirstPrincipalComponent(matrix, standardize=False)
        scores_norm, _ = FirstPrincipalComponent(matrix, standardize=False, row_normalize=True)
        assert scores_raw[0] != pytest.approx(scores_raw[2])
        assert scores_norm[0] == pytest.approx(scores_norm[2])
        assert scores_norm[1] == pytest.approx(scores_norm[3])

    def test_return_loadings_sign_fixed(self) -> None:
        matrix: pd.DataFrame = pd.DataFrame({
            "a": [3.0, 1.0, 4.0, 2.0],
            "b": [1.0, 3.0, 2.0, 4.0],
        })
        scores, ratio, loadings = FirstPrincipalComponent(
            matrix, standardize=False, return_loadings=True
        )
        assert loadings.shape == (2,)
        assert ratio > 0.0
        centered: np.ndarray = matrix.to_numpy() - matrix.to_numpy().mean(axis=0)
        assert np.allclose(scores, centered @ loadings)
        assert loadings[int(np.argmax(np.abs(loadings)))] >= 0.0

    def test_constant_matrix_returns_zero_loadings(self) -> None:
        matrix: pd.DataFrame = pd.DataFrame({"m": [5.0, 5.0, 5.0]})
        scores, ratio, loadings = FirstPrincipalComponent(
            matrix, standardize=False, return_loadings=True
        )
        assert ratio == 0.0
        assert np.all(scores == 0.0)
        assert np.all(loadings == 0.0)


class TestBuildPcaFigure:
    """Tests for pca.build_pca_figure — figure JSON and point counting."""

    def test_all_scope_sequences(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csvs(dirs)

        result: dict[str, Any] = pca.build_pca_figure("all", "", "sequences")
        assert result["n_points"] == 6
        assert result["scope"] == "all"
        fig: dict[str, Any] = json.loads(result["fig_json"])
        assert "data" in fig and "layout" in fig

    def test_all_scope_matches(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csvs(dirs)

        result: dict[str, Any] = pca.build_pca_figure("all", "", "matches")
        assert result["n_points"] == 3  # 2 Alice matches + 1 Bob match

    def test_wrestler_scope(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csvs(dirs)

        result: dict[str, Any] = pca.build_pca_figure("wrestler", "Alice", "sequences")
        assert result["n_points"] == 4

    def test_team_scope(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csvs(dirs)
        monkeypatch.setattr(
            pca, "load_roster",
            lambda: Roster(wrestlers=["Alice", "Bob"], teams={"Varsity": ["Alice"]}),
        )

        result: dict[str, Any] = pca.build_pca_figure("team", "Varsity", "sequences")
        assert result["n_points"] == 4

    def test_empty_data_raises(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        with pytest.raises(ValueError, match="Run Compile Stats"):
            pca.build_pca_figure("all", "", "sequences")

    def test_only_single_use_moves_raises(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["csv"] / "Alice.csv").write_text(
            '"x.mkv:1",1,10,A,collar tie,"double","sprawl","T","None"\n'
            '"x.mkv:2",11,20,A,standing,"single","whizzer","None","T"'
        )

        with pytest.raises(ValueError, match="Not enough move variety"):
            pca.build_pca_figure("all", "", "sequences")


class TestRunPcaCompileJob:
    """Tests for pca.run_pca_compile_job — the reel-building job body."""

    def test_creates_reel(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["taged"] / "v.mkv").write_bytes(b"video")
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        req: pca.PcaCompileRequest = pca.PcaCompileRequest(
            name="demo",
            items=[
                pca.PcaCompileItem(video="v.mkv", start_time=1, end_time=10),
                pca.PcaCompileItem(video="v.mkv", start_time=11, end_time=20),
            ],
        )
        result: dict[str, Any] = pca.run_pca_compile_job(FakeContext(), req)

        assert result["segments"] == 2
        assert result["errors"] == []
        assert result["output"].startswith("pca_demo_")
        assert (dirs["clips"] / result["output"]).read_bytes() == b"ok"

    def test_empty_items_raise(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        with pytest.raises(ValueError, match="No sequences selected"):
            pca.run_pca_compile_job(
                FakeContext(), pca.PcaCompileRequest(items=[])
            )

    def test_missing_source_video_records_error(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["taged"] / "v.mkv").write_bytes(b"video")
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        req: pca.PcaCompileRequest = pca.PcaCompileRequest(
            items=[
                pca.PcaCompileItem(video="missing.mkv", start_time=1, end_time=10),
                pca.PcaCompileItem(video="v.mkv", start_time=11, end_time=20),
            ],
        )
        result: dict[str, Any] = pca.run_pca_compile_job(FakeContext(), req)

        assert result["segments"] == 1
        assert len(result["errors"]) == 1
        assert "Source video not found: missing.mkv" in result["errors"][0]

    def test_invalid_video_name_records_error(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["taged"] / "v.mkv").write_bytes(b"video")
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        req: pca.PcaCompileRequest = pca.PcaCompileRequest(
            items=[
                pca.PcaCompileItem(video="a/b.mkv", start_time=1, end_time=10),
                pca.PcaCompileItem(video="v.mkv", start_time=11, end_time=20),
            ],
        )
        result: dict[str, Any] = pca.run_pca_compile_job(FakeContext(), req)

        assert result["segments"] == 1
        assert "Invalid video name: a/b.mkv" in result["errors"][0]

    def test_all_missing_raises(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        req: pca.PcaCompileRequest = pca.PcaCompileRequest(
            items=[
                pca.PcaCompileItem(video="gone.mkv", start_time=1, end_time=10),
                pca.PcaCompileItem(video="away.mkv", start_time=11, end_time=20),
            ],
        )
        with pytest.raises(RuntimeError, match="No clips were successfully extracted"):
            pca.run_pca_compile_job(FakeContext(), req)


class TestPcaRoutes:
    """Tests for the /api/pca figure and compile routes."""

    def test_figure_returns_json(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csvs(dirs)

        resp = client.get("/api/pca?scope=all&layout=matches")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["n_points"] == 3
        fig: dict[str, Any] = json.loads(body["fig_json"])
        assert "data" in fig and "layout" in fig

    def test_invalid_scope_400(self, client, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        resp = client.get("/api/pca?scope=bogus")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid scope"

    def test_wrestler_scope_requires_name_400(self, client, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        resp = client.get("/api/pca?scope=wrestler")
        assert resp.status_code == 400
        assert "Name is required" in resp.json()["detail"]

    def test_empty_data_400(self, client, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        resp = client.get("/api/pca?scope=all&layout=sequences")
        assert resp.status_code == 400
        assert "Run Compile Stats" in resp.json()["detail"]

    def test_compile_job_completes(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["taged"] / "v.mkv").write_bytes(b"video")
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        payload: dict[str, Any] = {
            "name": "demo",
            "items": [
                {"wrestler": "Alice", "video": "v.mkv", "start_time": 1, "end_time": 10},
                {"wrestler": "Alice", "video": "v.mkv", "start_time": 11, "end_time": 20},
            ],
        }
        resp = client.post("/api/pca/compile", json=payload)
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["status"] == "queued"

        job: dict[str, Any] = _wait_for_job(client, body["job_id"])
        assert job["status"] == "done"
        assert job["result"]["segments"] == 2
        assert job["result"]["output"].startswith("pca_demo_")
        assert (dirs["clips"] / job["result"]["output"]).read_bytes() == b"ok"

    def test_compile_empty_items_400(self, client) -> None:
        resp = client.post("/api/pca/compile", json={"items": []})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "No sequences selected"

    def test_compile_invalid_video_400(self, client) -> None:
        payload: dict[str, Any] = {
            "items": [{"video": "a/b.mkv", "start_time": 1, "end_time": 10}],
        }
        resp = client.post("/api/pca/compile", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid video name"

    def test_compile_invalid_times_400(self, client) -> None:
        payload: dict[str, Any] = {
            "items": [{"video": "v.mkv", "start_time": 10, "end_time": 5}],
        }
        resp = client.post("/api/pca/compile", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid sequence times"
