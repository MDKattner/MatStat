"""Tests for the web preview transcoder — cache logic and command building."""

import sys
import threading
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.web import transcode
from scripts.web.ffmpeg import FfmpegResult


class TestPreviewCachePath:
    """Tests for preview_cache_path naming."""

    def test_cache_lives_under_preview_dir(self, monkeypatch) -> None:
        monkeypatch.setattr(transcode, "PREVIEW_DIR", Path("/tmp/preview"))
        cache: Path = transcode.preview_cache_path(Path("vids/taged/match.mkv"))
        assert cache == Path("/tmp/preview/match.mkv.mp4")


class TestPreviewIsFresh:
    """Tests for preview_is_fresh mtime checks."""

    def test_missing_source_not_fresh(self, tmp_path) -> None:
        cache: Path = tmp_path / "preview.mp4"
        cache.write_bytes(b"x")
        assert transcode.preview_is_fresh(tmp_path / "missing.mkv", cache) is False

    def test_missing_cache_not_fresh(self, tmp_path) -> None:
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"x")
        assert transcode.preview_is_fresh(source, tmp_path / "preview.mp4") is False

    def test_stale_cache_not_fresh(self, tmp_path) -> None:
        import os

        source: Path = tmp_path / "match.mkv"
        cache: Path = tmp_path / "preview.mp4"
        source.write_bytes(b"x")
        cache.write_bytes(b"x")
        os.utime(cache, (1000, 1000))
        os.utime(source, (2000, 2000))
        assert transcode.preview_is_fresh(source, cache) is False

    def test_new_cache_is_fresh(self, tmp_path) -> None:
        import os

        source: Path = tmp_path / "match.mkv"
        cache: Path = tmp_path / "preview.mp4"
        source.write_bytes(b"x")
        cache.write_bytes(b"x")
        os.utime(source, (1000, 1000))
        os.utime(cache, (2000, 2000))
        assert transcode.preview_is_fresh(source, cache) is True


class TestBuildTranscodeCmd:
    """Tests for build_transcode_cmd ffmpeg arguments."""

    def test_includes_browser_required_flags(self) -> None:
        cmd: list[str] = transcode.build_transcode_cmd(
            Path("a.mkv"), Path("a.mp4")
        )
        assert cmd[0] == "ffmpeg"
        assert "-i" in cmd
        assert cmd[cmd.index("-i") + 1] == "a.mkv"
        assert "libx264" in cmd
        assert "yuv420p" in cmd
        assert "+faststart" in cmd
        assert cmd[-1] == "a.mp4"


class TestEnsurePreview:
    """Tests for ensure_preview cache-or-transcode behaviour."""

    def test_returns_fresh_cache_without_ffmpeg(self, tmp_path, monkeypatch) -> None:
        source: Path = tmp_path / "match.mkv"
        cache: Path = tmp_path / "match.mkv.mp4"
        source.write_bytes(b"source")
        cache.write_bytes(b"cached")
        cache.touch()

        monkeypatch.setattr(transcode, "PREVIEW_DIR", tmp_path)

        def fail_ffmpeg(*args, **kwargs) -> FfmpegResult:
            raise AssertionError("ffmpeg should not be called on a cache hit")

        monkeypatch.setattr(transcode, "run_ffmpeg", fail_ffmpeg)

        result: Path = transcode.ensure_preview(source)
        assert result == cache

    def test_transcodes_when_no_cache(self, tmp_path, monkeypatch) -> None:
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"source")

        monkeypatch.setattr(transcode, "PREVIEW_DIR", tmp_path)

        def fake_ffmpeg(cmd, description="", on_progress=None, cancel_event=None) -> FfmpegResult:
            # Simulate a successful transcode producing the output file.
            output: Path = Path(cmd[-1])
            output.write_bytes(b"encoded")
            if on_progress:
                on_progress(10, description)
            return FfmpegResult(success=True, message="ok")

        monkeypatch.setattr(transcode, "run_ffmpeg", fake_ffmpeg)

        progress_log: list[tuple[int, str]] = []

        def on_progress(seconds: int, description: str) -> None:
            progress_log.append((seconds, description))

        result: Path = transcode.ensure_preview(source, on_progress=on_progress)
        assert result == tmp_path / "match.mkv.mp4"
        assert result.read_bytes() == b"encoded"
        assert progress_log == [(10, "Preparing preview: match.mkv")]

    def test_raises_when_transcode_fails(self, tmp_path, monkeypatch) -> None:
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"source")

        monkeypatch.setattr(transcode, "PREVIEW_DIR", tmp_path)

        def failed_ffmpeg(*args, **kwargs) -> FfmpegResult:
            return FfmpegResult(success=False, message="encoding error")

        monkeypatch.setattr(transcode, "run_ffmpeg", failed_ffmpeg)

        with pytest.raises(RuntimeError, match="encoding error"):
            transcode.ensure_preview(source)

    def test_cancel_event_aborts(self, tmp_path, monkeypatch) -> None:
        source: Path = tmp_path / "match.mkv"
        source.write_bytes(b"source")

        monkeypatch.setattr(transcode, "PREVIEW_DIR", tmp_path)

        captured: list[threading.Event] = []

        def cancelled_ffmpeg(cmd, description="",
                             on_progress=None, cancel_event=None) -> FfmpegResult:
            captured.append(cancel_event)
            return FfmpegResult(success=False, message="Cancelled")

        monkeypatch.setattr(transcode, "run_ffmpeg", cancelled_ffmpeg)

        cancel_event: threading.Event = threading.Event()
        cancel_event.set()

        with pytest.raises(RuntimeError, match="Cancelled"):
            transcode.ensure_preview(source, cancel_event=cancel_event)
        assert captured == [cancel_event]
