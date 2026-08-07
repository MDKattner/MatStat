"""Tests for the web Compile Stats flow — the job body and API routes."""

import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.web import configs, stats


class FakeContext:
    """Minimal stand-in for scripts.web.jobs.JobContext."""

    def __init__(self, cancelled: bool = False) -> None:
        self.job_id: str = "testjob"
        self.reports: list[tuple[float, str]] = []
        self.cancelled: bool = cancelled

    def report(self, progress: float, message: str = "") -> None:
        self.reports.append((progress, message))


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
    """Write a small Wrestlers.config for the route/job to read."""
    content: str = "\n".join(names) + "\n# a comment\n"
    (tmp_path / "cfg" / "Wrestlers.config").write_text(content)


def _mock_make_name_and_csv(monkeypatch, table: dict[str, tuple[str, str]]) -> None:
    """Stub stats.MakeNameAndCSV with a filename -> (name, data) table."""

    def fake(path: Path) -> tuple[str, str]:
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
        _mock_make_name_and_csv(monkeypatch, {"match.mkv": ("Alice", "row")})
        name: str | None
        data: str
        name, data = stats._process_video_safely(Path("match.mkv"))
        assert name == "Alice"
        assert data == "row"

    def test_returns_error_on_failure(self, monkeypatch) -> None:
        def broken(path: Path) -> tuple[str, str]:
            raise OSError("boom")

        monkeypatch.setattr(stats, "MakeNameAndCSV", broken)
        name: str | None
        data: str
        name, data = stats._process_video_safely(Path("broken.mkv"))
        assert name is None
        assert "broken.mkv" in data
        assert "boom" in data


class TestRunCompileStatsJob:
    """Tests for stats.run_compile_stats_job — the job body."""

    def test_writes_csv_per_wrestler(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "a.mkv").write_bytes(b"x")
        (tmp_path / "taged" / "b.mkv").write_bytes(b"x")
        (tmp_path / "taged" / "c.mkv").write_bytes(b"x")
        _mock_make_name_and_csv(monkeypatch, {
            "a.mkv": ("Alice", "a.mkv:1,0,10,collar tie,double,nothing,T,None"),
            "b.mkv": ("Alice", "b.mkv:1,0,10,standing,single,nothing,None,None\n"
                      "b.mkv:2,10,20,front headlock,double,whizzer,T,E"),
            "c.mkv": ("Bob Smith", "c.mkv:1,0,10,regular ride,nothing,sprawl,None,T"),
        })

        result: dict[str, Any] = stats.run_compile_stats_job(
            FakeContext(), ["Alice", "Bob Smith"]
        )

        assert result["videos_processed"] == 3
        assert result["wrestlers"] == {"Alice": 3, "Bob Smith": 1}
        assert result["errors"] == []
        assert (tmp_path / "csv" / "Alice.csv").read_text().count("\n") == 2
        assert "b.mkv:2,10,20" in (tmp_path / "csv" / "Alice.csv").read_text()
        assert (tmp_path / "csv" / "Bob Smith.csv").read_text() == "c.mkv:1,0,10,regular ride,nothing,sprawl,None,T"

    def test_empty_data_skips_file(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "empty.mkv").write_bytes(b"x")
        _mock_make_name_and_csv(monkeypatch, {"empty.mkv": ("Alice", "")})

        result: dict[str, Any] = stats.run_compile_stats_job(FakeContext(), ["Alice"])

        assert result["wrestlers"] == {"Alice": 0}
        assert not (tmp_path / "csv" / "Alice.csv").exists()

    def test_unknown_wrestler_reported(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "guest.mkv").write_bytes(b"x")
        _mock_make_name_and_csv(monkeypatch, {"guest.mkv": ("Jane Doe", "row")})

        result: dict[str, Any] = stats.run_compile_stats_job(FakeContext(), ["Alice"])

        assert result["wrestlers"] == {"Alice": 0}
        assert len(result["errors"]) == 1
        assert "Jane Doe" in result["errors"][0]
        assert not (tmp_path / "csv" / "Jane Doe.csv").exists()

    def test_processing_failure_reported(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "bad.mkv").write_bytes(b"x")

        def broken(path: Path) -> tuple[str, str]:
            raise OSError("corrupt")

        monkeypatch.setattr(stats, "MakeNameAndCSV", broken)

        result: dict[str, Any] = stats.run_compile_stats_job(FakeContext(), ["Alice"])

        assert result["wrestlers"] == {"Alice": 0}
        assert len(result["errors"]) == 1
        assert "bad.mkv" in result["errors"][0]

    def test_no_videos_reports_idle(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        ctx: FakeContext = FakeContext()

        result: dict[str, Any] = stats.run_compile_stats_job(ctx, ["Alice"])

        assert result == {"videos_processed": 0, "wrestlers": {"Alice": 0}, "errors": []}
        assert ctx.reports[-1] == (100, "No tagged videos found.")

    def test_missing_dir_treated_as_no_videos(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        dirs["taged"].rmdir()

        result: dict[str, Any] = stats.run_compile_stats_job(FakeContext(), ["Alice"])

        assert result["videos_processed"] == 0

    def test_cancelled_stops_early(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "a.mkv").write_bytes(b"x")
        (tmp_path / "taged" / "b.mkv").write_bytes(b"x")
        _mock_make_name_and_csv(monkeypatch, {
            "a.mkv": ("Alice", "a.mkv:1,0,10,collar tie,double,nothing,T,None"),
            "b.mkv": ("Alice", "b.mkv:1,0,10,standing,single,nothing,None,None"),
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
            "a.mkv": ("Alice", "a.mkv:1,0,10,collar tie,double,nothing,T,None")
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
        assert "Wrestlers.config" in resp.json()["detail"]
