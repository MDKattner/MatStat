"""Tests for the web Combine Clips flow — command builders, jobs, and routes."""

import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.helpers import (
    COL_END_TIME,
    COL_START_TIME,
    COL_TIE_UP,
)
from scripts.web import app as web_app
from scripts.web import clips
from scripts.web.ffmpeg import FfmpegResult

CSV_ROW: str = '"v.mkv:1",0,6,A,collar tie,"double","sprawl","T","E"'


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
    """Point the clips module's data dirs at a temp dir."""
    dirs: dict[str, Path] = {
        "csv": tmp_path / "csv",
        "taged": tmp_path / "taged",
        "clips": tmp_path / "clips",
        "tmp": tmp_path / "tmp",
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(clips, "csv_dir", dirs["csv"])
    monkeypatch.setattr(clips, "taged_dir", dirs["taged"])
    monkeypatch.setattr(clips, "clips_dir", dirs["clips"])
    monkeypatch.setattr(clips, "tmp_dir", dirs["tmp"])
    return dirs


def _redirect_route_dirs(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    """Redirect clips module dirs plus the web_app dirs the routes read."""
    dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(web_app, "csv_dir", dirs["csv"])
    monkeypatch.setattr(web_app, "clips_dir", dirs["clips"])
    return dirs


def _write_csv(dirs: dict[str, Path], name: str = "Alice", content: str = CSV_ROW) -> None:
    (dirs["csv"] / f"{name}.csv").write_text(content)


def _mock_ffmpeg(monkeypatch, success: bool = True) -> None:
    """Stub clips.run_ffmpeg to write the output file (and segments) on success."""

    def fake(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
        if not success:
            return FfmpegResult(success=False, message="encode error")
        output: Path = Path(cmd[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"ok")
        return FfmpegResult(success=True, message="ok")

    monkeypatch.setattr(clips, "run_ffmpeg", fake)


def _mock_codecs(monkeypatch) -> None:
    monkeypatch.setattr(clips, "GetVideoCodecs", lambda _p: {"video": "h264", "audio": "aac"})


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


class TestBuildExtractCmd:
    """Tests for clips.build_extract_cmd — ffmpeg segment extraction args."""

    def test_stream_copy_for_h264_aac(self) -> None:
        cmd: list[str] = clips.build_extract_cmd(
            Path("in.mkv"), 5, 15, Path("seg.ts"), use_stream_copy=True,
            codecs={"video": "h264", "audio": "aac"},
        )
        assert cmd[:5] == ["ffmpeg", "-i", "in.mkv", "-ss", "5"]
        assert cmd[5:7] == ["-to", "15"]
        assert "-c" in cmd and "copy" in cmd
        assert "-avoid_negative_ts" in cmd
        assert cmd[-2:] == ["-y", "seg.ts"]

    def test_reencode_when_codecs_not_compatible(self) -> None:
        cmd: list[str] = clips.build_extract_cmd(
            Path("in.mkv"), 0, 5, Path("seg.ts"), use_stream_copy=True,
            codecs={"video": "hevc", "audio": "aac"},
        )
        assert "copy" not in cmd
        assert "-vf" in cmd and "scale=1280:720,setsar=1" in cmd
        assert cmd[cmd.index("-c:v") + 1] == "libx264"

    def test_reencode_when_stream_copy_disabled(self) -> None:
        cmd: list[str] = clips.build_extract_cmd(
            Path("in.mkv"), 0, 5, Path("seg.ts"), use_stream_copy=False
        )
        assert "copy" not in cmd
        assert "-c:v" in cmd


class TestBuildConcatCmd:
    """Tests for clips.build_concat_cmd — ffmpeg concatenation args."""

    def test_arguments(self) -> None:
        cmd: list[str] = clips.build_concat_cmd(Path("list.txt"), Path("out.mkv"))
        assert cmd[1:5] == ["-f", "concat", "-safe", "0"]
        assert cmd[5:7] == ["-i", "list.txt"]
        assert cmd[7:9] == ["-c", "copy"]
        assert cmd[-2:] == ["-y", "out.mkv"]


class TestFilterDataframe:
    """Tests for clips.filter_dataframe — sequence filtering."""

    def test_starting_tie(self, simple_df) -> None:
        filtered = clips.filter_dataframe(simple_df, "Starting Tie", "collar tie")
        assert len(filtered) == 1
        assert filtered[COL_TIE_UP].iloc[0] == "collar tie"

    def test_move_used(self, simple_df) -> None:
        filtered = clips.filter_dataframe(simple_df, "Move Used", "double")
        assert len(filtered) == 1
        assert filtered[COL_START_TIME].iloc[0] == 0

    def test_move_defended(self, simple_df) -> None:
        filtered = clips.filter_dataframe(simple_df, "Move Defended", "sweep single")
        assert len(filtered) == 1
        assert filtered[COL_END_TIME].iloc[0] == 20


class TestMatchesToJson:
    """Tests for clips.matches_to_json — match serialization."""

    def test_serializes_matches(self, simple_df) -> None:
        filtered = clips.filter_dataframe(simple_df, "Move Used", "double")
        matches: list[dict[str, Any]] = clips.matches_to_json(filtered)
        assert matches == [{
            "video": "v.mkv",
            "start_time": 0,
            "end_time": 10,
            "attacking": True,
            "tie_up": "collar tie",
            "moves": ["double"],
        }]


class TestRunCombineClipsJob:
    """Tests for clips.run_combine_clips_job — the single-reel job body."""

    def test_creates_reel(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)
        (dirs["taged"] / "v.mkv").write_bytes(b"video")
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        ctx: FakeContext = FakeContext()
        result: dict[str, Any] = clips.run_combine_clips_job(
            ctx, "Alice", "Starting Tie", "collar tie", use_stream_copy=True
        )

        assert result == {
            "output": "Alice_Starting_Tie_collar_tie.mkv",
            "segments": 1,
            "errors": [],
        }
        assert (dirs["clips"] / "Alice_Starting_Tie_collar_tie.mkv").read_bytes() == b"ok"
        assert ctx.reports[-1] == (100, "Created: Alice_Starting_Tie_collar_tie.mkv")

    def test_output_name_sanitizes_spaces(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(
            dirs,
            name="Bob Smith",
            content='"v.mkv:1",0,6,A,standing,"high crotch:double","sprawl","T","E"',
        )
        (dirs["taged"] / "v.mkv").write_bytes(b"video")
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        result: dict[str, Any] = clips.run_combine_clips_job(
            ctx=FakeContext(), wrestler="Bob Smith", filter_type="Move Used",
            filter_item="high crotch", use_stream_copy=False,
        )
        assert result["output"] == "Bob_Smith_Move_Used_high_crotch.mkv"

    def test_missing_csv_raises(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        with pytest.raises(FileNotFoundError, match="Compile Stats"):
            clips.run_combine_clips_job(
                FakeContext(), "Alice", "Starting Tie", "collar tie"
            )

    def test_no_matches_raises(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)

        with pytest.raises(ValueError, match="No sequences found"):
            clips.run_combine_clips_job(
                FakeContext(), "Alice", "Starting Tie", "nonexistent tie"
            )

    def test_concat_failure_raises(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)
        (dirs["taged"] / "v.mkv").write_bytes(b"video")
        _mock_codecs(monkeypatch)

        def fake(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            if "concat" in cmd:
                return FfmpegResult(success=False, message="concat error")
            output: Path = Path(cmd[-1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"seg")
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(clips, "run_ffmpeg", fake)

        with pytest.raises(RuntimeError, match="concat error"):
            clips.run_combine_clips_job(
                FakeContext(), "Alice", "Starting Tie", "collar tie"
            )
        assert not (dirs["clips"] / "Alice_Starting_Tie_collar_tie.mkv").exists()

    def test_missing_source_video_raises(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        with pytest.raises(RuntimeError, match="No clips were successfully extracted"):
            clips.run_combine_clips_job(
                FakeContext(), "Alice", "Starting Tie", "collar tie"
            )


class TestRunBatchClipsJob:
    """Tests for clips.run_batch_clips_job — the per-wrestler batch job."""

    def test_mixes_success_and_failure(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(dirs, "Alice")
        (dirs["taged"] / "v.mkv").write_bytes(b"video")
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        result: dict[str, Any] = clips.run_batch_clips_job(
            FakeContext(), ["Alice", "Bob Smith"], "Starting Tie", "collar tie"
        )

        assert result["successes"] == 1
        assert result["failures"] == 1
        assert result["results"][0]["success"] is True
        assert result["results"][0]["wrestler"] == "Alice"
        assert result["results"][1]["success"] is False
        assert "Compile Stats" in result["results"][1]["message"]
        assert result["summary"] == "Batch complete. 1 succeeded, 1 failed."


class TestClipRoutes:
    """Tests for the clips/find, combine-clips, and download routes."""

    def _payload(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "wrestler": "Alice",
            "filter_type": "Starting Tie",
            "filter_item": "collar tie",
        }
        base.update(overrides)
        return base

    def test_find_returns_matches(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_route_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)

        resp = client.post("/api/clips/find", json=self._payload())
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["count"] == 1
        assert body["matches"][0]["video"] == "v.mkv"
        assert body["matches"][0]["tie_up"] == "collar tie"

    def test_find_invalid_filter_422(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_route_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)

        resp = client.post("/api/clips/find", json=self._payload(filter_type="Bogus"))
        assert resp.status_code == 422

    def test_find_missing_csv_404(self, client, tmp_path, monkeypatch) -> None:
        _redirect_route_dirs(monkeypatch, tmp_path)

        resp = client.post("/api/clips/find", json=self._payload())
        assert resp.status_code == 404
        assert "Compile Stats" in resp.json()["detail"]

    def test_combine_job_completes_and_download(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_route_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)
        (dirs["taged"] / "v.mkv").write_bytes(b"video")
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        resp = client.post("/api/combine-clips", json=self._payload())
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["status"] == "queued"
        job: dict[str, Any] = _wait_for_job(client, body["job_id"])
        assert job["status"] == "done"
        assert job["result"]["output"] == "Alice_Starting_Tie_collar_tie.mkv"

        dl = client.get(f"/api/clips/{job['result']['output']}")
        assert dl.status_code == 200
        assert dl.content == b"ok"

    def test_combine_missing_csv_404(self, client, tmp_path, monkeypatch) -> None:
        _redirect_route_dirs(monkeypatch, tmp_path)

        resp = client.post("/api/combine-clips", json=self._payload())
        assert resp.status_code == 404

    def test_combine_empty_item_400(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_route_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)

        resp = client.post("/api/combine-clips", json=self._payload(filter_item="  "))
        assert resp.status_code == 400

    def test_batch_route_requires_wrestlers(self, client) -> None:
        payload: dict[str, Any] = {
            "wrestlers": [],
            "filter_type": "Starting Tie",
            "filter_item": "collar tie",
        }
        resp = client.post("/api/combine-clips/batch", json=payload)
        assert resp.status_code == 400

    def test_download_missing_clip_404(self, client, tmp_path, monkeypatch) -> None:
        _redirect_route_dirs(monkeypatch, tmp_path)

        resp = client.get("/api/clips/nope.mkv")
        assert resp.status_code == 404

    def test_download_invalid_name_400(self, client) -> None:
        resp = client.get("/api/clips/.hidden.mkv")
        assert resp.status_code == 400
