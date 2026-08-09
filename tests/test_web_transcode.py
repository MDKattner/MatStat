"""Tests for the web preview transcoder — HLS paths, readiness, and commands."""

import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.web import transcode
from scripts.web.ffmpeg import FfmpegResult


def _redirect_preview_dir(monkeypatch, tmp_path: Path) -> None:
    """Point the transcode module's PREVIEW_DIR at a temp dir."""
    monkeypatch.setattr(transcode, "PREVIEW_DIR", tmp_path / "preview")
    (tmp_path / "preview").mkdir(parents=True, exist_ok=True)


class TestHlsPaths:
    """Tests for the HLS on-disk layout helpers."""

    def test_paths_live_under_hls_dir(self, monkeypatch) -> None:
        monkeypatch.setattr(transcode, "PREVIEW_DIR", Path("/tmp/preview"))
        source: Path = Path("vids/taged/match.mkv")
        assert transcode.hls_dir_path(source) == Path("/tmp/preview/hls/match.mkv")
        assert transcode.playlist_path(source) == Path("/tmp/preview/hls/match.mkv/prog.m3u8")
        assert transcode.segments_dir(source) == Path("/tmp/preview/hls/match.mkv/segments")


class TestHlsIsReady:
    """Tests for hls_is_ready playlist playability checks."""

    def test_missing_source_not_ready(self, tmp_path, monkeypatch) -> None:
        _redirect_preview_dir(monkeypatch, tmp_path)
        playlist: Path = transcode.playlist_path(tmp_path / "match.mkv")
        playlist.parent.mkdir(parents=True, exist_ok=True)
        playlist.write_text("#EXTM3U\n#EXT-X-ENDLIST\n")
        assert transcode.hls_is_ready(tmp_path / "match.mkv") is False

    def test_missing_playlist_not_ready(self, tmp_path, monkeypatch) -> None:
        _redirect_preview_dir(monkeypatch, tmp_path)
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"x")
        assert transcode.hls_is_ready(source) is False

    def test_stub_only_not_ready(self, tmp_path, monkeypatch) -> None:
        _redirect_preview_dir(monkeypatch, tmp_path)
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"x")
        playlist: Path = transcode.playlist_path(source)
        playlist.parent.mkdir(parents=True, exist_ok=True)
        playlist.write_text("#EXTM3U\n#EXT-X-VERSION:3\n")
        assert transcode.hls_is_ready(source) is False

    def test_live_playlist_with_segment_is_ready(self, tmp_path, monkeypatch) -> None:
        _redirect_preview_dir(monkeypatch, tmp_path)
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"x")
        playlist: Path = transcode.playlist_path(source)
        playlist.parent.mkdir(parents=True, exist_ok=True)
        playlist.write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:4.0,\nseg_00000.ts\n")
        assert transcode.hls_is_ready(source) is True

    def test_finished_playlist_is_ready(self, tmp_path, monkeypatch) -> None:
        _redirect_preview_dir(monkeypatch, tmp_path)
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"x")
        playlist: Path = transcode.playlist_path(source)
        playlist.parent.mkdir(parents=True, exist_ok=True)
        playlist.write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-ENDLIST\n")
        assert transcode.hls_is_ready(source) is True

    def test_stale_finished_playlist_not_ready(self, tmp_path, monkeypatch) -> None:
        _redirect_preview_dir(monkeypatch, tmp_path)
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"x")
        playlist: Path = transcode.playlist_path(source)
        playlist.parent.mkdir(parents=True, exist_ok=True)
        playlist.write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-ENDLIST\n")
        os.utime(source, (2000, 2000))
        os.utime(playlist, (1000, 1000))
        assert transcode.hls_is_ready(source) is False


class TestBuildHlsCmd:
    """Tests for build_hls_cmd ffmpeg arguments."""

    def _cmd(self, use_stream_copy: bool) -> list[str]:
        return transcode.build_hls_cmd(
            Path("a.mkv"),
            Path("prog.m3u8"),
            Path("segments"),
            use_stream_copy=use_stream_copy,
        )

    def test_common_arguments(self) -> None:
        cmd: list[str] = self._cmd(False)
        assert cmd[0] == "ffmpeg"
        assert cmd[cmd.index("-i") + 1] == "a.mkv"
        assert cmd[cmd.index("-f") + 1] == "hls"
        assert cmd[cmd.index("-hls_list_size") + 1] == "0"
        assert "-hls_segment_filename" in cmd
        assert cmd[-1] == "prog.m3u8"

    def test_stream_copy_branch(self) -> None:
        cmd: list[str] = self._cmd(True)
        assert cmd[cmd.index("-c") + 1] == "copy"
        assert "libx264" not in cmd

    def test_reencode_branch(self) -> None:
        cmd: list[str] = self._cmd(False)
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert cmd[cmd.index("-preset") + 1] == "ultrafast"
        assert "scale=-2:min(720\\,ih)" in cmd
        assert cmd[cmd.index("-c:a") + 1] == "aac"


