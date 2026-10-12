#!/usr/bin/env python3
"""Native Termux bridge for hp-ungu.

Exposes a token-authenticated HTTP JSON API bound to 127.0.0.1 that wraps an
explicit allow-list of Termux:API commands plus PM2 and SSH diagnostics.

This process never acts as a general shell proxy.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

BRIDGE_VERSION = "1.0.0"
DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"
MAX_BODY_BYTES = 1_048_576
DEFAULT_MAX_IMAGE_BYTES = 5_242_880
DEFAULT_ENV_PATH = os.path.expanduser("~/hp-ungu-telegram-bot/.env")

PM2_ALLOWED_ACTIONS = {"restart", "reload", "stop", "start"}

_TERMUX_API_COMMANDS: Dict[str, str] = {
    "battery": "termux-battery-status",
    "camera": "termux-camera-photo",
    "clipboard_get": "termux-clipboard-get",
    "clipboard_set": "termux-clipboard-set",
    "device_info": "termux-telephony-deviceinfo",
    "location": "termux-location",
    "microphone": "termux-microphone-record",
    "notification": "termux-notification",
    "toast": "termux-toast",
    "torch": "termux-torch",
    "tts": "termux-tts-speak",
    "vibrate": "termux-vibrate",
    "wifi_connectioninfo": "termux-wifi-connectioninfo",
    "wifi_scaninfo": "termux-wifi-scaninfo",
    "sensor": "termux-sensor",
    "volume": "termux-volume",
    "brightness": "termux-brightness",
}

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.@\- ]{1,120}$")


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class BridgeOpError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #
def load_env_file(path: str) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not os.path.isfile(path):
        return data
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            data[key] = value
    return data


class BridgeConfig:
    def __init__(self) -> None:
        self.env_path = os.environ.get("HP_UNGU_ENV", DEFAULT_ENV_PATH)
        file_env = load_env_file(self.env_path)

        def pick(name: str, default: Optional[str] = None) -> Optional[str]:
            return os.environ.get(name) or file_env.get(name) or default

        self.host = pick("TERMUX_BRIDGE_HOST", DEFAULT_HOST) or DEFAULT_HOST
        try:
            self.port = int(pick("TERMUX_BRIDGE_PORT", str(DEFAULT_PORT)) or DEFAULT_PORT)
        except ValueError:
            self.port = DEFAULT_PORT
        self.token = pick("TERMUX_BRIDGE_TOKEN", "") or ""
        try:
            self.max_image_bytes = int(
                pick("TERMUX_BRIDGE_MAX_IMAGE_BYTES", str(DEFAULT_MAX_IMAGE_BYTES))
                or DEFAULT_MAX_IMAGE_BYTES
            )
        except ValueError:
            self.max_image_bytes = DEFAULT_MAX_IMAGE_BYTES
        self.env_path = self.env_path


# --------------------------------------------------------------------------- #
# Shell helpers
# --------------------------------------------------------------------------- #
def which(command: str) -> Optional[str]:
    return shutil.which(command)


def run_cmd(args: List[str], timeout: float = 20.0,
            input_bytes: Optional[bytes] = None) -> subprocess.CompletedProcess:
    """Run a Termux command with a hard timeout."""
    try:
        return subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            input=input_bytes,
        )
    except FileNotFoundError as exc:
        raise BridgeOpError("unavailable", f"{args[0]} is not installed", status=501) from exc
    except subprocess.TimeoutExpired as exc:
        raise BridgeOpError(
            "timeout", f"{args[0]} did not finish within {timeout:.0f}s", status=504
        ) from exc


def stdout_text(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout.decode("utf-8", "replace").strip()


def stderr_text(proc: subprocess.CompletedProcess) -> str:
    return proc.stderr.decode("utf-8", "replace").strip()


def require_ok(proc: subprocess.CompletedProcess, label: str) -> str:
    if proc.returncode != 0:
        message = stderr_text(proc) or stdout_text(proc) or f"{label} failed"
        raise BridgeOpError("failed", message, status=502)
    return stdout_text(proc)


def try_json(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def getprop(key: str) -> Optional[str]:
    binary = which("getprop") or ("/system/bin/getprop" if os.path.exists("/system/bin/getprop") else None)
    if binary is None:
        return None
    try:
        proc = subprocess.run([binary, key], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    value = stdout_text(proc)
    return value or None


def _require_command(key: str) -> str:
    command = _TERMUX_API_COMMANDS[key]
    if which(command) is None:
        raise BridgeOpError(
            "unavailable",
            f"{command} is not installed. Install the termux-api package and the "
            "Termux:API Android app.",
            status=501,
        )
    return command


# --------------------------------------------------------------------------- #
# Termux:API operations
# --------------------------------------------------------------------------- #
def op_battery(_params: Dict[str, Any]) -> Dict[str, Any]:
    proc = run_cmd([_require_command("battery")], timeout=15)
    payload = try_json(require_ok(proc, "termux-battery-status"))
    if not isinstance(payload, dict):
        raise BridgeOpError("bad_response", "Unexpected battery response", status=502)
    return payload


def op_device(_params: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}

    telephony: Any = None
    if which(_TERMUX_API_COMMANDS["device_info"]):
        try:
            proc = run_cmd([_TERMUX_API_COMMANDS["device_info"]], timeout=20)
            if proc.returncode == 0:
                telephony = try_json(stdout_text(proc))
        except BridgeOpError:
            telephony = None

    if isinstance(telephony, dict):
        result.update(
            {
                "model": telephony.get("model") or telephony.get("device") or telephony.get("market_name"),
                "manufacturer": telephony.get("manufacturer") or telephony.get("brand"),
                "android_version": telephony.get("version") or telephony.get("release"),
                "sdk": telephony.get("sdk") or telephony.get("sdk_int"),
            }
        )

    result.setdefault("model", getprop("ro.product.model"))
    result.setdefault("manufacturer", getprop("ro.product.manufacturer"))
    result.setdefault("brand", getprop("ro.product.brand"))
    result.setdefault("android_version", getprop("ro.build.version.release"))
    result.setdefault("sdk", getprop("ro.build.version.sdk"))
    result.setdefault("device", getprop("ro.product.device"))
    result.setdefault("hardware", getprop("ro.hardware"))
    result["raw_telephony"] = telephony
    result = {k: v for k, v in result.items() if v is not None}
    if not result:
        raise BridgeOpError(
            "unavailable",
            "No device information source is available (telephony API denied and "
            "getprop missing).",
            status=501,
        )
    return result


def op_notification(params: Dict[str, Any]) -> Dict[str, Any]:
    title = str(params.get("title") or "hp-ungu")
    content = str(params.get("content") or "")
    if not content:
        raise BridgeOpError("bad_request", "content is required")
    args = [_require_command("notification"), "--title", title, "--content", content]
    notify_id = params.get("id")
    if notify_id is not None:
        args += ["--id", str(int(notify_id))]
    proc = run_cmd(args, timeout=20)
    require_ok(proc, "termux-notification")
    return {"delivered": True, "title": title}


def op_vibrate(params: Dict[str, Any]) -> Dict[str, Any]:
    try:
        milliseconds = int(params.get("milliseconds", 500))
    except (TypeError, ValueError):
        raise BridgeOpError("bad_request", "milliseconds must be an integer")
    milliseconds = max(1, min(60_000, milliseconds))
    proc = run_cmd([_require_command("vibrate"), "-d", str(milliseconds)], timeout=20)
    require_ok(proc, "termux-vibrate")
    return {"vibrated_ms": milliseconds}


def op_torch(params: Dict[str, Any]) -> Dict[str, Any]:
    state = str(params.get("state", "toggle")).lower()
    if state not in {"on", "off", "toggle"}:
        raise BridgeOpError("bad_request", "state must be on, off or toggle")
    proc = run_cmd([_require_command("torch"), state], timeout=20)
    require_ok(proc, "termux-torch")
    return {"torch": state}


def op_clipboard_get(_params: Dict[str, Any]) -> Dict[str, Any]:
    proc = run_cmd([_require_command("clipboard_get")], timeout=15)
    text = require_ok(proc, "termux-clipboard-get")
    return {"text": text[:100_000], "truncated": len(text) > 100_000}


def op_clipboard_set(params: Dict[str, Any]) -> Dict[str, Any]:
    text = str(params.get("text") or "")
    if len(text.encode("utf-8")) > 200_000:
        raise BridgeOpError("too_large", "Clipboard payload is too large")
    proc = run_cmd(
        [_require_command("clipboard_set")], timeout=15, input_bytes=text.encode("utf-8")
    )
    require_ok(proc, "termux-clipboard-set")
    return {"written": len(text)}


def _read_and_encode(path: str, max_bytes: int, mime: str, extra: Dict[str, Any]) -> Dict[str, Any]:
    size = os.path.getsize(path)
    if size == 0:
        raise BridgeOpError("empty", "The captured file is empty", status=502)
    if size > max_bytes:
        raise BridgeOpError(
            "too_large",
            f"Captured file is {size} bytes, above the {max_bytes} byte bridge limit.",
            status=413,
        )
    with open(path, "rb") as handle:
        data = handle.read()
    payload = {"mime": mime, "size": size, "data_b64": base64.b64encode(data).decode("ascii")}
    payload.update(extra)
    return payload


def op_camera(params: Dict[str, Any], config: "BridgeConfig") -> Dict[str, Any]:
    try:
        camera_id = int(params.get("camera", 0))
    except (TypeError, ValueError):
        raise BridgeOpError("bad_request", "camera must be an integer")
    fd, path = tempfile.mkstemp(suffix=".jpg", prefix="hp-ungu-cam-")
    os.close(fd)
    try:
        proc = run_cmd([_require_command("camera"), "-c", str(camera_id), path], timeout=60)
        require_ok(proc, "termux-camera-photo")
        if not os.path.exists(path):
            raise BridgeOpError(
                "failed",
                "Camera produced no file. Check the camera permission for Termux:API.",
                status=502,
            )
        return _read_and_encode(path, config.max_image_bytes, "image/jpeg", {"camera": camera_id})
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def op_microphone(params: Dict[str, Any], config: "BridgeConfig") -> Dict[str, Any]:
    try:
        seconds = int(params.get("seconds", 5))
    except (TypeError, ValueError):
        raise BridgeOpError("bad_request", "seconds must be an integer")
    seconds = max(1, min(120, seconds))
    encoder = str(params.get("encoder", "aac"))
    if encoder not in {"aac", "amr", "opus"}:
        raise BridgeOpError("bad_request", "encoder must be aac, amr or opus")

    suffix = {"aac": ".m4a", "amr": ".amr", "opus": ".ogg"}[encoder]
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="hp-ungu-rec-")
    os.close(fd)
    try:
        proc = run_cmd(
            [_require_command("microphone"), "-f", path, "-l", str(seconds), "-e", encoder],
            timeout=seconds + 30,
        )
        if proc.returncode != 0:
            raise BridgeOpError(
                "failed",
                stderr_text(proc)
                or "termux-microphone-record failed. Check the microphone permission.",
                status=502,
            )
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise BridgeOpError(
                "failed",
                "No audio was recorded. Grant the microphone permission to Termux:API "
                "and ensure the app is allowed in the background.",
                status=502,
            )
        mime = {"aac": "audio/mp4", "amr": "audio/amr", "opus": "audio/ogg"}[encoder]
        return _read_and_encode(path, config.max_image_bytes, mime, {"seconds": seconds})
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def op_location(params: Dict[str, Any]) -> Dict[str, Any]:
    provider = str(params.get("provider", "network")).lower()
    if provider not in {"gps", "network", "passive"}:
        raise BridgeOpError("bad_request", "provider must be gps, network or passive")
    proc = run_cmd([_require_command("location"), "-p", provider], timeout=60)
    payload = try_json(require_ok(proc, "termux-location"))
    if not isinstance(payload, dict):
        raise BridgeOpError("bad_response", "Unexpected location response", status=502)
    return payload


def op_wifi(_params: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if which(_TERMUX_API_COMMANDS["wifi_connectioninfo"]):
        try:
            proc = run_cmd([_TERMUX_API_COMMANDS["wifi_connectioninfo"]], timeout=25)
            if proc.returncode == 0:
                info = try_json(stdout_text(proc))
                if isinstance(info, dict):
                    result.update(info)
        except BridgeOpError as exc:
            result["connection_error"] = exc.message
    if not result:
        raise BridgeOpError(
            "unavailable",
            "termux-wifi-connectioninfo returned nothing. Android 10+ may require "
            "location permission for Wi-Fi details.",
            status=501,
        )
    return result


def op_toast(params: Dict[str, Any]) -> Dict[str, Any]:
    text = str(params.get("text") or "")
    if not text:
        raise BridgeOpError("bad_request", "text is required")
    proc = run_cmd([_require_command("toast"), text[:1000]], timeout=15)
    require_ok(proc, "termux-toast")
    return {"shown": True}


def op_tts(params: Dict[str, Any]) -> Dict[str, Any]:
    text = str(params.get("text") or "")
    if not text:
        raise BridgeOpError("bad_request", "text is required")
    proc = run_cmd([_require_command("tts"), text[:2000]], timeout=30)
    require_ok(proc, "termux-tts-speak")
    return {"spoken": True}


def op_features(_params: Dict[str, Any]) -> Dict[str, Any]:
    available = {key: which(cmd) is not None for key, cmd in _TERMUX_API_COMMANDS.items()}
    available["pm2"] = which("pm2") is not None
    available["tailscale"] = which("tailscale") is not None
    unsupported = [k for k, v in available.items() if not v]
    return {"available": available, "unsupported": unsupported, "version": BRIDGE_VERSION}


# --------------------------------------------------------------------------- #
# PM2
# --------------------------------------------------------------------------- #
def _pm2_payload() -> List[Dict[str, Any]]:
    proc = run_cmd(["pm2", "jlist"], timeout=30)
    text = stdout_text(proc)
    start = text.find("[")
    if proc.returncode != 0 or start < 0:
        raise BridgeOpError(
            "failed",
            stderr_text(proc) or "pm2 jlist produced no JSON. Is the PM2 daemon running?",
            status=502,
        )
    try:
        data = json.loads(text[start:])
    except ValueError as exc:
        raise BridgeOpError("bad_response", f"Cannot parse pm2 output: {exc}", status=502) from exc
    if not isinstance(data, list):
        raise BridgeOpError("bad_response", "Unexpected pm2 output shape", status=502)
    return data


def op_pm2_list(_params: Dict[str, Any]) -> Dict[str, Any]:
    if which("pm2") is None:
        raise BridgeOpError("unavailable", "pm2 is not installed in Termux", status=501)
    processes = []
    for entry in _pm2_payload():
        env = entry.get("pm2_env") or {}
        monit = entry.get("monit") or {}
        uptime = env.get("pm_uptime")
        processes.append(
            {
                "name": env.get("name"),
                "pm_id": entry.get("pm_id"),
                "pid": entry.get("pid"),
                "status": env.get("status"),
                "cpu": monit.get("cpu"),
                "memory": monit.get("memory"),
                "restarts": env.get("restart_time"),
                "uptime_ms": (int(time.time() * 1000) - uptime) if uptime else None,
                "script": env.get("pm_exec_path"),
                "exit_code": env.get("exit_code"),
            }
        )
    return {"available": True, "processes": processes}


def op_pm2_action(params: Dict[str, Any]) -> Dict[str, Any]:
    action = str(params.get("action") or "").lower()
    name = str(params.get("name") or "")
    if action not in PM2_ALLOWED_ACTIONS:
        raise BridgeOpError("bad_request", f"action must be one of {sorted(PM2_ALLOWED_ACTIONS)}")
    if not _SAFE_NAME.match(name):
        raise BridgeOpError("bad_request", "Invalid PM2 process name")

    known = {p.get("name") for p in op_pm2_list({})["processes"]}
    if name not in known:
        raise BridgeOpError("not_found", f"No PM2 process named {name!r}", status=404)

    proc = run_cmd(["pm2", action, name], timeout=45)
    require_ok(proc, f"pm2 {action}")
    return {"action": action, "name": name, "output": stdout_text(proc)}


def op_pm2_logs(params: Dict[str, Any]) -> Dict[str, Any]:
    name = str(params.get("name") or "")
    if not _SAFE_NAME.match(name):
        raise BridgeOpError("bad_request", "Invalid PM2 process name")
    try:
        lines = int(params.get("lines", 40))
    except (TypeError, ValueError):
        lines = 40
    lines = max(5, min(300, lines))
    proc = run_cmd(["pm2", "logs", name, "--lines", str(lines), "--nostream"], timeout=30)
    output = (stdout_text(proc) or "") + ("\n" + stderr_text(proc) if stderr_text(proc) else "")
    if proc.returncode != 0 and not output.strip():
        raise BridgeOpError("failed", "pm2 logs produced no output", status=502)
    return {"name": name, "lines": lines, "output": output[:200_000]}


# --------------------------------------------------------------------------- #
# SSH diagnostics
# --------------------------------------------------------------------------- #
def _proc_cmdlines() -> List[tuple[int, str]]:
    results: List[tuple[int, str]] = []
    proc_root = "/proc"
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return results
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"{proc_root}/{entry}/cmdline", "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        if cmdline:
            results.append((int(entry), cmdline))
    return results


def _port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _tailscale_info() -> Dict[str, Any]:
    binary = which("tailscale")
    if binary is None:
        for candidate in (
            "/data/data/com.termux/files/usr/bin/tailscale",
            "/system/bin/tailscale",
        ):
            if os.path.exists(candidate):
                binary = candidate
                break
    if binary is None:
        return {"status": "not-installed", "ip": None, "hostname": None}

    try:
        proc = run_cmd([binary, "status", "--json"], timeout=15)
    except BridgeOpError as exc:
        return {"status": f"error: {exc.message}", "ip": None, "hostname": None}

    if proc.returncode != 0:
        return {
            "status": stderr_text(proc) or "unavailable",
            "ip": None,
            "hostname": None,
        }
    try:
        payload = json.loads(stdout_text(proc))
    except ValueError:
        return {"status": "unparseable", "ip": None, "hostname": None}

    self_info = payload.get("Self") or {}
    ips = self_info.get("TailscaleIPs") or []
    ipv4 = next((ip for ip in ips if ":" not in ip), None)
    backend = payload.get("BackendState") or "unknown"
    return {
        "status": backend,
        "ip": ipv4,
        "hostname": self_info.get("DNSName", "").rstrip(".") or None,
    }


def op_ssh_status(params: Dict[str, Any]) -> Dict[str, Any]:
    try:
        port = int(params.get("port", 8022))
    except (TypeError, ValueError):
        port = 8022

    cmdlines = _proc_cmdlines()
    sshd_pids = [pid for pid, cmd in cmdlines if "sshd" in cmd and "grep" not in cmd]

    pm2_sshd_status = None
    if which("pm2"):
        try:
            for entry in op_pm2_list({})["processes"]:
                if "ssh" in (entry.get("name") or "").lower():
                    pm2_sshd_status = f"{entry.get('name')}: {entry.get('status')}"
                    break
        except BridgeOpError:
            pm2_sshd_status = "pm2 unavailable"

    tailscale = _tailscale_info()
    hostname = None
    try:
        hostname = socket.gethostname()
    except OSError:
        pass

    return {
        "available": True,
        "sshd_running": bool(sshd_pids),
        "sshd_pids": sshd_pids,
        "configured_port": port,
        "port_open": _port_open("127.0.0.1", port),
        "pm2_sshd_status": pm2_sshd_status,
        "tailscale_status": tailscale["status"],
        "tailscale_ip": tailscale["ip"],
        "hostname": tailscale["hostname"] or hostname,
        "device_name": getprop("net.hostname") or hostname,
    }


def op_health(_params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "ok",
        "version": BRIDGE_VERSION,
        "uptime": time.time() - START_TIME,
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "termux_prefix": os.environ.get("PREFIX"),
    }


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
OpFunc = Callable[..., Dict[str, Any]]

GET_ROUTES: Dict[str, OpFunc] = {
    "/health": op_health,
    "/api/v1/features": op_features,
    "/api/v1/battery": op_battery,
    "/api/v1/device": op_device,
    "/api/v1/clipboard": op_clipboard_get,
    "/api/v1/location": op_location,
    "/api/v1/wifi": op_wifi,
    "/api/v1/pm2": op_pm2_list,
    "/api/v1/pm2/logs": op_pm2_logs,
    "/api/v1/ssh/status": op_ssh_status,
}

POST_ROUTES: Dict[str, OpFunc] = {
    "/api/v1/notification": op_notification,
    "/api/v1/vibrate": op_vibrate,
    "/api/v1/torch": op_torch,
    "/api/v1/clipboard": op_clipboard_set,
    "/api/v1/camera": op_camera,
    "/api/v1/microphone": op_microphone,
    "/api/v1/toast": op_toast,
    "/api/v1/tts": op_tts,
    "/api/v1/pm2/action": op_pm2_action,
}

NEEDS_CONFIG = {op_camera, op_microphone}


class BridgeApp:
    """Transport-independent request dispatcher (easy to unit test)."""

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config

    def dispatch(
        self,
        method: str,
        path: str,
        query: Dict[str, List[str]],
        body: Dict[str, Any],
        headers: Dict[str, str],
    ) -> tuple[int, Dict[str, Any]]:
        token = headers.get("x-auth-token", "")
        if not self.config.token:
            return 500, {"error": "Bridge token is not configured", "code": "no_token"}
        if not hmac.compare_digest(token, self.config.token):
            return 401, {"error": "Unauthorized", "code": "unauthorized"}

        method = method.upper()
        routes = GET_ROUTES if method == "GET" else POST_ROUTES if method == "POST" else {}
        if not routes:
            return 405, {"error": "Method not allowed", "code": "method_not_allowed"}
        handler = routes.get(path)
        if handler is None:
            return 404, {"error": f"No such endpoint: {path}", "code": "not_found"}

        params: Dict[str, Any] = {}
        for key, values in query.items():
            if values:
                params[key] = values[0]
        if isinstance(body, dict):
            params.update(body)

        try:
            if handler in NEEDS_CONFIG:
                result = handler(params, self.config)
            else:
                result = handler(params)
        except BridgeOpError as exc:
            return exc.status, {"error": exc.message, "code": exc.code}
        except Exception as exc:  # noqa: BLE001 - never leak a traceback over HTTP
            return 500, {"error": f"{type(exc).__name__}: {exc}", "code": "internal_error"}

        if isinstance(result, dict) and "error" in result and len(result) == 1:
            return 502, {"error": str(result["error"]), "code": "operation_error"}
        return 200, result


def make_handler(app: BridgeApp, logger=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"hpUnguBridge/{BRIDGE_VERSION}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            if logger is not None:
                logger(f"{self.address_string()} - {fmt % args}")

        # -------------------------------------------------------- responses
        def _send(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _read_body(self) -> Dict[str, Any]:
            length_header = self.headers.get("Content-Length")
            if not length_header:
                return {}
            try:
                length = int(length_header)
            except ValueError:
                raise BridgeOpError("bad_request", "Invalid Content-Length")
            if length <= 0:
                return {}
            if length > MAX_BODY_BYTES:
                raise BridgeOpError("too_large", "Request body is too large", status=413)
            raw = self.rfile.read(length)
            if not raw:
                return {}
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise BridgeOpError("bad_request", f"Invalid JSON body: {exc}")
            if not isinstance(payload, dict):
                raise BridgeOpError("bad_request", "JSON body must be an object")
            return payload

        # ---------------------------------------------------------- handlers
        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def _handle(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            try:
                body = self._read_body()
            except BridgeOpError as exc:
                self._send(exc.status, {"error": exc.message, "code": exc.code})
                return

            headers = {k.lower(): v for k, v in self.headers.items()}
            status, payload = app.dispatch(method, path, query, body, headers)
            self._send(status, payload)

    return Handler


START_TIME = time.time()


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    config = BridgeConfig()

    if not config.token or "REPLACE_WITH" in config.token:
        print(
            "[hp-ungu-bridge] TERMUX_BRIDGE_TOKEN is missing or still a placeholder.\n"
            f"[hp-ungu-bridge] Looked at env HP_UNGU_ENV and {config.env_path}.\n"
            "[hp-ungu-bridge] Run termux/install.sh or set the variable manually.",
            file=sys.stderr,
        )
        return 2

    if config.host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            f"[hp-ungu-bridge] Refusing to bind to {config.host!r}. "
            "Only loopback addresses are permitted.",
            file=sys.stderr,
        )
        return 2

    app = BridgeApp(config)

    def logger(message: str) -> None:
        print(f"[hp-ungu-bridge] {message}", flush=True)

    httpd = ThreadingHTTPServer((config.host, config.port), make_handler(app, logger))
    httpd.daemon_threads = True
    logger(f"listening on http://{config.host}:{config.port} (pid {os.getpid()})")
    logger(f"env file: {config.env_path}")

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        logger("interrupted, shutting down")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
