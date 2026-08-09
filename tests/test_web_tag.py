"""Tests for the web tagging flow — metadata building, the tag job, and API routes."""

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.helpers import ChapterSequence
from scripts.web import configs
from scripts.web import tag
from scripts.web import transcode
from scripts.web.ffmpeg import FfmpegResult
from scripts.web.tag import TagSequence


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


def _make_source(tmp_path: Path, name: str = "match.mkv") -> Path:
    """Create a fake source video under a temp untaged dir."""
    source: Path = tmp_path / "untaged" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    return source


def _redirect_dirs(monkeypatch, tmp_path) -> None:
    """Point the tag module's data dirs at a temp dir."""
    monkeypatch.setattr(tag, "untaged_dir", tmp_path / "untaged")
    monkeypatch.setattr(tag, "taged_dir", tmp_path / "taged")
    monkeypatch.setattr(tag, "tmp_dir", tmp_path / "tmp")
    (tmp_path / "untaged").mkdir(parents=True, exist_ok=True)
    (tmp_path / "taged").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)


class TestBuildTagMetadata:
    """Tests for build_tag_metadata — ffmpeg metadata file content."""

    def test_header_and_title(self) -> None:
        content: str = tag.build_tag_metadata([], "  Alice Smith  ")
        assert content.startswith(";FFMETADATA1\ntitle=Alice Smith\n\n")

    def test_includes_each_chapter_block(self) -> None:
        chap: ChapterSequence = ChapterSequence(
            start_time=10,
            end_time=20,
            attack_defend=True,
            tie_up="collar tie",
            team_moves=["double"],
            op_moves=["nothing"],
            team_scores=["T"],
            op_scores=["None"],
        )
        content: str = tag.build_tag_metadata([chap], "Alice")
        assert content.count("[CHAPTER]") == 1
        assert "START=10" in content
        assert "END=20" in content
        assert "title=A,collar tie,double,nothing,T,None" in content

    def test_preserves_empty_chapters(self) -> None:
        empty: ChapterSequence = ChapterSequence.MakeEmptyChap(0, 5)
        real: ChapterSequence = ChapterSequence.MakeEmptyChap(5, 10)
        content: str = tag.build_tag_metadata([empty, real], "Alice")
        assert content.count("[CHAPTER]") == 2
        assert content.count("EMPTY") == 2


class TestBuildTagCmd:
    """Tests for build_tag_cmd — ffmpeg arguments."""

    def test_cmd_arguments(self) -> None:
        cmd: list[str] = tag.build_tag_cmd(
            Path("in.mkv"), Path("meta.txt"), Path("out.mkv")
        )
        assert cmd[0] == "ffmpeg"
        assert cmd[1:3] == ["-i", "in.mkv"]
        assert cmd[3:5] == ["-i", "meta.txt"]
        assert cmd[5:7] == ["-map_metadata", "1"]
        assert cmd[7:9] == ["-codec", "copy"]
        assert cmd[-2:] == ["-y", "out.mkv"]


