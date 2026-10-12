"""Bridge tests. No real Termux:API commands are executed."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from termux import bridge_server as bs


class FakeConfig(bs.BridgeConfig):
    def __init__(self, token: str = "test-token", host: str = "127.0.0.1",
                 port: int = 0) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.max_image_bytes = 1024
        self.env_path = "<test>"


@pytest.fixture()
def app():
    return bs.BridgeApp(FakeConfig())


def _call(app, method, path, *, token="test-token", body=None, query=None):
    headers = {"x-auth-token": token} if token is not None else {}
    return app.dispatch(method, path, query or {}, body or {}, headers)


def test_missing_token_is_rejected(app):
    status, payload = _call(app, "GET", "/health", token=None)
    assert status == 401
    assert payload["code"] == "unauthorized"


def test_wrong_token_is_rejected(app):
    status, payload = _call(app, "GET", "/health", token="nope")
    assert status == 401


def test_no_configured_token_is_rejected():
    app = bs.BridgeApp(FakeConfig(token=""))
    status, payload = app.dispatch("GET", "/health", {}, {}, {"x-auth-token": ""})
    assert status == 500
    assert payload["code"] == "no_token"


def test_health_ok(app):
    status, payload = _call(app, "GET", "/health")
    assert status == 200
    assert payload["status"] == "ok"
    assert "version" in payload


def test_unknown_endpoint(app):
    status, payload = _call(app, "GET", "/api/v1/nope")
    assert status == 404
    assert payload["code"] == "not_found"


def test_method_not_allowed(app):
    status, payload = _call(app, "DELETE", "/health")
    assert status == 405


def test_get_on_post_only_route_is_404(app):
    status, _payload = _call(app, "GET", "/api/v1/vibrate")
    assert status == 404


def test_post_on_get_only_route_is_404(app):
    status, _payload = _call(app, "POST", "/api/v1/battery", body={})
    assert status == 404


def test_op_error_is_mapped_to_status(app, monkeypatch):
    def boom(_params):
        raise bs.BridgeOpError("unavailable", "termux-camera-photo is not installed", 501)

    monkeypatch.setitem(bs.GET_ROUTES, "/api/v1/battery", boom)
    status, payload = _call(app, "GET", "/api/v1/battery")
    assert status == 501
    assert payload["code"] == "unavailable"


def test_unexpected_exception_is_500(app, monkeypatch):
    def boom(_params):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(bs.GET_ROUTES, "/api/v1/battery", boom)
    status, payload = _call(app, "GET", "/api/v1/battery")
    assert status == 500
    assert payload["code"] == "internal_error"


def test_query_params_are_forwarded(app, monkeypatch):
    captured = {}

    def fake(params):
        captured.update(params)
        return {"ok": True}

    monkeypatch.setitem(bs.GET_ROUTES, "/api/v1/location", fake)
    status, _ = _call(app, "GET", "/api/v1/location", query={"provider": ["gps"]})
    assert status == 200
    assert captured["provider"] == "gps"


def test_body_params_are_forwarded(app, monkeypatch):
    captured = {}

    def fake(params):
        captured.update(params)
        return {"ok": True}

    monkeypatch.setitem(bs.POST_ROUTES, "/api/v1/vibrate", fake)
    status, _ = _call(app, "POST", "/api/v1/vibrate", body={"milliseconds": 250})
    assert status == 200
    assert captured["milliseconds"] == 250


def test_torch_validates_state(app):
    # Without termux-torch installed this raises 'unavailable', but invalid
    # input must be rejected before any command is executed.
    status, payload = _call(app, "POST", "/api/v1/torch", body={"state": "explode"})
    assert status == 400
    assert payload["code"] == "bad_request"


def test_pm2_action_rejects_unknown_action(app):
    status, payload = _call(
        app, "POST", "/api/v1/pm2/action", body={"action": "rm -rf", "name": "x"}
    )
    assert status == 400
    assert payload["code"] == "bad_request"


def test_pm2_action_rejects_shell_metacharacters(app):
    status, payload = _call(
        app,
        "POST",
        "/api/v1/pm2/action",
        body={"action": "restart", "name": "a; rm -rf /"},
    )
    assert status == 400


def test_load_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nA=1\nB=\"quoted value\"\nC='single'\nD=\n",
        encoding="utf-8",
    )
    data = bs.load_env_file(str(env))
    assert data["A"] == "1"
    assert data["B"] == "quoted value"
    assert data["C"] == "single"
    assert data["D"] == ""


def test_http_roundtrip(app):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), bs.make_handler(app))
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        request = urllib.request.Request(
            f"http://{host}:{port}/health", headers={"X-Auth-Token": "test-token"}
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
        assert payload["status"] == "ok"

        bad = urllib.request.Request(
            f"http://{host}:{port}/health", headers={"X-Auth-Token": "nope"}
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(bad, timeout=5)
        assert excinfo.value.code == 401
    finally:
        httpd.shutdown()
        httpd.server_close()
