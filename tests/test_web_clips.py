"""Tests for the web Combine Clips flow — command builders, jobs, and routes."""

import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

sys.path.append(str(Path(__file__).parent.parent))

from conftest import FakeContext, seed_compiled_lines

from scripts.helpers import (
    COL_END_TIME,
    COL_START_TIME,
    COL_TIE_UP,
)
from scripts.web import app as web_app
from scripts.web import clips
from scripts.web.query import QuerySpec, apply_query
from scripts.web.ffmpeg import FfmpegResult

CSV_ROW: str = '"v.mkv:1",0,6,A,collar tie,"double","sprawl","T","E"'


def _redirect_dirs(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    """Point the clips module's data dirs at a temp dir."""
    dirs: dict[str, Path] = {
        "taged": tmp_path / "taged",
        "clips": tmp_path / "clips",
        "tmp": tmp_path / "tmp",
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(clips, "taged_dir", dirs["taged"])
    monkeypatch.setattr(clips, "clips_dir", dirs["clips"])
    monkeypatch.setattr(clips, "tmp_dir", dirs["tmp"])
    return dirs


def _redirect_route_dirs(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    """Redirect clips module dirs plus the web_app dirs the routes read."""
    dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(web_app, "clips_dir", dirs["clips"])
    return dirs


def _write_csv(dirs: dict[str, Path], name: str = "Alice", content: str = CSV_ROW) -> None:
    seed_compiled_lines(name, content.splitlines())


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
    """Tests for apply_query — the shared sequence filtering implementation."""

    def test_starting_tie(self, simple_df) -> None:
        filtered = apply_query(simple_df, QuerySpec(tie_up="collar tie"))
        assert len(filtered) == 1
        assert filtered[COL_TIE_UP].iloc[0] == "collar tie"

    def test_starting_tie_dual_mode_matches_first_element(self, simple_df) -> None:
        dual_df: pd.DataFrame = simple_df.copy()
        dual_df[COL_TIE_UP] = ["collar tie:underhook", "standing:front headlock"]

        filtered = apply_query(dual_df, QuerySpec(tie_up="collar tie"))
        assert len(filtered) == 1
        assert filtered[COL_TIE_UP].iloc[0] == "collar tie:underhook"

    def test_move_used(self, simple_df) -> None:
        filtered = apply_query(simple_df, QuerySpec(team_move="double"))
        assert len(filtered) == 1
        assert filtered[COL_START_TIME].iloc[0] == 0

    def test_move_defended(self, simple_df) -> None:
        filtered = apply_query(simple_df, QuerySpec(opp_move="sweep single"))
        assert len(filtered) == 1
        assert filtered[COL_END_TIME].iloc[0] == 20


class TestMatchesToJson:
    """Tests for clips.matches_to_json — match serialization."""

    def test_serializes_matches(self, simple_df) -> None:
        filtered = apply_query(simple_df, QuerySpec(team_move="double"))
        matches: list[dict[str, Any]] = clips.matches_to_json(filtered)
        assert matches == [{
            "video": "v.mkv",
            "start_time": 0,
            "end_time": 10,
            "attacking": True,
            "tie_up": "collar tie",
            "moves": ["double"],
            "net_points": 3,
            "adjusted_net_points": 3,
            "match_result": "",
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
            ctx, "Alice", QuerySpec(tie_up="collar tie"), use_stream_copy=True
        )

        assert result["output"].startswith("Alice_Starting_Tie_collar_tie_")
        assert result["output"].endswith(".mkv")
        assert result["segments"] == 1
        assert result["errors"] == []
        assert result["latest"] == "Alice_Starting_Tie_collar_tie_latest.mkv"
        assert (dirs["clips"] / result["output"]).read_bytes() == b"ok"
        assert (dirs["clips"] / result["latest"]).resolve() == (
            dirs["clips"] / result["output"]
        ).resolve()
        assert ctx.reports[-1] == (100, f"Created: {result['output']}")

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
            ctx=FakeContext(), wrestler="Bob Smith",
            spec=QuerySpec(team_move="high crotch", move_match="exact"),
            use_stream_copy=False,
        )
        assert result["output"].startswith("Bob_Smith_Move_Used_high_crotch_")

    def test_missing_csv_raises(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)

        with pytest.raises(FileNotFoundError, match="Compile Stats"):
            clips.run_combine_clips_job(
                FakeContext(), "Alice", QuerySpec(tie_up="collar tie")
            )

    def test_no_matches_raises(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)

        with pytest.raises(ValueError, match="No sequences found"):
            clips.run_combine_clips_job(
                FakeContext(), "Alice", QuerySpec(tie_up="nonexistent tie")
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
                FakeContext(), "Alice", QuerySpec(tie_up="collar tie")
            )
        assert not (dirs["clips"] / "Alice_Starting_Tie_collar_tie.mkv").exists()

    def test_missing_source_video_raises(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        with pytest.raises(RuntimeError, match="No clips were successfully extracted"):
            clips.run_combine_clips_job(
                FakeContext(), "Alice", QuerySpec(tie_up="collar tie")
            )


    def test_rerun_keeps_both_versions_and_moves_latest(
        self, tmp_path, monkeypatch
    ) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)
        (dirs["taged"] / "v.mkv").write_bytes(b"video")
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        first: dict[str, Any] = clips.run_combine_clips_job(
            FakeContext(), "Alice", QuerySpec(tie_up="collar tie")
        )
        second: dict[str, Any] = clips.run_combine_clips_job(
            FakeContext(), "Alice", QuerySpec(tie_up="collar tie")
        )

        assert first["output"] != second["output"]
        assert (dirs["clips"] / first["output"]).is_file()
        assert (dirs["clips"] / second["output"]).is_file()
        assert (dirs["clips"] / second["latest"]).resolve() == (
            dirs["clips"] / second["output"]
        ).resolve()

    def test_symlink_failure_skips_latest(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)
        (dirs["taged"] / "v.mkv").write_bytes(b"video")
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        def broken(self, target) -> None:
            raise OSError("symlinks unavailable")

        monkeypatch.setattr(Path, "symlink_to", broken)

        result: dict[str, Any] = clips.run_combine_clips_job(
            FakeContext(), "Alice", QuerySpec(tie_up="collar tie")
        )

        assert result["output"]
        assert result["latest"] is None


class TestRunBatchClipsJob:
    """Tests for clips.run_batch_clips_job — the per-wrestler batch job."""

    def test_mixes_success_and_failure(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(dirs, "Alice")
        (dirs["taged"] / "v.mkv").write_bytes(b"video")
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        result: dict[str, Any] = clips.run_batch_clips_job(
            FakeContext(), ["Alice", "Bob Smith"], QuerySpec(tie_up="collar tie")
        )

        assert result["successes"] == 1
        assert result["failures"] == 1
        assert result["results"][0]["success"] is True
        assert result["results"][0]["wrestler"] == "Alice"
        assert result["results"][1]["success"] is False
        assert "Compile Stats" in result["results"][1]["message"]
        assert result["summary"] == "Batch complete. 1 succeeded, 1 failed."


class TestListReels:
    """Reel version listing and preview/reel origin parity (P0-3)."""

    def test_groups_versions_and_marks_latest(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        older: Path = dirs["clips"] / "Alice_Tie_collar_tie_20260101-120000.mkv"
        newer: Path = dirs["clips"] / "Alice_Tie_collar_tie_20260102-120000.mkv"
        older.write_bytes(b"old")
        newer.write_bytes(b"new")
        (dirs["clips"] / "Alice_Tie_collar_tie_latest.mkv").symlink_to(newer.name)

        groups: list[dict[str, Any]] = clips.list_reels()

        assert len(groups) == 1
        group = groups[0]
        assert group["base"] == "Alice_Tie_collar_tie"
        assert group["latest"] == "Alice_Tie_collar_tie_latest.mkv"
        assert [v["name"] for v in group["versions"]] == [newer.name, older.name]
        assert [v["current"] for v in group["versions"]] == [True, False]

    def test_empty_or_missing_dir_returns_empty(self, tmp_path, monkeypatch) -> None:
        _redirect_dirs(monkeypatch, tmp_path)
        assert clips.list_reels() == []

    def test_reel_receives_the_previewed_origins(self, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_dirs(monkeypatch, tmp_path)
        _write_csv(dirs, "Alice", (
            'v.mkv:1,0,6,A,collar tie,"double","sprawl","T","E"\n'
            'v.mkv:2,6,12,D,standing,"sprawl","sweep single","E","T"\n'
            'w.mkv:1,0,6,A,collar tie,"single","whizzer","T","E"'
        ))
        spec: QuerySpec = QuerySpec(
            wrestlers=["Alice"], tie_up="collar tie", attack_mode="Attacking"
        )
        previewed: list[str] = list(clips.find_with_query(spec, "Alice").index)
        captured: dict[str, list[str]] = {}

        def fake_build(ctx, filtered, wrestler, filter_type, filter_item,
                       use_stream_copy, work_name):
            captured["origins"] = list(filtered.index)
            return ("reel.mkv", len(filtered), [], None)

        monkeypatch.setattr(clips, "_build_reel", fake_build)
        clips.run_combine_clips_job(FakeContext(), "Alice", spec)

        assert previewed
        assert captured["origins"] == previewed


class TestClipRoutes:
    """Tests for the clips/find, combine-clips, and download routes."""

    def _payload(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "wrestlers": ["Alice"],
            "tie_up": "collar tie",
        }
        base.update(overrides)
        return base

    def test_reels_route_lists_versions(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_route_dirs(monkeypatch, tmp_path)
        reel: Path = dirs["clips"] / "Alice_Tie_collar_tie_20260101-120000.mkv"
        reel.write_bytes(b"reel")

        resp = client.get("/api/reels")

        assert resp.status_code == 200
        groups: list[dict[str, Any]] = resp.json()["reels"]
        assert len(groups) == 1
        assert groups[0]["versions"][0]["name"] == reel.name

    def test_find_returns_matches(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_route_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)

        resp = client.post("/api/clips/find", json=self._payload())
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["count"] == 1
        assert body["matches"][0]["video"] == "v.mkv"
        assert body["matches"][0]["tie_up"] == "collar tie"

    def test_find_legacy_filter_fields_422(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_route_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)

        resp = client.post("/api/clips/find", json={
            "wrestler": "Alice",
            "filter_type": "Starting Tie",
            "filter_item": "collar tie",
        })
        assert resp.status_code == 422
        detail: str = resp.text
        assert "filter_type" in detail or "wrestler" in detail

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
        assert job["result"]["output"].startswith("Alice_Starting_Tie_collar_tie_")
        assert job["result"]["latest"] == "Alice_Starting_Tie_collar_tie_latest.mkv"

        dl = client.get(f"/api/clips/{job['result']['output']}")
        assert dl.status_code == 200
        assert dl.content == b"ok"

    def test_combine_missing_csv_404(self, client, tmp_path, monkeypatch) -> None:
        _redirect_route_dirs(monkeypatch, tmp_path)

        resp = client.post("/api/combine-clips", json=self._payload())
        assert resp.status_code == 404

    def test_combine_requires_exactly_one_wrestler(
        self, client, tmp_path, monkeypatch
    ) -> None:
        dirs: dict[str, Path] = _redirect_route_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)

        resp = client.post("/api/combine-clips", json=self._payload(
            wrestlers=["Alice", "Bob"]
        ))
        assert resp.status_code == 400

    def test_batch_route_requires_wrestlers(self, client) -> None:
        payload: dict[str, Any] = {
            "wrestlers": [],
            "tie_up": "collar tie",
        }
        resp = client.post("/api/combine-clips/batch", json=payload)
        assert resp.status_code == 400

    def test_batch_job_completes(self, client, tmp_path, monkeypatch) -> None:
        dirs: dict[str, Path] = _redirect_route_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)
        (dirs["taged"] / "v.mkv").write_bytes(b"video")
        _mock_codecs(monkeypatch)
        _mock_ffmpeg(monkeypatch)

        payload: dict[str, Any] = {
            "wrestlers": ["Alice"],
            "tie_up": "collar tie",
        }
        resp = client.post("/api/combine-clips/batch", json=payload)
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["status"] == "queued"
        job: dict[str, Any] = _wait_for_job(client, body["job_id"])
        assert job["status"] == "done"
        result: dict[str, Any] = job["result"]
        assert result["successes"] == 1
        assert result["failures"] == 0
        assert result["results"][0]["success"] is True
        assert result["results"][0]["wrestler"] == "Alice"
        output: str = result["results"][0]["output"]
        assert output.startswith("Alice_Starting_Tie_collar_tie_")
        assert result["results"][0]["message"] == f"Created: {output}"

        dl = client.get(f"/api/clips/{output}")
        assert dl.status_code == 200
        assert dl.content == b"ok"

    def test_download_missing_clip_404(self, client, tmp_path, monkeypatch) -> None:
        _redirect_route_dirs(monkeypatch, tmp_path)

        resp = client.get("/api/clips/nope.mkv")
        assert resp.status_code == 404

    def test_download_invalid_name_400(self, client) -> None:
        resp = client.get("/api/clips/.hidden.mkv")
        assert resp.status_code == 400

    def test_find_with_query_builder(self, client, tmp_path, monkeypatch) -> None:
        """POST /api/clips/find with QuerySpec fields."""
        dirs: dict[str, Path] = _redirect_route_dirs(monkeypatch, tmp_path)
        _write_csv(dirs)

        resp = client.post("/api/clips/find", json={
            "wrestlers": ["Alice"],
            "attack_mode": "Attacking",
        })
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["count"] == 1
        assert body["matches"][0]["attacking"] is True
        assert body["matches"][0]["wrestler"] == "Alice"
        assert "net_points" in body["matches"][0]
        assert "adjusted_net_points" in body["matches"][0]
        assert "match_result" in body["matches"][0]

    def test_find_empty_wrestler_list_400(
        self, client, tmp_path, monkeypatch
    ) -> None:
        """POST /api/clips/find with no wrestlers returns 400."""
        _redirect_route_dirs(monkeypatch, tmp_path)

        resp = client.post("/api/clips/find", json={"wrestlers": []})
        assert resp.status_code == 400
