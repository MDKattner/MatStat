"""Tests for the web app — routes, static serving, WebSocket, preview flow."""

import asyncio
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


class TestDeploymentHardening:
    """Tests for public probes, security headers, CSRF, and upload guards."""

    def test_liveness_is_public_and_minimal(self, client) -> None:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_readiness_reports_checks(self, client) -> None:
        resp = client.get("/readyz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["config"] == "ok"
        assert body["checks"]["data_root"] == "ok"

    def test_readiness_503_when_data_root_unwritable(self, client, monkeypatch) -> None:
        monkeypatch.setattr(web_app, "_dir_writable", lambda _path: False)
        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["checks"]["data_root"] == "error"

    def test_security_headers_on_api(self, client) -> None:
        resp = client.get("/api/health")
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["referrer-policy"] == "no-referrer"
        assert "script-src 'self'" in resp.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
        # Plain HTTP must not send HSTS.
        assert "strict-transport-security" not in resp.headers

    def test_security_headers_cover_redirects(self, auth_client) -> None:
        resp = auth_client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_cross_origin_write_rejected(self, client) -> None:
        resp = client.post(
            "/api/auth/login",
            json={"password": "x"},
            headers={"Origin": "https://evil.example"},
        )
        assert resp.status_code == 403
        assert "Cross-origin" in resp.json()["detail"]

    def test_same_origin_write_allowed(self, client) -> None:
        resp = client.post(
            "/api/auth/login",
            json={"password": "x"},
            headers={"Origin": "http://testserver"},
        )
        assert resp.status_code == 200

    def test_oversized_request_413(self, client, monkeypatch) -> None:
        monkeypatch.setattr(web_app, "_REQUEST_MAX_BYTES", 10)
        resp = client.post(
            "/api/videos",
            files=[("files", ("a.mkv", b"x" * 100, "video/x-matroska"))],
        )
        assert resp.status_code == 413

    def test_upload_rejects_unknown_extension(self, client) -> None:
        resp = client.post(
            "/api/videos",
            files=[("files", ("notes.txt", b"hello", "text/plain"))],
        )
        assert resp.status_code == 200
        result = resp.json()["results"][0]
        assert result["status"] == "error"
        assert "Unsupported video type" in result["detail"]

    def test_upload_rejects_unreadable_video(self, client, monkeypatch) -> None:
        monkeypatch.setattr(web_app, "_is_valid_video", lambda _path: False)
        resp = client.post(
            "/api/videos",
            files=[("files", ("a.mkv", b"not-a-video", "video/x-matroska"))],
        )
        result = resp.json()["results"][0]
        assert result["status"] == "error"
        assert result["detail"] == "File is not a readable video"

    def test_upload_enforces_per_file_size(self, client, monkeypatch) -> None:
        monkeypatch.setattr(web_app, "_UPLOAD_MAX_BYTES", 4)
        resp = client.post(
            "/api/videos",
            files=[("files", ("a.mkv", b"x" * 100, "video/x-matroska"))],
        )
        result = resp.json()["results"][0]
        assert result["status"] == "error"
        assert "upload limit" in result["detail"]
        assert not (web_app._UPLOAD_DIR / "a.mkv").exists()

    def test_upload_accepts_valid_video(self, client, monkeypatch) -> None:
        monkeypatch.setattr(web_app, "_is_valid_video", lambda _path: True)
        resp = client.post(
            "/api/videos",
            files=[("files", ("a.mkv", b"video-bytes", "video/x-matroska"))],
        )
        body = resp.json()
        assert body["uploaded"] == 1
        assert body["results"][0]["status"] == "ok"
        assert (web_app._UPLOAD_DIR / "a.mkv").read_bytes() == b"video-bytes"

    def test_upload_collision_is_case_insensitive(self, client, monkeypatch) -> None:
        monkeypatch.setattr(web_app, "_is_valid_video", lambda _path: True)
        (web_app._UPLOAD_DIR / "Match.MKV").write_bytes(b"x")
        resp = client.post(
            "/api/videos",
            files=[("files", ("match.mkv", b"y", "video/x-matroska"))],
        )
        result = resp.json()["results"][0]
        assert result["status"] == "conflict"

    def test_upload_caps_file_count(self, client, monkeypatch) -> None:
        monkeypatch.setattr(web_app, "_MAX_UPLOAD_FILES", 1)
        resp = client.post(
            "/api/videos",
            files=[
                ("files", ("a.mkv", b"a", "video/x-matroska")),
                ("files", ("b.mkv", b"b", "video/x-matroska")),
            ],
        )
        assert resp.status_code == 400
        assert "Too many files" in resp.json()["detail"]


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

    def test_lists_taged_and_clips(self, client, tmp_path) -> None:
        (tmp_path / "taged" / "wrestler_match.mkv").write_bytes(b"x")
        (tmp_path / "clips" / "reel.mkv").write_bytes(b"x")

        taged_resp = client.get("/api/videos/taged")
        assert taged_resp.status_code == 200
        assert taged_resp.json() == {"files": ["wrestler_match.mkv"]}

        clips_resp = client.get("/api/videos/clips")
        assert clips_resp.status_code == 200
        assert clips_resp.json() == {"files": ["reel.mkv"]}

    def test_missing_taged_dir_empty(self, client, tmp_path) -> None:
        (tmp_path / "untaged").mkdir(parents=True, exist_ok=True)
        resp = client.get("/api/videos/clips")
        assert resp.status_code == 200
        assert resp.json() == {"files": []}


class TestDeleteVideo:
    """Tests for DELETE /api/videos/{dir}/{file} and reel deletion."""

    def test_deletes_tagged_video_and_preview(self, client, tmp_path) -> None:
        video: Path = tmp_path / "taged" / "a.mkv"
        video.write_bytes(b"x")
        transcode.prepare_hls(video)

        resp = client.delete("/api/videos/taged/a.mkv")

        assert resp.status_code == 200
        assert resp.json() == {
            "deleted": "a.mkv", "needs_recompile": True, "job_id": None,
        }
        assert not video.exists()
        assert not transcode.hls_dir_path(video).exists()

    def test_untagged_delete_does_not_request_recompile(self, client, tmp_path) -> None:
        video: Path = tmp_path / "untaged" / "a.mkv"
        video.write_bytes(b"x")

        resp = client.delete("/api/videos/untaged/a.mkv")

        assert resp.status_code == 200
        assert resp.json()["needs_recompile"] is False
        assert not video.exists()

    def test_unknown_dir_404(self, client) -> None:
        assert client.delete("/api/videos/nope/a.mkv").status_code == 404

    def test_missing_file_404(self, client) -> None:
        assert client.delete("/api/videos/taged/nope.mkv").status_code == 404

    def test_invalid_name_400(self, client) -> None:
        resp = client.delete("/api/videos/taged/a%5Cb.mkv")
        assert resp.status_code == 400

    def test_deletes_reel(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(web_app, "clips_dir", tmp_path / "clips")
        reel: Path = tmp_path / "clips" / "reel.mkv"
        reel.write_bytes(b"x")

        resp = client.delete("/api/clips/reel.mkv")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": "reel.mkv"}
        assert not reel.exists()

    def test_missing_reel_404(self, client, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(web_app, "clips_dir", tmp_path / "clips")
        assert client.delete("/api/clips/nope.mkv").status_code == 404


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
        started_jobs: list[str] = []

        def fake_transcode_hls(src, job_id, on_progress=None, cancel_event=None) -> None:
            started_jobs.append(job_id)
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

        assert len(started_jobs) == 1
        transcode_job: Job | None = web_app.job_manager.get(started_jobs[0])
        assert transcode_job is not None
        assert transcode_job.kind == "transcode"

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

    def test_cancel_job_returns_200(self, client) -> None:
        job_id: str = web_app.job_manager.submit(
            "test", lambda ctx: ctx.cancel_event.wait(5.0)
        )
        resp = client.post(f"/api/jobs/{job_id}/cancel")
        assert resp.status_code == 200
        assert resp.json() == {"cancelled": True}

        deadline: float = time.monotonic() + 5.0
        job: Job | None = None
        while time.monotonic() < deadline:
            job = web_app.job_manager.get(job_id)
            assert job is not None
            if job.status == "cancelled":
                break
            time.sleep(0.01)

        assert job is not None
        assert job.status == "cancelled"


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

    def test_disconnect_without_events_returns(self, monkeypatch) -> None:
        """A closed socket must end the handler even with no queued events.

        Regression: the endpoint used to await only ``queue.get()``, so a
        client disconnect was never observed. uvicorn then waited for the
        handler task forever during graceful shutdown.
        """

        class FakeSocket:
            def __init__(self) -> None:
                self.cookies: dict[str, str] = {}
                self.sent: list[dict[str, Any]] = []

            async def accept(self) -> None:
                return None

            async def send_json(self, payload: dict[str, Any]) -> None:
                self.sent.append(payload)

            async def receive(self) -> dict[str, Any]:
                await asyncio.sleep(0.05)
                return {"type": "websocket.disconnect", "code": 1000}

        monkeypatch.setattr(web_app.auth, "enabled", lambda: False)
        socket: FakeSocket = FakeSocket()

        async def run() -> None:
            await asyncio.wait_for(web_app.websocket_endpoint(socket), timeout=1.0)

        asyncio.run(run())
        assert socket.sent == [{"type": "connected", "status": "ok"}]

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