class TestPrepareHls:
    """Tests for prepare_hls — fresh HLS workspace creation."""

    def test_creates_stub_playlist_and_dirs(self, tmp_path, monkeypatch) -> None:
        _redirect_preview_dir(monkeypatch, tmp_path)
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"x")

        playlist: Path = transcode.prepare_hls(source)
        assert playlist == transcode.playlist_path(source)
        assert playlist.read_text() == "#EXTM3U\n#EXT-X-VERSION:3\n"
        assert transcode.segments_dir(source).is_dir()

    def test_wipes_stale_segments(self, tmp_path, monkeypatch) -> None:
        _redirect_preview_dir(monkeypatch, tmp_path)
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"x")
        stale: Path = transcode.segments_dir(source) / "seg_00000.ts"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_bytes(b"old")

        transcode.prepare_hls(source)
        assert not stale.exists()


class TestTranscodeHls:
    """Tests for transcode_hls — the job body that runs the encoder."""

    def test_success_appends_endlist_and_clears_in_flight(
        self, tmp_path, monkeypatch
    ) -> None:
        _redirect_preview_dir(monkeypatch, tmp_path)
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"source")
        transcode.prepare_hls(source)
        transcode.mark_in_flight(source, "job1")

        def fake_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            playlist: Path = Path(cmd[-1])
            playlist.write_text(
                "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:4.0,\nseg_00000.ts\n"
            )
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(transcode, "run_ffmpeg", fake_ffmpeg)
        monkeypatch.setattr(transcode, "GetVideoCodecs", lambda p: {"video": "hevc", "audio": "aac"})

        transcode.transcode_hls(source, "job1")

        playlist: Path = transcode.playlist_path(source)
        assert "#EXT-X-ENDLIST" in playlist.read_text()
        assert transcode.in_flight_job(source) is None

    def test_uses_stream_copy_for_h264_aac(self, tmp_path, monkeypatch) -> None:
        _redirect_preview_dir(monkeypatch, tmp_path)
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"source")
        transcode.prepare_hls(source)
        transcode.mark_in_flight(source, "job1")

        captured: dict[str, list[str]] = {}

        def fake_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            captured["cmd"] = cmd
            Path(cmd[-1]).write_text("#EXTM3U\n#EXT-X-ENDLIST\n")
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(transcode, "run_ffmpeg", fake_ffmpeg)
        monkeypatch.setattr(
            transcode, "GetVideoCodecs", lambda p: {"video": "h264", "audio": "aac"}
        )

        transcode.transcode_hls(source, "job1")
        assert captured["cmd"][captured["cmd"].index("-c") + 1] == "copy"

    def test_uses_reencode_for_other_codecs(self, tmp_path, monkeypatch) -> None:
        _redirect_preview_dir(monkeypatch, tmp_path)
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"source")
        transcode.prepare_hls(source)
        transcode.mark_in_flight(source, "job1")

        captured: dict[str, list[str]] = {}

        def fake_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            captured["cmd"] = cmd
            Path(cmd[-1]).write_text("#EXTM3U\n#EXT-X-ENDLIST\n")
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(transcode, "run_ffmpeg", fake_ffmpeg)
        monkeypatch.setattr(
            transcode, "GetVideoCodecs", lambda p: {"video": "hevc", "audio": "aac"}
        )

        transcode.transcode_hls(source, "job1")
        assert "libx264" in captured["cmd"]
        assert "-c" not in captured["cmd"] or captured["cmd"][captured["cmd"].index("-c") + 1] != "copy"

    def test_failure_raises_and_cleans_up(self, tmp_path, monkeypatch) -> None:
        _redirect_preview_dir(monkeypatch, tmp_path)
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"source")
        transcode.prepare_hls(source)
        transcode.mark_in_flight(source, "job1")

        def failed_ffmpeg(*args, **kwargs) -> FfmpegResult:
            return FfmpegResult(success=False, message="encoding error")

        monkeypatch.setattr(transcode, "run_ffmpeg", failed_ffmpeg)
        monkeypatch.setattr(transcode, "GetVideoCodecs", lambda p: {})

        with pytest.raises(RuntimeError, match="encoding error"):
            transcode.transcode_hls(source, "job1")
        assert not transcode.hls_dir_path(source).exists()
        assert transcode.in_flight_job(source) is None

    def test_cancel_event_aborts(self, tmp_path, monkeypatch) -> None:
        _redirect_preview_dir(monkeypatch, tmp_path)
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"source")
        transcode.prepare_hls(source)
        transcode.mark_in_flight(source, "job1")

        captured: list[threading.Event | None] = []

        def cancelled_ffmpeg(cmd, description="",
                             on_progress=None, cancel_event=None) -> FfmpegResult:
            captured.append(cancel_event)
            return FfmpegResult(success=False, message="Cancelled")

        monkeypatch.setattr(transcode, "run_ffmpeg", cancelled_ffmpeg)
        monkeypatch.setattr(transcode, "GetVideoCodecs", lambda p: {})

        cancel_event: threading.Event = threading.Event()
        cancel_event.set()

        with pytest.raises(RuntimeError, match="Cancelled"):
            transcode.transcode_hls(source, "job1", cancel_event=cancel_event)
        assert captured == [cancel_event]
        assert transcode.in_flight_job(source) is None
