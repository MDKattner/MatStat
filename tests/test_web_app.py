"""Tests for the web app — routes, static serving, WebSocket, preview flow."""

import logging
import sys
import time
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).parent.parent))

from scripts.web import app as web_app
from scripts.web.jobs import Job
from scripts.web.transcode import preview_cache_path


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
    """Tests for the transcode-on-demand preview endpoint."""

    def test_missing_file_404(self, client) -> None:
        resp = client.get("/api/preview/untaged/nope.mkv")
        assert resp.status_code == 404

    def test_invalid_filename_400(self, client, tmp_path) -> None:
        (tmp_path / "untaged" / "a.mkv").write_bytes(b"x")
        # URL-encoded backslash passes the path segment but fails the name check.
        resp = client.get("/api/preview/untaged/a%5Cb.mkv")
        assert resp.status_code == 400

    def test_serves_fresh_cache_as_mp4(self, client, tmp_path) -> None:
        import os

        source: Path = tmp_path / "taged" / "match.mkv"
        source.write_bytes(b"source")
        cache: Path = preview_cache_path(source)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(b"encoded")
        os.utime(source, (1000, 1000))
        os.utime(cache, (2000, 2000))

        resp = client.get("/api/preview/taged/match.mkv")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"
        assert resp.content == b"encoded"

    def test_starts_transcode_job_when_no_cache(self, client, tmp_path, monkeypatch) -> None:
        source: Path = tmp_path / "taged" / "match.mkv"
        source.write_bytes(b"source")

        # Avoid running real ffmpeg in the transcode job.
        def fake_ensure_preview(src, on_progress=None, cancel_event=None) -> None:
            return None

        monkeypatch.setattr(web_app, "ensure_preview", fake_ensure_preview)

        resp = client.get("/api/preview/taged/match.mkv")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["status"] == "transcoding"
        assert "job_id" in body

        # The transcode job exists and is queryable.
        job_resp = client.get(f"/api/jobs/{body['job_id']}")
        assert job_resp.status_code == 200
        assert job_resp.json()["kind"] == "transcode"


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

    def test_receives_log_events(self, client) -> None:
        with client.websocket_connect("/ws") as ws:
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
