"""Tests for the web Compile Stats flow — the job body and API routes."""

import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

sys.path.append(str(Path(__file__).parent.parent))

import scripts.helpers as helpers
from scripts.helpers import MatchOutcomeConflictError
from scripts.web import configs, stats

from conftest import FakeContext


class FlipContext:
    """JobContext that requests cancellation after the first progress report."""

    def __init__(self) -> None:
        self.job_id: str = "flipjob"
        self.reports: list[tuple[float, str]] = []
        self.cancelled: bool = False

    def report(self, progress: float, message: str = "") -> None:
        self.reports.append((progress, message))
        self.cancelled = True


def _redirect_dirs(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    """Point the stats/config modules' data dirs at a temp dir."""
    dirs: dict[str, Path] = {
        "taged": tmp_path / "taged",
        "csv": tmp_path / "csv",
        "cfg": tmp_path / "cfg",
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(stats, "taged_dir", dirs["taged"])
    monkeypatch.setattr(stats, "csv_dir", dirs["csv"])
    monkeypatch.setattr(configs, "cfg_dir", dirs["cfg"])
    return dirs


def _write_config(tmp_path: Path, names: list[str] = ("Alice", "Bob Smith")) -> None:
    """Write a small Wrestlers.json for the route/job to read."""
    (tmp_path / "cfg" / "Wrestlers.json").write_text(
        json.dumps({"wrestlers": list(names), "teams": {}})
    )


def _mock_make_name_and_csv(
    monkeypatch, table: dict[str, tuple[str, str, str]]
) -> None:
    """Stub stats.MakeNameAndCSV with a filename -> (name, match_date, data) table."""

    def fake(path: Path) -> tuple[str, str, str]:
        key: str = Path(path).name
        if key not in table:
            raise RuntimeError(f"no entry for {key}")
        return table[key]

    monkeypatch.setattr(stats, "MakeNameAndCSV", fake)


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


class TestProcessVideoSafely:
    """Tests for stats._process_video_safely — the per-video wrapper."""

    def test_returns_name_and_data(self, monkeypatch) -> None:
        _mock_make_name_and_csv(monkeypatch, {"match.mkv": ("Alice", "2026-08-01", "row")})
        name: str | None
        match_date: str
        data: str
        name, match_date, data = stats._process_video_safely(Path("match.mkv"))
        assert name == "Alice"
        assert match_date == "2026-08-01"
        assert data == "row"

    def test_returns_error_on_failure(self, monkeypatch) -> None:
        def broken(path: Path) -> tuple[str, str, str]:
            raise OSError("boom")

        monkeypatch.setattr(stats, "MakeNameAndCSV", broken)
        name: str | None
        match_date: str
        data: str
        name, match_date, data = stats._process_video_safely(Path("broken.mkv"))
        assert name is None
        assert match_date == ""
        assert "broken.mkv" in data
        assert "boom" in data


class TestRulesetStatus:
    """Tests for the manifest/active ruleset comparison."""

    def test_missing_manifest_is_not_stale(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        status: dict[str, Any] = stats.ruleset_status()

        assert status["stale"] is False
        assert status["compiled_ruleset"] == ""
        assert status["active_ruleset"]

    def test_hash_mismatch_sets_stale_without_mutating_points(
        self, tmp_path, monkeypatch
    ) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        monkeypatch.setattr(stats, "LoadAppConfig", lambda: helpers._DefaultAppConfig())
        (dirs["csv"] / "manifest.json").write_text(json.dumps({
            "ruleset": "OldStyle",
            "ruleset_hash": "deadbeef",
            "generated_at": "2026-01-01T00:00:00+00:00",
        }))
        csv_file: Path = dirs["csv"] / "Alice.csv"
        csv_file.write_text(
            'a.mkv:1,0,6,A,collar tie,double,nothing,T,None,99,123,W\n'
        )

        status: dict[str, Any] = stats.ruleset_status()
        df = helpers.MakeFormattedDataFrame(csv_file)

        assert status["stale"] is True
        assert status["compiled_ruleset"] == "OldStyle"
        assert status["active_ruleset"] == "Folkstyle"
        assert df[helpers.COL_NET_POINTS].iloc[0] == 99
        assert df[helpers.COL_ADJUSTED_NET_POINTS].iloc[0] == 123


class TestRunCompileStatsJob:
    """Tests for stats.run_compile_stats_job — the job body."""

    def test_writes_csv_per_wrestler(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "a.mkv").write_bytes(b"x")
        (tmp_path / "taged" / "b.mkv").write_bytes(b"x")
        (tmp_path / "taged" / "c.mkv").write_bytes(b"x")
        _mock_make_name_and_csv(monkeypatch, {
            "a.mkv": ("Alice", "2026-08-01",
                      "a.mkv:1,0,10,A,collar tie,double,nothing,T,None"),
            "b.mkv": ("Alice", "",
                      "b.mkv:1,0,10,A,standing,single,nothing,None,None\n"
                      "b.mkv:2,10,20,D,front headlock,double,whizzer,T,E"),
            "c.mkv": ("Bob Smith", "",
                      "c.mkv:1,0,10,A,regular ride,nothing,sprawl,None,T"),
        })

        result: dict[str, Any] = stats.run_compile_stats_job(
            FakeContext(), ["Alice", "Bob Smith"]
        )

        assert result["videos_processed"] == 3
        assert result["wrestlers"] == {"Alice": 3, "Bob Smith": 1}
        assert result["errors"] == []
        assert (tmp_path / "csv" / "Alice.csv").read_text().count("\n") == 2
        assert "b.mkv:2,10,20" in (tmp_path / "csv" / "Alice.csv").read_text()
        assert "a.mkv:1,0,10,A,collar tie,double,nothing,T,None,3,3,,2026-08-01" in (
            (tmp_path / "csv" / "Alice.csv").read_text()
        )
        assert (tmp_path / "csv" / "Bob Smith.csv").read_text() == (
            "c.mkv:1,0,10,A,regular ride,nothing,sprawl,None,T,-3,-3,,"
        )

    def test_dual_mode_swaps_into_opponent_bucket(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "d.mkv").write_bytes(b"x")
        _mock_make_name_and_csv(monkeypatch, {
            "d.mkv": ("Alice (W) / Bob Smith", "2026-08-01",
                      "d.mkv:1,0,10,A,collar tie:underhook,double,nothing,T,None"),
        })

        result: dict[str, Any] = stats.run_compile_stats_job(
            FakeContext(), ["Alice", "Bob Smith"]
        )

        assert result["errors"] == []
        assert result["wrestlers"] == {"Alice": 1, "Bob Smith": 1}
        assert (tmp_path / "csv" / "Alice.csv").read_text() == (
            "d.mkv:1,0,10,A,collar tie:underhook,double,nothing,T,None,3,3,W,2026-08-01"
        )
        assert (tmp_path / "csv" / "Bob Smith.csv").read_text() == (
            "d.mkv:1,0,10,D,underhook:collar tie,nothing,double,None,T,-3,-3,L,2026-08-01"
        )

    def test_dual_mode_opponent_not_in_roster_reports_error(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "e.mkv").write_bytes(b"x")
        _mock_make_name_and_csv(monkeypatch, {
            "e.mkv": ("Alice / Jane Doe", "",
                      "e.mkv:1,0,10,A,collar tie:underhook,double,nothing,T,None"),
        })

        result: dict[str, Any] = stats.run_compile_stats_job(FakeContext(), ["Alice"])

        assert result["wrestlers"] == {"Alice": 1}
        assert result["errors"] == []
        assert len(result["warnings"]) == 1
        assert "Jane Doe" in result["warnings"][0]
        assert (tmp_path / "csv" / "Alice.csv").is_file()
        assert not (tmp_path / "csv" / "Jane Doe.csv").exists()

    def test_empty_data_skips_file(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "empty.mkv").write_bytes(b"x")
        _mock_make_name_and_csv(monkeypatch, {"empty.mkv": ("Alice", "", "")})

        result: dict[str, Any] = stats.run_compile_stats_job(FakeContext(), ["Alice"])

        assert result["wrestlers"] == {"Alice": 0}
        assert not (tmp_path / "csv" / "Alice.csv").exists()

    def test_unknown_wrestler_preserves_existing_csv(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "guest.mkv").write_bytes(b"x")
        stale: Path = tmp_path / "csv" / "Jane Doe.csv"
        stale.write_text("old-data")
        _mock_make_name_and_csv(monkeypatch, {"guest.mkv": ("Jane Doe", "", "row")})

        result: dict[str, Any] = stats.run_compile_stats_job(FakeContext(), ["Alice"])

        assert result["wrestlers"] == {"Alice": 0}
        assert result["errors"] == []
        assert "Jane Doe" in result["wrestlers_stale"]
        assert "Jane Doe" in result["warnings"][0]
        assert stale.read_text() == "old-data"
        manifest = json.loads((tmp_path / "csv" / "manifest.json").read_text())
        assert manifest["wrestlers"]["Jane Doe"]["stale"] is True

    def test_processing_failure_aborts_without_writing(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "bad.mkv").write_bytes(b"x")
        existing: Path = tmp_path / "csv" / "Alice.csv"
        existing.write_text("old-data")

        def broken(path: Path) -> tuple[str, str, str]:
            raise OSError("corrupt")

        monkeypatch.setattr(stats, "MakeNameAndCSV", broken)

        with pytest.raises(RuntimeError, match="bad.mkv"):
            stats.run_compile_stats_job(FakeContext(), ["Alice"])

        assert existing.read_text() == "old-data"
        assert not (tmp_path / "csv" / "manifest.json").exists()

    def test_no_videos_clears_stale_csvs(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        stale: Path = tmp_path / "csv" / "Alice.csv"
        stale.write_text("old-data")
        ctx: FakeContext = FakeContext()

        result: dict[str, Any] = stats.run_compile_stats_job(ctx, ["Alice"])

        assert result["videos_processed"] == 0
        assert result["wrestlers"] == {"Alice": 0}
        assert result["errors"] == []
        assert not stale.exists()
        manifest = json.loads((tmp_path / "csv" / "manifest.json").read_text())
        assert manifest["wrestlers"]["Alice"]["rows"] == 0
        assert ctx.reports[-1] == (100, "Compiled 0 videos.")

    def test_missing_dir_is_fatal(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        existing: Path = dirs["csv"] / "Alice.csv"
        existing.write_text("old-data")
        dirs["taged"].rmdir()

        with pytest.raises(FileNotFoundError, match="Tagged video directory"):
            stats.run_compile_stats_job(FakeContext(), ["Alice"])

        assert existing.read_text() == "old-data"
        assert not (tmp_path / "csv" / "manifest.json").exists()

    def test_manifest_records_sources_and_ruleset(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "a.mkv").write_bytes(b"x")
        _mock_make_name_and_csv(monkeypatch, {
            "a.mkv": ("Alice", "2026-08-01",
                      "a.mkv:1,0,10,A,collar tie,double,nothing,T,None"),
        })

        result: dict[str, Any] = stats.run_compile_stats_job(FakeContext(), ["Alice"])

        assert result["manifest"] == "manifest.json"
        manifest = json.loads((tmp_path / "csv" / "manifest.json").read_text())
        assert manifest["schema_version"] == 1
        assert manifest["ruleset"]
        assert len(manifest["ruleset_hash"]) == 64
        assert manifest["sources"][0]["file"] == "a.mkv"
        assert manifest["sources"][0]["status"] == "ok"
        assert manifest["wrestlers"]["Alice"]["rows"] == 1

    def test_atomic_replace_failure_preserves_csv(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "a.mkv").write_bytes(b"x")
        existing: Path = tmp_path / "csv" / "Alice.csv"
        existing.write_text("old-data")
        _mock_make_name_and_csv(monkeypatch, {
            "a.mkv": ("Alice", "", "a.mkv:1,0,10,A,collar tie,double,nothing,T,None"),
        })

        def broken_replace(src, dst) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(stats.os, "replace", broken_replace)

        result: dict[str, Any] = stats.run_compile_stats_job(FakeContext(), ["Alice"])

        assert existing.read_text() == "old-data"
        assert any("Alice.csv" in error for error in result["errors"])

    def test_conflicting_existing_csv_blocks_commit(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "a.mkv").write_bytes(b"x")
        conflicting: Path = tmp_path / "csv" / "Alice.csv"
        conflicting.write_text(
            '"a.mkv:1",0,6,A,collar tie,"double","sprawl","T","E",2,2,W\n'
            '"a.mkv:2",6,12,D,standing,"sprawl","sweep single","E","T",-2,-2,L'
        )
        _mock_make_name_and_csv(monkeypatch, {
            "a.mkv": ("Alice", "", "a.mkv:1,0,10,A,collar tie,double,nothing,T,None"),
        })

        with pytest.raises(MatchOutcomeConflictError, match="a.mkv"):
            stats.run_compile_stats_job(FakeContext(), ["Alice"])

        assert "a.mkv:1" in conflicting.read_text()
        assert not (tmp_path / "csv" / "manifest.json").exists()

    def test_force_overrides_conflict_and_warns(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "a.mkv").write_bytes(b"x")
        conflicting: Path = tmp_path / "csv" / "Alice.csv"
        conflicting.write_text(
            '"a.mkv:1",0,6,A,collar tie,"double","sprawl","T","E",2,2,W\n'
            '"a.mkv:2",6,12,D,standing,"sprawl","sweep single","E","T",-2,-2,L'
        )
        _mock_make_name_and_csv(monkeypatch, {
            "a.mkv": ("Alice", "", "a.mkv:1,0,10,A,collar tie,double,nothing,T,None"),
        })

        result: dict[str, Any] = stats.run_compile_stats_job(
            FakeContext(), ["Alice"], force=True
        )

        assert result["warnings"]
        assert any("force" in warning.lower() for warning in result["warnings"])
        assert conflicting.read_text().startswith("a.mkv:1,0,10,A")
        assert (tmp_path / "csv" / "manifest.json").is_file()

    def test_cancelled_stops_early(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "a.mkv").write_bytes(b"x")
        (tmp_path / "taged" / "b.mkv").write_bytes(b"x")
        _mock_make_name_and_csv(monkeypatch, {
            "a.mkv": ("Alice", "", "a.mkv:1,0,10,A,collar tie,double,nothing,T,None"),
            "b.mkv": ("Alice", "", "b.mkv:1,0,10,A,standing,single,nothing,None,None"),
        })

        result: dict[str, Any] = stats.run_compile_stats_job(FlipContext(), ["Alice"])

        assert result["videos_processed"] == 1
        assert not (tmp_path / "csv" / "Alice.csv").exists()


class TestCompileStatsRoute:
    """Tests for POST /api/compile-stats — queuing and completing the job."""

    def test_job_completes(self, client, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _write_config(tmp_path)
        (tmp_path / "taged" / "a.mkv").write_bytes(b"x")
        _mock_make_name_and_csv(monkeypatch, {
            "a.mkv": ("Alice", "2026-08-01",
                      "a.mkv:1,0,10,A,collar tie,double,nothing,T,None")
        })

        resp = client.post("/api/compile-stats")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["status"] == "queued"
        job_id: str = body["job_id"]

        job: dict[str, Any] = _wait_for_job(client, job_id)
        assert job["status"] == "done"
        assert job["result"]["videos_processed"] == 1
        assert job["result"]["wrestlers"] == {"Alice": 1, "Bob Smith": 0}
        assert job["result"]["errors"] == []
        assert (tmp_path / "csv" / "Alice.csv").exists()

    def test_missing_config_404(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        dirs["cfg"].rmdir()

        resp = client.post("/api/compile-stats")
        assert resp.status_code == 404
        assert "Wrestlers.json" in resp.json()["detail"]


class TestEnrichCsvRows:
    """Tests for stats._enrich_csv_rows — appending net/adjusted/result columns."""

    def test_appends_net_and_adjusted(self, monkeypatch) -> None:
        monkeypatch.setattr(stats, "ReloadScoringMap", lambda: None)
        monkeypatch.setattr(helpers, "score_to_pts", {"T": np.int16(3), "None": np.int16(0)})
        monkeypatch.setattr(helpers, "_ACTIVE_PIN_CODES", frozenset({"PIN"}))
        monkeypatch.setattr(helpers, "active_pin_points", 13)
        rows: list[str] = ["fake.mkv:1,0,6,A,collar tie,double,nothing,T,None"]
        assert stats._enrich_csv_rows(rows) == [
            "fake.mkv:1,0,6,A,collar tie,double,nothing,T,None,3,3,,"
        ]

    def test_appends_match_result(self, monkeypatch) -> None:
        monkeypatch.setattr(stats, "ReloadScoringMap", lambda: None)
        monkeypatch.setattr(helpers, "score_to_pts", {"T": np.int16(3), "None": np.int16(0)})
        monkeypatch.setattr(helpers, "_ACTIVE_PIN_CODES", frozenset({"PIN"}))
        monkeypatch.setattr(helpers, "active_pin_points", 13)
        rows: list[str] = ["fake.mkv:1,0,6,A,collar tie,double,nothing,T,None"]
        assert stats._enrich_csv_rows(rows, "L") == [
            "fake.mkv:1,0,6,A,collar tie,double,nothing,T,None,3,3,L,"
        ]

    def test_appends_match_date(self, monkeypatch) -> None:
        monkeypatch.setattr(stats, "ReloadScoringMap", lambda: None)
        monkeypatch.setattr(helpers, "score_to_pts", {"T": np.int16(3), "None": np.int16(0)})
        monkeypatch.setattr(helpers, "_ACTIVE_PIN_CODES", frozenset({"PIN"}))
        monkeypatch.setattr(helpers, "active_pin_points", 13)
        rows: list[str] = ["fake.mkv:1,0,6,A,collar tie,double,nothing,T,None"]
        assert stats._enrich_csv_rows(rows, "", "2026-08-01") == [
            "fake.mkv:1,0,6,A,collar tie,double,nothing,T,None,3,3,,2026-08-01"
        ]

    def test_pin_bonus_applied(self, monkeypatch) -> None:
        monkeypatch.setattr(stats, "ReloadScoringMap", lambda: None)
        monkeypatch.setattr(helpers, "score_to_pts", {"PIN": np.int16(0), "None": np.int16(0)})
        monkeypatch.setattr(helpers, "_ACTIVE_PIN_CODES", frozenset({"PIN"}))
        monkeypatch.setattr(helpers, "active_pin_points", 13)
        rows: list[str] = ["fake.mkv:2,6,12,D,standing,sprawl,single,PIN,None"]
        assert stats._enrich_csv_rows(rows) == [
            "fake.mkv:2,6,12,D,standing,sprawl,single,PIN,None,0,13,,"
        ]

    def test_malformed_row_passes_through(self, monkeypatch) -> None:
        rows: list[str] = ["not,a,valid,row"]
        assert stats._enrich_csv_rows(rows) == rows
