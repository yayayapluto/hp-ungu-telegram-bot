"""Async client for the native Termux bridge."""
from __future__ import annotations

import base64
from typing import Any, Dict, Optional

import httpx

from ubuntu.config import Settings
from ubuntu.utils.logging_config import get_logger

log = get_logger(__name__)


class BridgeError(RuntimeError):
    """Raised when the bridge returns an error or is unreachable."""

    def __init__(self, message: str, code: str = "error", status: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class TermuxBridgeClient:
    """Thin, typed wrapper over the bridge HTTP API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.bridge_base_url,
            headers={"X-Auth-Token": settings.termux_bridge_token},
            timeout=httpx.Timeout(settings.termux_bridge_timeout_seconds),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- plumbing
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        try:
            response = await self._client.request(
                method, path, json=json_body, timeout=timeout
            )
        except httpx.ConnectError as exc:
            raise BridgeError(
                "Cannot reach the Termux bridge on "
                f"{self._settings.bridge_base_url} ({exc}). "
                "Check that PM2 process hp-ungu-bridge is online.",
                code="unreachable",
            ) from exc
        except httpx.TimeoutException as exc:
            raise BridgeError(
                f"Bridge request {path} timed out.", code="timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise BridgeError(f"Bridge transport error: {exc}", code="transport") from exc

        try:
            payload = response.json()
        except ValueError:
            raise BridgeError(
                f"Bridge returned non-JSON response (HTTP {response.status_code}).",
                code="bad_response",
                status=response.status_code,
            )

        if response.status_code >= 400:
            raise BridgeError(
                str(payload.get("error", "bridge error")),
                code=str(payload.get("code", "error")),
                status=response.status_code,
            )
        return payload

    # ------------------------------------------------------------------ health
    async def health(self) -> Dict[str, Any]:
        return await self._request("GET", "/health", timeout=10)

    async def features(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/features", timeout=20)

    # --------------------------------------------------------------- android
    async def battery(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/battery", timeout=20)

    async def device_info(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/device", timeout=25)

    async def notification(self, title: str, content: str, notify_id: Optional[int] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"title": title, "content": content}
        if notify_id is not None:
            body["id"] = notify_id
        return await self._request("POST", "/api/v1/notification", json_body=body, timeout=20)

    async def vibrate(self, milliseconds: int = 500) -> Dict[str, Any]:
        return await self._request(
            "POST", "/api/v1/vibrate", json_body={"milliseconds": milliseconds}, timeout=20
        )

    async def torch(self, state: str) -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/torch", json_body={"state": state}, timeout=20)

    async def clipboard_get(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/clipboard", timeout=20)

    async def clipboard_set(self, text: str) -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/clipboard", json_body={"text": text}, timeout=20)

    async def camera_photo(self, camera_id: int = 0) -> bytes:
        payload = await self._request(
            "POST", "/api/v1/camera", json_body={"camera": camera_id}, timeout=90
        )
        return base64.b64decode(payload["data_b64"])

    async def microphone_record(self, seconds: int = 5, encoder: str = "aac") -> bytes:
        payload = await self._request(
            "POST",
            "/api/v1/microphone",
            json_body={"seconds": seconds, "encoder": encoder},
            timeout=max(60, seconds + 45),
        )
        return base64.b64decode(payload["data_b64"])

    async def location(self, provider: str = "network") -> Dict[str, Any]:
        return await self._request(
            "GET", f"/api/v1/location?provider={provider}", timeout=90
        )

    async def wifi_info(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/wifi", timeout=30)

    async def tts(self, text: str) -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/tts", json_body={"text": text}, timeout=30)

    async def toast(self, text: str) -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/toast", json_body={"text": text}, timeout=20)

    # -------------------------------------------------------------------- pm2
    async def pm2_list(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/pm2", timeout=45)

    async def pm2_action(self, action: str, name: str) -> Dict[str, Any]:
        return await self._request(
            "POST", "/api/v1/pm2/action", json_body={"action": action, "name": name}, timeout=60
        )

    async def pm2_logs(self, name: str, lines: int = 40) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/api/v1/pm2/logs?name={name}&lines={lines}", timeout=45
        )

    # -------------------------------------------------------------------- ssh
    async def ssh_status(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/ssh/status", timeout=45)
