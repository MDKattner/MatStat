"""Tests for the web Auditing flow — the audit listing, retag job, and routes."""

import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.web import app as web_app
from scripts.web import audit
from scripts.web import configs
from scripts.web import stats
from scripts.web.ffmpeg import FfmpegResult
from scripts.web.tag import TagSequence

CSV_DATA: str = (
    'match.mkv:1,0,6,A,collar tie,double:high crotch,sprawl,T,N2\n'
    'match.mkv:2,6,12,D,standing,sprawl,sweep single,E,T'
)


class FakeContext:
    """Minimal stand-in for scripts.web.jobs.JobContext."""

    def __init__(self) -> None:
        self.job_id: str = "testjob"
        self.reports: list[tuple[float, str]] = []

    def report(self, progress: float, message: str = "") -> None:
        self.reports.append((progress, message))

    @property
    def cancelled(self) -> bool:
        return False


def _redirect_dirs(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    """Point the audit/stats/config modules' data dirs at a temp dir."""
    dirs: dict[str, Path] = {
        "taged": tmp_path / "taged",
        "tmp": tmp_path / "tmp",
        "csv": tmp_path / "csv",
        "cfg": tmp_path / "cfg",
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(audit, "taged_dir", dirs["taged"])
    monkeypatch.setattr(audit, "tmp_dir", dirs["tmp"])
    monkeypatch.setattr(stats, "taged_dir", dirs["taged"])
    monkeypatch.setattr(stats, "csv_dir", dirs["csv"])
    monkeypatch.setattr(configs, "cfg_dir", dirs["cfg"])
    monkeypatch.setattr(web_app, "taged_dir", dirs["taged"])
    monkeypatch.setattr(web_app, "csv_dir", dirs["csv"])
    return dirs


def _mock_make_name_and_csv(monkeypatch, table: dict[str, tuple[str, str]]) -> None:
    """Stub audit.MakeNameAndCSV with a filename -> (name, data) table."""

    def fake(path: Path) -> tuple[str, str]:
        key: str = Path(path).name
        if key not in table:
            raise RuntimeError(f"no entry for {key}")
        return table[key]

    monkeypatch.setattr(audit, "MakeNameAndCSV", fake)


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


class TestListTaggedVideos:
    """Tests for audit.list_tagged_videos — the audit listing."""

    def test_lists_videos_with_sequences(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "match.mkv").write_bytes(b"old")
        _mock_make_name_and_csv(monkeypatch, {"match.mkv": ("Alice", CSV_DATA)})

        videos: list[dict[str, Any]] = audit.list_tagged_videos()

        assert len(videos) == 1
        assert videos[0]["name"] == "match.mkv"
        assert videos[0]["wrestler"] == "Alice"
        assert videos[0]["opponent"] == ""
        assert videos[0]["result"] == ""
        assert videos[0]["sequences"] == [
            {
                "start_time": 0,
                "end_time": 6,
                "attack_defend": True,
                "tie_up": "collar tie",
                "opp_tie": "",
                "team_moves": ["double", "high crotch"],
                "op_moves": ["sprawl"],
                "team_scores": ["T"],
                "op_scores": ["N2"],
            },
            {
                "start_time": 6,
                "end_time": 12,
                "attack_defend": False,
                "tie_up": "standing",
                "opp_tie": "",
                "team_moves": ["sprawl"],
                "op_moves": ["sweep single"],
                "team_scores": ["E"],
                "op_scores": ["T"],
            },
        ]

    def test_lists_dual_mode_video_with_parsed_fields(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "dual.mkv").write_bytes(b"x")
        dual_data: str = (
            'dual.mkv:1,0,6,A,collar tie:underhook,double,sprawl,T,None\n'
            'dual.mkv:2,6,12,D,standing:front headlock,sprawl,single,None,E'
        )
        _mock_make_name_and_csv(
            monkeypatch, {"dual.mkv": ("Alice (W) / Bob Smith", dual_data)}
        )

        videos: list[dict[str, Any]] = audit.list_tagged_videos()

        assert len(videos) == 1
        assert videos[0]["wrestler"] == "Alice"
        assert videos[0]["opponent"] == "Bob Smith"
        assert videos[0]["result"] == "W"
        assert videos[0]["sequences"][0]["tie_up"] == "collar tie"
        assert videos[0]["sequences"][0]["opp_tie"] == "underhook"
        assert videos[0]["sequences"][1]["tie_up"] == "standing"
        assert videos[0]["sequences"][1]["opp_tie"] == "front headlock"

    def test_skips_hidden_files(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / ".hidden").write_bytes(b"x")

        def unexpected(path: Path) -> tuple[str, str]:
            raise AssertionError(f"Should not probe {path}")

        monkeypatch.setattr(audit, "MakeNameAndCSV", unexpected)
        assert audit.list_tagged_videos() == []

    def test_missing_dir_returns_empty(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(audit, "taged_dir", tmp_path / "nope")
        assert audit.list_tagged_videos() == []

    def test_skips_malformed_chapter_rows(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "bad.mkv").write_bytes(b"x")
        _mock_make_name_and_csv(
            monkeypatch,
            {"bad.mkv": ("Alice", "bad.mkv:1,0,0,A,tie,double,sprawl,T,E")},
        )

        videos: list[dict[str, Any]] = audit.list_tagged_videos()
        assert videos[0]["name"] == "bad.mkv"
        assert videos[0]["sequences"] == []


class TestRunRetagJob:
    """Tests for audit.run_retag_job — the re-tag + recompile job body."""

    def test_retag_replaces_metadata_and_recompiles(self, tmp_path, monkeypatch) -> None:
        dirs = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["taged"] / "match.mkv").write_bytes(b"old")
        monkeypatch.setattr(audit, "GetVidDuration", lambda p: 60)

        captured: dict[str, str] = {}

        def fake_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            captured["metadata"] = Path(cmd[4]).read_text()
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"new")
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(audit, "run_ffmpeg", fake_ffmpeg)
        monkeypatch.setattr(audit.configs, "load_wrestler_names", lambda: ["Alice"])
        monkeypatch.setattr(
            stats, "MakeNameAndCSV", lambda p: ("Alice", "match.mkv:1,0,6,A,collar tie,double,sprawl,T,E")
        )

        seq: TagSequence = TagSequence(
            start_time=0, end_time=6, tie_up="collar tie",
            team_moves=["double"], op_moves=["sprawl"],
            team_scores=["T"], op_scores=["E"],
        )
        result: dict[str, Any] = audit.run_retag_job(
            FakeContext(), "match.mkv", "Alice", [seq]
        )

        assert result["output"] == "match.mkv"
        assert result["compile"]["videos_processed"] == 1
        assert (dirs["taged"] / "match.mkv").read_bytes() == b"new"
        assert (dirs["csv"] / "Alice.csv").is_file()
        assert "match.mkv:1,0,6,A,collar tie,double,sprawl,T,E" in (
            dirs["csv"] / "Alice.csv"
        ).read_text()
        assert "A,collar tie,double,sprawl,T,E" in captured["metadata"]
        assert result["compile"] is not None

    def test_recompile_skipped_without_roster(self, tmp_path, monkeypatch) -> None:
        dirs = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["taged"] / "match.mkv").write_bytes(b"old")
        monkeypatch.setattr(audit, "GetVidDuration", lambda p: 60)

        def fake_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"new")
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(audit, "run_ffmpeg", fake_ffmpeg)
        monkeypatch.setattr(audit.configs, "load_wrestler_names", lambda: [])

        seq: TagSequence = TagSequence(start_time=0, end_time=6)
        result: dict[str, Any] = audit.run_retag_job(
            FakeContext(), "match.mkv", "Alice", [seq]
        )

        assert result["output"] == "match.mkv"
        assert result["compile"] is None
        assert not (dirs["csv"] / "Alice.csv").exists()

    def test_retag_dual_mode_embeds_combined_title(self, tmp_path, monkeypatch) -> None:
        dirs = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["taged"] / "match.mkv").write_bytes(b"old")
        monkeypatch.setattr(audit, "GetVidDuration", lambda p: 60)

        captured: dict[str, str] = {}

        def fake_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            captured["metadata"] = Path(cmd[4]).read_text()
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"new")
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(audit, "run_ffmpeg", fake_ffmpeg)
        monkeypatch.setattr(audit.configs, "load_wrestler_names", lambda: [])

        seq: TagSequence = TagSequence(
            start_time=0, end_time=6, tie_up="collar tie", opp_tie="underhook"
        )
        audit.run_retag_job(
            FakeContext(), "match.mkv", "Alice", [seq],
            opponent="Bob", match_result="W",
        )

        assert "title=Alice (W) / Bob" in captured["metadata"]
        assert "collar tie:underhook" in captured["metadata"]

    def test_missing_video_raises(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        with pytest.raises(FileNotFoundError, match="missing.mkv"):
            audit.run_retag_job(FakeContext(), "missing.mkv", "Alice", [TagSequence(start_time=0, end_time=5)])

    def test_requires_wrestler(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        with pytest.raises(ValueError, match="Wrestler name"):
            audit.run_retag_job(FakeContext(), "match.mkv", "  ", [TagSequence(start_time=0, end_time=5)])

    def test_requires_sequences(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        with pytest.raises(ValueError, match="At least one sequence"):
            audit.run_retag_job(FakeContext(), "match.mkv", "Alice", [])

    def test_rejects_overlapping_sequences(self, tmp_path, monkeypatch) -> None:
        dirs = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["taged"] / "match.mkv").write_bytes(b"old")
        monkeypatch.setattr(audit, "GetVidDuration", lambda p: 60)

        seqs: list[TagSequence] = [
            TagSequence(start_time=0, end_time=10),
            TagSequence(start_time=5, end_time=15),
        ]
        with pytest.raises(ValueError, match="overlaps"):
            audit.run_retag_job(FakeContext(), "match.mkv", "Alice", seqs)

    def test_ffmpeg_failure_keeps_original(self, tmp_path, monkeypatch) -> None:
        dirs = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["taged"] / "match.mkv").write_bytes(b"old")
        monkeypatch.setattr(audit, "GetVidDuration", lambda p: 60)

        def failed_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"partial")
            return FfmpegResult(success=False, message="encode error")

        monkeypatch.setattr(audit, "run_ffmpeg", failed_ffmpeg)

        with pytest.raises(RuntimeError, match="encode error"):
            audit.run_retag_job(FakeContext(), "match.mkv", "Alice", [TagSequence(start_time=0, end_time=5)])
        assert (dirs["taged"] / "match.mkv").read_bytes() == b"old"
        assert not (dirs["tmp"] / "retag-testjob.mkv").exists()


