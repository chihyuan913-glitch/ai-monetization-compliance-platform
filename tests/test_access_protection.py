"""存取保護 (BasicAuthMiddleware) 驗收測試：驗證真實 ASGI 行為，不 mock 中介層。"""
import base64
import os
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import web_app  # noqa: E402


def _basic(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


@pytest.fixture
def client():
    return TestClient(web_app.app)


def test_local_without_password_stays_open(client, monkeypatch):
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    assert client.get("/").status_code == 200


def test_password_set_blocks_anonymous(client, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3cret")
    r = client.get("/api/dashboard-data")
    assert r.status_code == 401
    assert "Basic" in r.headers["www-authenticate"]


def test_wrong_password_rejected(client, monkeypatch):
    monkeypatch.setenv("ACCESS_USER", "boss")
    monkeypatch.setenv("ACCESS_PASSWORD", "s3cret")
    assert client.get("/api/dashboard-data", headers=_basic("boss", "bad")).status_code == 401
    assert client.get("/api/dashboard-data", headers=_basic("other", "s3cret")).status_code == 401


def test_correct_credentials_pass_including_unicode_password(client, monkeypatch):
    monkeypatch.setenv("ACCESS_USER", "boss")
    monkeypatch.setenv("ACCESS_PASSWORD", "密碼:with:colons")
    assert client.get("/api/dashboard-data", headers=_basic("boss", "密碼:with:colons")).status_code == 200


def test_health_always_open_for_render_probe(client, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3cret")
    assert client.get("/health").status_code == 200


def test_cloud_without_password_fails_closed(client, monkeypatch):
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    monkeypatch.setenv("RENDER", "true")
    assert client.get("/").status_code == 503
    assert client.get("/health").status_code == 200


def test_websocket_not_a_bypass(client, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3cret")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/heartbeat"):
            pass
    with client.websocket_connect("/ws/heartbeat", headers=_basic("admin", "s3cret")) as ws:
        ws.send_text("ping")
        assert ws.receive_text() == "pong"
