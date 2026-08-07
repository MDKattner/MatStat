"""Tests for the web tagging flow — metadata building, the tag job, and API routes."""

import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.helpers import ChapterSequence
from scripts.web import tag
from scripts.web.ffmpeg import FfmpegResult
from scripts.web.tag import TagSequence


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

    def test_success_hides_original_and_creates_output(self, tmp_path, monkeypatch) -> None:
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

        assert result == {"output": "match.mkv", "original_hidden": ".match.mkv"}
        assert not source.exists()
        assert (tmp_path / "untaged" / ".match.mkv").read_bytes() == b"source"
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


class TestUploadRoutes:
    """Tests for POST /api/videos — video upload into the untagged pool."""

    def test_upload_writes_file(self, client, tmp_path) -> None:
        resp = client.post(
            "/api/videos",
            files={"file": ("match.mkv", b"video-bytes", "video/x-matroska")},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "file": "match.mkv"}
        assert (tmp_path / "untaged" / "match.mkv").read_bytes() == b"video-bytes"

    def test_upload_duplicate_409(self, client, tmp_path) -> None:
        (tmp_path / "untaged" / "match.mkv").write_bytes(b"x")
        resp = client.post(
            "/api/videos",
            files={"file": ("match.mkv", b"new", "video/x-matroska")},
        )
        assert resp.status_code == 409
        assert (tmp_path / "untaged" / "match.mkv").read_bytes() == b"x"

    def test_upload_invalid_name_400(self, client) -> None:
        resp = client.post(
            "/api/videos",
            files={"file": ("bad/name.mkv", b"x", "video/x-matroska")},
        )
        assert resp.status_code == 400

    def test_upload_dotfile_400(self, client) -> None:
        resp = client.post(
            "/api/videos",
            files={"file": (".hidden", b"x", "video/x-matroska")},
        )
        assert resp.status_code == 400


class TestConfigRoutes:
    """Tests for GET /api/configs/{name} — config file entries."""

    def test_returns_items(self, client) -> None:
        resp = client.get("/api/configs/Wrestlers.config")
        assert resp.status_code == 200
        items: list[str] = resp.json()["items"]
        assert "UNKNOWN" in items

    def test_unknown_config_404(self, client) -> None:
        resp = client.get("/api/configs/Nonexistent.config")
        assert resp.status_code == 404

    def test_invalid_name_400(self, client) -> None:
        resp = client.get("/api/configs/evil.config.txt")
        assert resp.status_code == 400

    def test_path_traversal_rejected_400(self, client) -> None:
        resp = client.get("/api/configs/..%5C..%5Cetc%5Cpasswd.config")
        assert resp.status_code == 400


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

        def fake_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"tagged")
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(tag, "run_ffmpeg", fake_ffmpeg)

        payload: dict[str, Any] = {
            "video": "match.mkv",
            "wrestler": "Alice",
            "sequences": [
                {
                    "start_time": 10,
                    "end_time": 20,
                    "attack_defend": True,
                    "tie_up": "collar tie",
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
        assert job["result"] == {"output": "match.mkv", "original_hidden": ".match.mkv"}

        assert not source.exists()
        assert (tmp_path / "untaged" / ".match.mkv").exists()
        assert (tmp_path / "taged" / "match.mkv").read_bytes() == b"tagged"

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
