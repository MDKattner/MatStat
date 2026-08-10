"""Tests for the web Trends flow — the per-match trend figure and API route."""

import base64
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.web import trend

ALICE_CSV: str = (
    '"m1.mkv:1",0,6,A,collar tie,"double","sprawl","T","None",3,3,W,2026-08-01\n'
    '"m1.mkv:2",6,12,A,standing,"single","whizzer","N2","E",1,1,W,2026-08-01\n'
    '"m2.mkv:1",0,6,D,front headlock,"sprawl","double","E","T",-2,-2,L,2026-08-15\n'
    '"m3.mkv:1",0,6,A,collar tie,"double","sprawl","T","E",2,2,W,2026-08-22\n'
    '"m3.mkv:2",6,12,A,standing,"single","whizzer","PIN","None",0,13,W,2026-08-22\n'
    '"m4.mkv:1",0,6,A,collar tie,"double","sprawl","None","None",0,0,,\n'
)


def _redirect_dirs(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    """Point the trend module's data dir at a temp dir."""
    dirs: dict[str, Path] = {"csv": tmp_path / "csv"}
    dirs["csv"].mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(trend, "csv_dir", dirs["csv"])
    return dirs


def _write_alice(dirs: dict[str, Path]) -> None:
    (dirs["csv"] / "Alice.csv").write_text(ALICE_CSV)


def _decode_array(value: Any) -> list[Any]:
    """Decode a plotly typed-array spec ({dtype, bdata}) into a Python list."""
    if isinstance(value, dict) and "bdata" in value:
        arr: np.ndarray = np.frombuffer(
            base64.b64decode(value["bdata"]), dtype=np.dtype(value["dtype"])
        )
        if value.get("shape"):
            shape: tuple[int, ...] = tuple(
                int(x) for x in str(value["shape"]).replace(" ", "").split(",")
            )
            arr = arr.reshape(shape)
        return arr.astype(float).tolist()
    return list(value)


def _trace_ys(result: dict[str, Any]) -> tuple[list[float], list[float], list[float]]:
    """Extract the three traces' y arrays (points, cumulative, rolling)."""
    fig: dict[str, Any] = json.loads(result["fig_json"])
    traces: list[Any] = fig["data"]
    assert len(traces) == 3
    return (
        _decode_array(traces[0]["y"]),
        _decode_array(traces[1]["y"]),
        _decode_array(traces[2]["y"]),
    )


class TestBuildTrendFigure:
    """Tests for trend.build_trend_figure — the figure builder."""

    def test_net_points_ordered_by_date(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_alice(dirs)

        result: dict[str, Any] = trend.build_trend_figure("Alice", "net", 5)

        assert result["wrestler"] == "Alice"
        assert result["metric"] == "net"
        assert result["window"] == 5
        assert result["n_matches"] == 3
        assert result["skipped_rows"] == 1
        points, cumulative, rolling = _trace_ys(result)
        assert points == [4.0, -2.0, 2.0]
        assert cumulative == [4.0, 1.0, pytest.approx(4.0 / 3.0)]
        assert rolling == [4.0, 1.0, pytest.approx(4.0 / 3.0)]

    def test_adjusted_metric_includes_pin_bonus(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_alice(dirs)

        result: dict[str, Any] = trend.build_trend_figure("Alice", "adjusted", 5)

        assert result["metric"] == "adjusted"
        points, cumulative, rolling = _trace_ys(result)
        assert points == [4.0, -2.0, 15.0]
        assert cumulative == [4.0, 1.0, pytest.approx(17.0 / 3.0)]

    def test_rolling_window_honored(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_alice(dirs)

        result: dict[str, Any] = trend.build_trend_figure("Alice", "net", 2)

        assert result["window"] == 2
        points, cumulative, rolling = _trace_ys(result)
        assert points == [4.0, -2.0, 2.0]
        assert rolling == [4.0, 1.0, 0.0]

    def test_customdata_carries_date_video_points(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_alice(dirs)

        result: dict[str, Any] = trend.build_trend_figure("Alice", "net", 5)
        fig: dict[str, Any] = json.loads(result["fig_json"])
        customdata: list[Any] = fig["data"][0]["customdata"]
        assert customdata[0] == ["2026-08-01", "m1.mkv", 4.0]
        assert customdata[1] == ["2026-08-15", "m2.mkv", -2.0]
        assert customdata[2] == ["2026-08-22", "m3.mkv", 2.0]

    def test_missing_wrestler_raises(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        with pytest.raises(ValueError, match="No compiled data"):
            trend.build_trend_figure("Ghost", "net", 5)

    def test_legacy_csv_without_dates_raises(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["csv"] / "Alice.csv").write_text(
            '"m1.mkv:1",0,6,A,collar tie,"double","sprawl","T","None",3,3,W\n'
        )

        with pytest.raises(ValueError, match="match dates"):
            trend.build_trend_figure("Alice", "net", 5)

    def test_no_dated_rows_raises(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["csv"] / "Alice.csv").write_text(
            '"m1.mkv:1",0,6,A,collar tie,"double","sprawl","T","None",3,3,W,\n'
        )

        with pytest.raises(ValueError, match="recorded date"):
            trend.build_trend_figure("Alice", "net", 5)

    def test_bad_window_raises(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_alice(dirs)

        with pytest.raises(ValueError, match="window"):
            trend.build_trend_figure("Alice", "net", 0)


class TestTrendRoutes:
    """Tests for GET /api/trend — the figure route."""

    def test_figure_returns_json(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_alice(dirs)

        resp = client.get("/api/trend?wrestler=Alice&metric=net&window=5")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["n_matches"] == 3
        assert body["skipped_rows"] == 1
        fig: dict[str, Any] = json.loads(body["fig_json"])
        assert "data" in fig and "layout" in fig

    def test_missing_wrestler_400(self, client) -> None:
        resp = client.get("/api/trend")
        assert resp.status_code == 400
        assert "Wrestler is required" in resp.json()["detail"]

    def test_invalid_metric_400(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_alice(dirs)

        resp = client.get("/api/trend?wrestler=Alice&metric=points")
        assert resp.status_code == 400
        assert "Invalid metric" in resp.json()["detail"]

    def test_invalid_window_400(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_alice(dirs)

        resp = client.get("/api/trend?wrestler=Alice&window=0")
        assert resp.status_code == 400
        assert "Window" in resp.json()["detail"]

    def test_unknown_wrestler_400(self, client, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        resp = client.get("/api/trend?wrestler=Ghost")
        assert resp.status_code == 400
        assert "No compiled data" in resp.json()["detail"]
