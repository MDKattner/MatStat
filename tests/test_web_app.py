"""Tests for the web app — routes, static serving, WebSocket, preview flow."""

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).parent.parent))

from scripts.web import app as web_app
from scripts.web import transcode
from scripts.web.jobs import Job


class TestHealthAndStatic:
    """Tests for basic routing and static file serving."""

    def test_health_endpoint(self, client) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_index_serves_spa_shell(self, client) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "MatStat" in resp.text
        assert 'id="tab-bar"' in resp.text

    def test_index_omits_log_panel_by_default(self, client) -> None:
        resp = client.get("/")
        assert 'id="log-dock"' not in resp.text
        assert 'id="log-toggle"' not in resp.text

    def test_index_includes_log_panel_when_enabled(self, logging_client) -> None:
        resp = logging_client.get("/")
        assert 'id="log-dock"' in resp.text
        assert 'id="log-toggle"' in resp.text

    def test_static_css_served(self, client) -> None:
        resp = client.get("/static/css/app.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    def test_unknown_route_404(self, client) -> None:
        resp = client.get("/nope")
        assert resp.status_code == 404


class TestVideoListing:
    """Tests for the video directory listing endpoint."""

    def test_lists_video_files(self, client, tmp_path) -> None:
        (tmp_path / "untaged" / "a.mkv").write_bytes(b"x")
        (tmp_path / "untaged" / "b.mkv").write_bytes(b"x")
        (tmp_path / "untaged" / ".hidden").write_bytes(b"x")

        resp = client.get("/api/videos/untaged")
        assert resp.status_code == 200
        assert resp.json() == {"files": ["a.mkv", "b.mkv"]}

    def test_unknown_dir_404(self, client) -> None:
        resp = client.get("/api/videos/nope")
        assert resp.status_code == 404


class TestPreviewFlow:
    """Tests for the progressive HLS preview endpoints."""

    def test_missing_file_404(self, client) -> None:
        resp = client.get("/api/preview/untaged/nope.mkv")
        assert resp.status_code == 404

    def test_invalid_filename_400(self, client, tmp_path) -> None:
        (tmp_path / "untaged" / "a.mkv").write_bytes(b"x")
        # URL-encoded backslash passes the path segment but fails the name check.
        resp = client.get("/api/preview/untaged/a%5Cb.mkv")
        assert resp.status_code == 400

    def test_serves_ready_playlist_url(self, client, tmp_path) -> None:
        import os

        source: Path = tmp_path / "taged" / "match.mkv"
        source.write_bytes(b"source")
        playlist: Path = transcode.playlist_path(source)
        playlist.parent.mkdir(parents=True, exist_ok=True)
        playlist.write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-ENDLIST\n")
        os.utime(source, (1000, 1000))
        os.utime(playlist, (2000, 2000))

        resp = client.get("/api/preview/taged/match.mkv")
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "ready",
            "playlist": "/api/preview/taged/match.mkv/prog.m3u8",
        }

    def test_serves_playlist_file(self, client, tmp_path) -> None:
        source: Path = tmp_path / "taged" / "match.mkv"
        source.write_bytes(b"source")
        playlist: Path = transcode.playlist_path(source)
        playlist.parent.mkdir(parents=True, exist_ok=True)
        playlist.write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:4.0,\nseg_00000.ts\n")

        resp = client.get("/api/preview/taged/match.mkv/prog.m3u8")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.apple.mpegurl"
        assert resp.headers["cache-control"] == "no-cache"
        assert "#EXTINF" in resp.text

    def test_serves_segment_file(self, client, tmp_path) -> None:
        source: Path = tmp_path / "taged" / "match.mkv"
        source.write_bytes(b"source")
        segment: Path = transcode.segments_dir(source) / "seg_00000.ts"
        segment.parent.mkdir(parents=True, exist_ok=True)
        segment.write_bytes(b"segment-data")

        resp = client.get("/api/preview/taged/match.mkv/seg_00000.ts")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp2t"
        assert resp.content == b"segment-data"

    def test_segment_invalid_name_400(self, client, tmp_path) -> None:
        (tmp_path / "taged" / "match.mkv").write_bytes(b"x")
        resp = client.get("/api/preview/taged/match.mkv/evil.ts")
        assert resp.status_code == 400

    def test_segment_missing_404(self, client, tmp_path) -> None:
        (tmp_path / "taged" / "match.mkv").write_bytes(b"x")
        resp = client.get("/api/preview/taged/match.mkv/seg_00099.ts")
        assert resp.status_code == 404

    def test_starts_transcode_job_when_not_ready(self, client, tmp_path, monkeypatch) -> None:
        source: Path = tmp_path / "taged" / "match.mkv"
        source.write_bytes(b"source")

        # Hold the transcode "running" so we can verify dedup while in flight
        # (avoids running real ffmpeg/ffprobe in the job thread).
        release: threading.Event = threading.Event()

        def fake_transcode_hls(src, job_id, on_progress=None, cancel_event=None) -> None:
            release.wait(timeout=5.0)

        monkeypatch.setattr(transcode, "transcode_hls", fake_transcode_hls)

        resp = client.get("/api/preview/taged/match.mkv")
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "ready",
            "playlist": "/api/preview/taged/match.mkv/prog.m3u8",
        }

        # The stub playlist exists immediately so playback can begin.
        assert transcode.playlist_path(source).is_file()

        # A second request while the job is in flight does not start another one.
        resp2 = client.get("/api/preview/taged/match.mkv")
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "ready"

        transcode_jobs: list[Job] = [
            job for job in web_app.job_manager.list() if job.kind == "transcode"
        ]
        assert len(transcode_jobs) == 1

        release.set()


class TestJobRoutes:
    """Tests for job status and cancellation endpoints."""

    def test_job_status_unknown_404(self, client) -> None:
        resp = client.get("/api/jobs/missing")
        assert resp.status_code == 404

    def test_job_runs_to_completion(self, client) -> None:
        job_id: str = web_app.job_manager.submit("test", lambda ctx: "result")

        deadline: float = time.monotonic() + 5.0
        job: Job | None = None
        while time.monotonic() < deadline:
            job = web_app.job_manager.get(job_id)
            assert job is not None
            if job.status in ("done", "failed", "cancelled"):
                break
            time.sleep(0.01)

        assert job is not None
        assert job.status == "done"
        assert job.result == "result"

    def test_cancel_unknown_job_404(self, client) -> None:
        resp = client.post("/api/jobs/missing/cancel")
        assert resp.status_code == 404


class TestWebSocket:
    """Tests for the /ws event stream (log bridge + job events)."""

    def test_connects_and_receives_connected_event(self, client) -> None:
        with client.websocket_connect("/ws") as ws:
            event = ws.receive_json()
            assert event["type"] == "connected"

    def test_receives_log_events(self, logging_client) -> None:
        with logging_client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume the "connected" event
            logging.getLogger().info("ws-test-log-line")

            found: bool = False
            deadline: float = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                event: dict[str, Any] = ws.receive_json()
                if event["type"] == "log" and "ws-test-log-line" in event["message"]:
                    found = True
                    break
            assert found

    def test_receives_job_events(self, client) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume the "connected" event
            job_id: str = web_app.job_manager.submit("test", lambda ctx: "ok")

            seen: list[str] = []
            deadline: float = time.monotonic() + 5.0
            while time.monotonic() < deadline and set(seen) != {"job_started", "job_finished"}:
                event: dict[str, Any] = ws.receive_json()
                if event.get("id") == job_id and event["type"] in ("job_started", "job_finished"):
                    seen.append(event["type"])
            assert set(seen) == {"job_started", "job_finished"}