class TestRunTagJob:
    """Tests for run_tag_job — the tagging job body."""

    def test_success_deletes_original_and_creates_output(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        source: Path = _make_source(tmp_path, "match.mkv")
        monkeypatch.setattr(tag, "GetVidDuration", lambda p: 60)

        def fake_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"tagged")
            if on_progress:
                on_progress(30, description)
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(tag, "run_ffmpeg", fake_ffmpeg)

        ctx: FakeContext = FakeContext()
        seq: TagSequence = TagSequence(start_time=10, end_time=20, tie_up="collar tie")
        result: dict[str, Any] = tag.run_tag_job(ctx, "match.mkv", "Alice", [seq])

        assert result == {"output": "match.mkv"}
        assert not source.exists()
        assert not (tmp_path / "untaged" / ".match.mkv").exists()
        assert (tmp_path / "taged" / "match.mkv").read_bytes() == b"tagged"
        assert ctx.reports[0] == (10, "Writing metadata...")
        assert ctx.reports[-1] == (100, "Tagged match.mkv")

    def test_metadata_pads_gaps_with_empty_chapters(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _make_source(tmp_path, "gap.mkv")
        monkeypatch.setattr(tag, "GetVidDuration", lambda p: 60)

        captured: dict[str, str] = {}

        def fake_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            captured["metadata"] = Path(cmd[4]).read_text()
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"tagged")
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(tag, "run_ffmpeg", fake_ffmpeg)

        seq: TagSequence = TagSequence(
            start_time=5, end_time=10, tie_up="front headlock", team_moves=["double"]
        )
        tag.run_tag_job(FakeContext(), "gap.mkv", "Alice", [seq])

        blocks: list[str] = captured["metadata"].split("[CHAPTER]\n")[1:]
        assert len(blocks) == 3
        # Filler 0-5, real 5-10, filler 10-60.
        assert blocks[0].startswith("TIMEBASE=1/1\nSTART=0\nEND=5\n")
        assert blocks[1].startswith("TIMEBASE=1/1\nSTART=5\nEND=10\n")
        assert "double" in blocks[1]
        assert blocks[2].startswith("TIMEBASE=1/1\nSTART=10\nEND=60\n")

    def test_rejects_overlapping_sequences(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _make_source(tmp_path, "overlap.mkv")
        monkeypatch.setattr(tag, "GetVidDuration", lambda p: 60)

        seqs: list[TagSequence] = [
            TagSequence(start_time=0, end_time=10),
            TagSequence(start_time=5, end_time=15),
        ]
        with pytest.raises(ValueError, match="overlaps"):
            tag.run_tag_job(FakeContext(), "overlap.mkv", "Alice", seqs)

    def test_rejects_bad_times(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _make_source(tmp_path, "bad.mkv")

        seqs: list[TagSequence] = [TagSequence(start_time=10, end_time=10)]
        with pytest.raises(ValueError, match="before end time"):
            tag.run_tag_job(FakeContext(), "bad.mkv", "Alice", seqs)

    def test_requires_sequences(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _make_source(tmp_path, "none.mkv")

        with pytest.raises(ValueError, match="At least one sequence"):
            tag.run_tag_job(FakeContext(), "none.mkv", "Alice", [])

    def test_requires_wrestler(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _make_source(tmp_path, "noname.mkv")

        seqs: list[TagSequence] = [TagSequence(start_time=0, end_time=5)]
        with pytest.raises(ValueError, match="Wrestler name"):
            tag.run_tag_job(FakeContext(), "noname.mkv", "  ", seqs)

    def test_missing_video_raises(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        seqs: list[TagSequence] = [TagSequence(start_time=0, end_time=5)]
        with pytest.raises(FileNotFoundError, match="missing.mkv"):
            tag.run_tag_job(FakeContext(), "missing.mkv", "Alice", seqs)

    def test_ffmpeg_failure_cleans_output(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _make_source(tmp_path, "fail.mkv")
        monkeypatch.setattr(tag, "GetVidDuration", lambda p: 60)

        # Simulate ffmpeg failing after writing a partial output file.
        def failed_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"partial")
            return FfmpegResult(success=False, message="encode error")

        monkeypatch.setattr(tag, "run_ffmpeg", failed_ffmpeg)

        seqs: list[TagSequence] = [TagSequence(start_time=0, end_time=5)]
        with pytest.raises(RuntimeError, match="encode error"):
            tag.run_tag_job(FakeContext(), "fail.mkv", "Alice", seqs)
        assert not (tmp_path / "taged" / "fail.mkv").exists()

    def test_zero_duration_raises(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _make_source(tmp_path, "zero.mkv")
        monkeypatch.setattr(tag, "GetVidDuration", lambda p: 0)

        seqs: list[TagSequence] = [TagSequence(start_time=0, end_time=5)]
        with pytest.raises(RuntimeError, match="duration"):
            tag.run_tag_job(FakeContext(), "zero.mkv", "Alice", seqs)

    def test_dual_mode_embeds_title_and_tie_pair(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _make_source(tmp_path, "dual.mkv")
        monkeypatch.setattr(tag, "GetVidDuration", lambda p: 60)

        captured: dict[str, str] = {}

        def fake_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            captured["metadata"] = Path(cmd[4]).read_text()
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"tagged")
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(tag, "run_ffmpeg", fake_ffmpeg)

        seq: TagSequence = TagSequence(
            start_time=0, end_time=6, tie_up="collar tie", opp_tie="underhook",
            team_moves=["double"], op_moves=["nothing"],
            team_scores=["T"], op_scores=["None"],
        )
        tag.run_tag_job(
            FakeContext(), "dual.mkv", "Alice", [seq],
            opponent="Bob", match_result="W",
        )

        assert "title=Alice (W) / Bob" in captured["metadata"]
        assert "title=A,collar tie:underhook,double,nothing,T,None" in captured["metadata"]

    def test_invalid_match_result_raises(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        _make_source(tmp_path, "badresult.mkv")
        monkeypatch.setattr(tag, "GetVidDuration", lambda p: 60)

        seqs: list[TagSequence] = [TagSequence(start_time=0, end_time=5)]
        with pytest.raises(ValueError, match="Invalid match result"):
            tag.run_tag_job(FakeContext(), "badresult.mkv", "Alice", seqs, match_result="D")


class TestUploadRoutes:
    """Tests for POST /api/videos — batch video upload into the untagged pool."""

    def test_upload_writes_file(self, client, tmp_path) -> None:
        resp = client.post(
            "/api/videos",
            files={"files": ("match.mkv", b"video-bytes", "video/x-matroska")},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "results": [{"file": "match.mkv", "status": "ok"}],
            "uploaded": 1,
        }
        assert (tmp_path / "untaged" / "match.mkv").read_bytes() == b"video-bytes"

    def test_upload_batch_multiple_files(self, client, tmp_path) -> None:
        resp = client.post(
            "/api/videos",
            files=[
                ("files", ("a.mkv", b"aaa", "video/x-matroska")),
                ("files", ("b.mkv", b"bbb", "video/x-matroska")),
            ],
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "results": [
                {"file": "a.mkv", "status": "ok"},
                {"file": "b.mkv", "status": "ok"},
            ],
            "uploaded": 2,
        }
        assert (tmp_path / "untaged" / "a.mkv").read_bytes() == b"aaa"
        assert (tmp_path / "untaged" / "b.mkv").read_bytes() == b"bbb"

    def test_upload_duplicate_reports_conflict(self, client, tmp_path) -> None:
        (tmp_path / "untaged" / "match.mkv").write_bytes(b"x")
        resp = client.post(
            "/api/videos",
            files={"files": ("match.mkv", b"new", "video/x-matroska")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["uploaded"] == 0
        assert body["results"][0]["status"] == "conflict"
        assert (tmp_path / "untaged" / "match.mkv").read_bytes() == b"x"

    def test_upload_invalid_name_reports_error(self, client) -> None:
        resp = client.post(
            "/api/videos",
            files={"files": ("bad/name.mkv", b"x", "video/x-matroska")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["uploaded"] == 0
        assert body["results"][0]["status"] == "error"
        assert "Invalid file name" in body["results"][0]["detail"]

    def test_upload_dotfile_reports_error(self, client) -> None:
        resp = client.post(
            "/api/videos",
            files={"files": (".hidden", b"x", "video/x-matroska")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["uploaded"] == 0
        assert body["results"][0]["status"] == "error"

    def test_upload_batch_mixed_results(self, client, tmp_path) -> None:
        (tmp_path / "untaged" / "existing.mkv").write_bytes(b"old")
        resp = client.post(
            "/api/videos",
            files=[
                ("files", ("new.mkv", b"new", "video/x-matroska")),
                ("files", ("existing.mkv", b"other", "video/x-matroska")),
                ("files", ("bad/name.mkv", b"x", "video/x-matroska")),
            ],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["uploaded"] == 1
        statuses: dict[str, str] = {r["file"]: r["status"] for r in body["results"]}
        assert statuses == {
            "new.mkv": "ok",
            "existing.mkv": "conflict",
            "bad/name.mkv": "error",
        }
        assert (tmp_path / "untaged" / "new.mkv").read_bytes() == b"new"
        assert (tmp_path / "untaged" / "existing.mkv").read_bytes() == b"old"

    def test_upload_no_files_400(self, client) -> None:
        resp = client.post("/api/videos", files={})
        assert resp.status_code == 400


def _seed_app_config(tmp_path: Path) -> None:
    """Write a small cfg/config.json with one Folkstyle ruleset."""
    (tmp_path / "config.json").write_text(
        json.dumps({
            "active_ruleset": "Folkstyle",
            "moves": ["double", "single"],
            "ties": ["collar tie"],
            "rulesets": {
                "Folkstyle": {
                    "description": "test ruleset",
                    "outcomes": {
                        "T": {"points": 3, "description": "Takedown", "counts_as_pin": False},
                    },
                }
            },
        })
    )


class TestConfigRoutes:
    """Tests for GET /api/config and GET /api/config/wrestlers."""

    def test_returns_merged_config(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(configs, "cfg_dir", tmp_path)
        _seed_app_config(tmp_path)
        (tmp_path / "Wrestlers.json").write_text(
            json.dumps({"wrestlers": ["Alice"], "teams": {"Varsity": ["Alice"]}})
        )

        resp = client.get("/api/config")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["active_ruleset"] == "Folkstyle"
        assert body["moves"] == ["double", "single"]
        assert body["ties"] == ["collar tie"]
        assert "Folkstyle" in body["rulesets"]
        assert body["wrestlers"] == ["Alice"]
        assert body["teams"] == {"Varsity": ["Alice"]}

    def test_falls_back_to_defaults(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(configs, "cfg_dir", tmp_path)

        resp = client.get("/api/config")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["active_ruleset"] == "Folkstyle"
        assert "Folkstyle" in body["rulesets"]

    def test_wrestlers_returns_roster(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(configs, "cfg_dir", tmp_path)
        (tmp_path / "Wrestlers.json").write_text(
            json.dumps({"wrestlers": ["Alice", "Bob"], "teams": {}})
        )

        resp = client.get("/api/config/wrestlers")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["wrestlers"] == ["Alice", "Bob"]
        assert body["teams"] == {}

    def test_wrestlers_falls_back_to_example(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(configs, "cfg_dir", tmp_path)
        (tmp_path / "Wrestlers.json.example").write_text(
            json.dumps({"wrestlers": ["Example"], "teams": {"A": ["Example"]}})
        )

        resp = client.get("/api/config/wrestlers")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["wrestlers"] == ["Example"]
        assert body["teams"] == {"A": ["Example"]}


class TestConfigWriteRoutes:
    """Tests for PUT /api/config and PUT /api/config/wrestlers."""

    def test_saves_app_config(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(configs, "cfg_dir", tmp_path)

        resp = client.put(
            "/api/config",
            json={
                "active_ruleset": "Folkstyle",
                "moves": ["double", " single ", ""],
                "ties": ["collar tie"],
                "rulesets": {
                    "Folkstyle": {
                        "description": "test ruleset",
                        "pin_points": 13,
                        "outcomes": {
                            "T": {"points": 3, "description": "Takedown"},
                            "E": {"points": 1, "counts_as_pin": False},
                        },
                    }
                },
            },
        )
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["status"] == "ok"
        assert body["active_ruleset"] == "Folkstyle"
        assert body["moves"] == ["double", "single"]
        assert body["ties"] == ["collar tie"]
        assert body["rulesets"]["Folkstyle"]["outcomes"]["T"]["points"] == 3
        assert body["rulesets"]["Folkstyle"]["pin_points"] == 13
        # Persisted to disk for the next GET.
        get_resp = client.get("/api/config")
        assert get_resp.status_code == 200
        assert get_resp.json()["moves"] == ["double", "single"]

    def test_invalid_active_ruleset_400(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(configs, "cfg_dir", tmp_path)
        resp = client.put(
            "/api/config",
            json={"active_ruleset": "Nope", "rulesets": {"Folkstyle": {}}},
        )
        assert resp.status_code == 400

    def test_missing_active_ruleset_422(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(configs, "cfg_dir", tmp_path)
        resp = client.put("/api/config", json={"moves": ["double"]})
        assert resp.status_code == 422

    def test_saves_roster_normalized(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(configs, "cfg_dir", tmp_path)
        resp = client.put(
            "/api/config/wrestlers",
            json={
                "wrestlers": ["Alice", "Bob"],
                "teams": {"Varsity": ["Alice", "Ghost"]},
            },
        )
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["status"] == "ok"
        assert body["wrestlers"] == ["Alice", "Bob"]
        # Team member "Ghost" is not on the wrestler list and is dropped.
        assert body["teams"] == {"Varsity": ["Alice"]}

        get_resp = client.get("/api/config/wrestlers")
        assert get_resp.status_code == 200
        assert get_resp.json()["teams"] == {"Varsity": ["Alice"]}


class TestTagRoute:
    """Tests for POST /api/tag — queuing and completing a tag job."""

    def _wait_for_job(self, client, job_id: str, timeout: float = 5.0) -> dict[str, Any]:
        deadline: float = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = client.get(f"/api/jobs/{job_id}")
            assert resp.status_code == 200
            job: dict[str, Any] = resp.json()
            if job["status"] in ("done", "failed", "cancelled"):
                return job
            time.sleep(0.01)
        raise AssertionError(f"Job {job_id} did not finish in {timeout}s")

    def test_tag_job_completes(self, client, tmp_path, monkeypatch) -> None:
        source: Path = tmp_path / "untaged" / "match.mkv"
        source.write_bytes(b"source")
        monkeypatch.setattr(tag, "GetVidDuration", lambda p: 60)

        captured: dict[str, str] = {}

        def fake_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            captured["metadata"] = Path(cmd[4]).read_text()
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"tagged")
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(tag, "run_ffmpeg", fake_ffmpeg)

        # Pre-warming the tagged preview runs in a background job; stub it so
        # it never touches real ffmpeg/ffprobe.
        def fake_transcode_hls(src, job_id, on_progress=None, cancel_event=None) -> None:
            return None

        monkeypatch.setattr(transcode, "transcode_hls", fake_transcode_hls)

        payload: dict[str, Any] = {
            "video": "match.mkv",
            "wrestler": "Alice",
            "opponent": "Bob",
            "match_result": "W",
            "sequences": [
                {
                    "start_time": 10,
                    "end_time": 20,
                    "attack_defend": True,
                    "tie_up": "collar tie",
                    "opp_tie": "underhook",
                    "team_moves": ["double"],
                    "op_moves": ["nothing"],
                    "team_scores": ["T"],
                    "op_scores": ["None"],
                }
            ],
        }
        resp = client.post("/api/tag", json=payload)
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["status"] == "queued"
        job_id: str = body["job_id"]

        job: dict[str, Any] = self._wait_for_job(client, job_id)
        assert job["status"] == "done"
        assert job["result"] == {"output": "match.mkv"}
        assert "title=Alice (W) / Bob" in captured["metadata"]
        assert "title=A,collar tie:underhook,double,nothing,T,None" in captured["metadata"]

        assert not source.exists()
        assert not (tmp_path / "untaged" / ".match.mkv").exists()
        assert (tmp_path / "taged" / "match.mkv").read_bytes() == b"tagged"

    def test_tag_invalid_match_result_400(self, client, tmp_path) -> None:
        (tmp_path / "untaged" / "match.mkv").write_bytes(b"x")
        payload: dict[str, Any] = {
            "video": "match.mkv",
            "wrestler": "Alice",
            "match_result": "D",
            "sequences": [{"start_time": 0, "end_time": 5}],
        }
        resp = client.post("/api/tag", json=payload)
        assert resp.status_code == 400

    def test_tag_missing_file_404(self, client) -> None:
        payload: dict[str, Any] = {
            "video": "missing.mkv",
            "wrestler": "Alice",
            "sequences": [{"start_time": 0, "end_time": 5}],
        }
        resp = client.post("/api/tag", json=payload)
        assert resp.status_code == 404

    def test_tag_invalid_name_400(self, client) -> None:
        payload: dict[str, Any] = {
            "video": "bad/name.mkv",
            "wrestler": "Alice",
            "sequences": [{"start_time": 0, "end_time": 5}],
        }
        resp = client.post("/api/tag", json=payload)
        assert resp.status_code == 400

    def test_tag_no_sequences_400(self, client, tmp_path) -> None:
        (tmp_path / "untaged" / "match.mkv").write_bytes(b"x")
        payload: dict[str, Any] = {"video": "match.mkv", "wrestler": "Alice", "sequences": []}
        resp = client.post("/api/tag", json=payload)
        assert resp.status_code == 400

    def test_tag_no_wrestler_400(self, client, tmp_path) -> None:
        (tmp_path / "untaged" / "match.mkv").write_bytes(b"x")
        payload: dict[str, Any] = {
            "video": "match.mkv",
            "wrestler": "  ",
            "sequences": [{"start_time": 0, "end_time": 5}],
        }
        resp = client.post("/api/tag", json=payload)
        assert resp.status_code == 400
