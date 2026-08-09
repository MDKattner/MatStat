"""Tests for web authentication — config, login/logout, session gate, WS."""

import sys
import time
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent))

from scripts.web import auth as auth_module


class TestAuthConfig:
    """Tests for the auth configuration and startup check."""

    def test_disabled_when_matstat_auth_off(self, monkeypatch) -> None:
        monkeypatch.setattr(auth_module, "_AUTH_OFF", True)
        monkeypatch.setattr(auth_module, "_PASSWORD", None)
        assert auth_module.enabled() is False

    def test_enabled_when_password_set(self, monkeypatch) -> None:
        monkeypatch.setattr(auth_module, "_AUTH_OFF", False)
        monkeypatch.setattr(auth_module, "_PASSWORD", "secret")
        assert auth_module.enabled() is True

    def test_check_configured_raises_without_password(self, monkeypatch) -> None:
        monkeypatch.setattr(auth_module, "_AUTH_OFF", False)
        monkeypatch.setattr(auth_module, "_PASSWORD", None)
        with pytest.raises(RuntimeError):
            auth_module.check_configured()

    def test_check_configured_passes_when_disabled(self, monkeypatch) -> None:
        monkeypatch.setattr(auth_module, "_AUTH_OFF", True)
        monkeypatch.setattr(auth_module, "_PASSWORD", None)
        auth_module.check_configured()

    def test_check_configured_passes_with_password(self, monkeypatch) -> None:
        monkeypatch.setattr(auth_module, "_AUTH_OFF", False)
        monkeypatch.setattr(auth_module, "_PASSWORD", "secret")
        auth_module.check_configured()


class TestLogin:
    """Tests for the login/logout API and single-session lock."""

    def test_wrong_password_401(self, auth_client) -> None:
        resp = auth_client.post("/api/auth/login", json={"password": "nope"})
        assert resp.status_code == 401
        assert "Incorrect password" in resp.json()["detail"]

    def test_correct_password_sets_cookie(self, auth_client) -> None:
        resp = auth_client.post("/api/auth/login", json={"password": "test-secret"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert auth_module.COOKIE_NAME in resp.cookies

    def test_second_login_rejected_single_session(self, auth_client) -> None:
        assert (
            auth_client.post("/api/auth/login", json={"password": "test-secret"}).status_code
            == 200
        )
        resp = auth_client.post("/api/auth/login", json={"password": "test-secret"})
        assert resp.status_code == 401
        assert "already active" in resp.json()["detail"]

    def test_logout_clears_session_and_allows_relogin(self, auth_client) -> None:
        auth_client.post("/api/auth/login", json={"password": "test-secret"})
        resp = auth_client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        resp2 = auth_client.post("/api/auth/login", json={"password": "test-secret"})
        assert resp2.status_code == 200

    def test_expired_session_allows_relogin(self, auth_client, monkeypatch) -> None:
        auth_client.post("/api/auth/login", json={"password": "test-secret"})
        monkeypatch.setattr(auth_module, "_CURRENT_SESSION", ("old-id", time.time() - 10))
        resp = auth_client.post("/api/auth/login", json={"password": "test-secret"})
        assert resp.status_code == 200


class TestAuthGate:
    """Tests for the request gate (pages redirect, APIs 401)."""

    def test_login_page_public(self, auth_client) -> None:
        resp = auth_client.get("/login")
        assert resp.status_code == 200
        assert "MatStat" in resp.text
        assert 'id="password"' in resp.text

    def test_login_page_redirects_when_authenticated(self, auth_client) -> None:
        auth_client.post("/api/auth/login", json={"password": "test-secret"})
        resp = auth_client.get("/login", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    def test_unauthenticated_index_redirects_to_login(self, auth_client) -> None:
        resp = auth_client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    def test_unauthenticated_static_redirects_to_login(self, auth_client) -> None:
        resp = auth_client.get("/static/js/app.js", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    def test_unauthenticated_api_401(self, auth_client) -> None:
        resp = auth_client.get("/api/health")
        assert resp.status_code == 401

    def test_unauthenticated_preview_api_401(self, auth_client) -> None:
        resp = auth_client.get("/api/preview/untaged/match.mkv")
        assert resp.status_code == 401

    def test_authenticated_requests_pass(self, auth_client) -> None:
        auth_client.post("/api/auth/login", json={"password": "test-secret"})
        assert auth_client.get("/").status_code == 200
        assert auth_client.get("/api/health").status_code == 200
        assert auth_client.get("/api/config").status_code == 200

    def test_index_includes_logout_button_when_enabled(self, auth_client) -> None:
        auth_client.post("/api/auth/login", json={"password": "test-secret"})
        resp = auth_client.get("/")
        assert 'id="logout-btn"' in resp.text

    def test_tampered_cookie_rejected(self, auth_client, monkeypatch) -> None:
        session_id: str = "forged-session-id"
        monkeypatch.setattr(auth_module, "_CURRENT_SESSION", (session_id, time.time() + 3600))
        good_sig: str = auth_module._sign(session_id)
        tampered_sig: str = "00" if good_sig[-2:] != "00" else "11"
        auth_client.cookies.delete(auth_module.COOKIE_NAME)
        resp = auth_client.get(
            "/api/health",
            headers={"Cookie": f"{auth_module.COOKIE_NAME}={session_id}.{tampered_sig}"},
        )
        assert resp.status_code == 401

        auth_client.cookies.delete(auth_module.COOKIE_NAME)
        resp2 = auth_client.get(
            "/api/health",
            headers={"Cookie": f"{auth_module.COOKIE_NAME}={session_id}.{good_sig}"},
        )
        assert resp2.status_code == 200


class TestWsAuth:
    """Tests for WebSocket authentication."""

    def test_ws_rejected_when_unauthenticated(self, auth_client) -> None:
        with pytest.raises(Exception):
            with auth_client.websocket_connect("/ws"):
                pass

    def test_ws_connects_when_authenticated(self, auth_client) -> None:
        auth_client.post("/api/auth/login", json={"password": "test-secret"})
        with auth_client.websocket_connect("/ws") as ws:
            event = ws.receive_json()
            assert event["type"] == "connected"


class TestAuthDisabled:
    """Tests that the default (no auth) client is unaffected."""

    def test_login_page_redirects_home_when_disabled(self, client) -> None:
        resp = client.get("/login", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    def test_index_omits_logout_button_when_disabled(self, client) -> None:
        resp = client.get("/")
        assert 'id="logout-btn"' not in resp.text

    def test_login_is_noop_when_disabled(self, client) -> None:
        resp = client.post("/api/auth/login", json={"password": "whatever"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