class TestAuditRoutes:
    """Tests for GET /api/audit and POST /api/audit/{file}/retag."""

    def test_audit_lists_videos(self, client, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        (tmp_path / "taged" / "match.mkv").write_bytes(b"old")
        _mock_make_name_and_csv(monkeypatch, {"match.mkv": ("Alice", CSV_DATA)})

        resp = client.get("/api/audit")
        assert resp.status_code == 200
        videos: list[dict[str, Any]] = resp.json()["videos"]
        assert videos[0]["name"] == "match.mkv"
        assert videos[0]["wrestler"] == "Alice"
        assert len(videos[0]["sequences"]) == 2

    def test_retag_route_completes(self, client, tmp_path, monkeypatch) -> None:
        dirs = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["taged"] / "match.mkv").write_bytes(b"old")
        monkeypatch.setattr(audit, "GetVidDuration", lambda p: 60)

        def fake_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"new")
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(audit, "run_ffmpeg", fake_ffmpeg)
        monkeypatch.setattr(audit.configs, "load_wrestler_names", lambda: ["Alice"])
        monkeypatch.setattr(
            stats, "MakeNameAndCSV", lambda p: ("Alice", "match.mkv:1,0,6,A,collar tie,double,sprawl,T,E")
        )

        payload: dict[str, Any] = {
            "wrestler": "Alice",
            "sequences": [{"start_time": 0, "end_time": 6}],
        }
        resp = client.post("/api/audit/match.mkv/retag", json=payload)
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["status"] == "queued"

        job: dict[str, Any] = _wait_for_job(client, body["job_id"])
        assert job["status"] == "done"
        assert job["result"]["output"] == "match.mkv"
        assert job["result"]["compile"]["videos_processed"] == 1
        assert (dirs["taged"] / "match.mkv").read_bytes() == b"new"

    def test_retag_missing_404(self, client) -> None:
        resp = client.post(
            "/api/audit/missing.mkv/retag",
            json={"wrestler": "Alice", "sequences": [{"start_time": 0, "end_time": 5}]},
        )
        assert resp.status_code == 404

    def test_retag_invalid_name_400(self, client) -> None:
        resp = client.post(
            "/api/audit/bad%2Fname.mkv/retag",
            json={"wrestler": "Alice", "sequences": [{"start_time": 0, "end_time": 5}]},
        )
        assert resp.status_code in (400, 404)

    def test_retag_no_sequences_400(self, client, tmp_path, monkeypatch) -> None:
        dirs = _redirect_dirs(monkeypatch, tmp_path)
        (dirs["taged"] / "match.mkv").write_bytes(b"old")

        resp = client.post(
            "/api/audit/match.mkv/retag",
            json={"wrestler": "Alice", "sequences": []},
        )
        assert resp.status_code == 400
