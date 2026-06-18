#!/usr/bin/env python3
"""Local Home Assistant camera wall.

Reads CABIN_HOME_ASSISTANT_TOKEN from the environment, starts eufy P2P streams
on demand, polls Home Assistant snapshot cameras, caches the latest frame, and
serves a browser-friendly monitor.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import socket
import ssl
import struct
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, fields
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


DEFAULT_HA_URL = "http://supervisor/core"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().with_name("camera_monitor.local.json")
VIEWER_TTL_SECONDS = 90
SOCKET_TIMEOUT_SECONDS = 12
CACHE_WRITE_INTERVAL_SECONDS = 2.0
MAX_BROWSER_FRAME_BYTES = 2_500_000
WEBRTC_SESSION_TTL_SECONDS = 120
WEBRTC_RATE_LIMIT_COOLDOWN_SECONDS = 5 * 60
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "camera_monitor"
LEGACY_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "eufy_monitor"
CACHE_DIR = Path(os.environ.get("CAMERA_MONITOR_CACHE_DIR", DEFAULT_CACHE_DIR))
ORDER_PATH = CACHE_DIR / "layout.json"
STALE_KICK_SECONDS = 5 * 60
STALE_KICK_COOLDOWN_SECONDS = 3 * 60
KICK_STOP_SETTLE_SECONDS = 3.0
EUFY_SECURITY_WS_ADDON = ""
ADDON_RESTART_AFTER_STALE_KICKS = 2
ADDON_RESTART_AFTER_START_FAILURES = 3
ADDON_RESTART_COOLDOWN_SECONDS = 20 * 60
ADDON_RESTART_SETTLE_SECONDS = 35.0
START_GATE = threading.Semaphore(1)
ADDON_RESTART_LOCK = threading.Lock()
LAST_ADDON_RESTART_AT = 0.0


def prepare_cache_dir() -> None:
    if CACHE_DIR.exists() or not LEGACY_CACHE_DIR.exists():
        return
    try:
        LEGACY_CACHE_DIR.rename(CACHE_DIR)
    except Exception as exc:  # noqa: BLE001 - losing cache should not block viewing.
        print(f"Unable to migrate legacy cache directory: {exc}", flush=True)


def detect_image_content_type(frame: bytes, fallback: str = "image/jpeg") -> str:
    if frame.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if frame.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if frame.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return fallback.split(";", 1)[0] or "application/octet-stream"


def is_placeholder_snapshot(frame: bytes, content_type: str) -> bool:
    return content_type == "image/png" and len(frame) < 10_000


@dataclass(frozen=True)
class CameraConfig:
    slug: str
    name: str
    entity_id: str
    station: str = ""
    lan_ip: str = ""
    retry_delay: float = 6.0
    start_delay: float = 12.0
    refresh_ms: int = 1000
    source: str = "eufy_p2p"
    snapshot_interval: float = 10.0
    stale_ok: bool = False
    stale_ok_seconds: int = 120
    stale_kick_seconds: int = STALE_KICK_SECONDS
    note: str = ""


CAMERA_CONFIG_FIELDS = {field.name for field in fields(CameraConfig)}
CAMERA_SOURCES = {"eufy_p2p", "snapshot", "webrtc"}
DEFAULT_CAMERA_ORDER: tuple[str, ...] = ()


def load_monitor_config(config_path: Path) -> tuple[str, str, tuple[CameraConfig, ...]]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        example_path = Path(__file__).resolve().with_name("camera_monitor.example.json")
        raise SystemExit(
            "Camera monitor config not found. Copy "
            f"{example_path} to {config_path} and fill in local camera details."
        ) from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Camera monitor config is not valid JSON: {config_path}") from exc

    if not isinstance(payload, dict):
        raise SystemExit("Camera monitor config must be a JSON object")

    camera_entries = payload.get("cameras")
    if not isinstance(camera_entries, list) or not camera_entries:
        raise SystemExit("Camera monitor config must include a non-empty cameras array")

    cameras: list[CameraConfig] = []
    seen_slugs: set[str] = set()
    for index, entry in enumerate(camera_entries, start=1):
        if not isinstance(entry, dict):
            raise SystemExit(f"Camera config entry {index} must be an object")
        unknown_keys = sorted(set(entry) - CAMERA_CONFIG_FIELDS)
        if unknown_keys:
            raise SystemExit(
                f"Camera config entry {index} has unknown keys: {', '.join(unknown_keys)}"
            )
        try:
            camera = CameraConfig(**entry)
        except TypeError as exc:
            raise SystemExit(f"Camera config entry {index} is incomplete: {exc}") from exc
        if not camera.slug or not camera.name or not camera.entity_id:
            raise SystemExit(
                f"Camera config entry {index} must include slug, name, and entity_id"
            )
        if camera.slug in seen_slugs:
            raise SystemExit(f"Duplicate camera slug in config: {camera.slug}")
        if camera.source not in CAMERA_SOURCES:
            raise SystemExit(
                f"Camera {camera.slug} has unsupported source {camera.source!r}"
            )
        seen_slugs.add(camera.slug)
        cameras.append(camera)

    ha_url = str(payload.get("ha_url") or "").strip()
    eufy_addon = str(payload.get("eufy_security_ws_addon") or "").strip()
    return ha_url, eufy_addon, tuple(cameras)


def normalize_camera_order(order: Any) -> list[str]:
    ordered: list[str] = []
    if isinstance(order, list):
        for slug in order:
            if (
                isinstance(slug, str)
                and slug in DEFAULT_CAMERA_ORDER
                and slug not in ordered
            ):
                ordered.append(slug)
    ordered.extend(slug for slug in DEFAULT_CAMERA_ORDER if slug not in ordered)
    return ordered


def load_camera_order() -> list[str]:
    try:
        payload = json.loads(ORDER_PATH.read_text(encoding="utf-8"))
        return normalize_camera_order(payload.get("camera_order", []))
    except FileNotFoundError:
        return list(DEFAULT_CAMERA_ORDER)
    except Exception as exc:  # noqa: BLE001 - a bad layout file should not block viewing.
        print(f"Unable to load camera layout order: {exc}", flush=True)
        return list(DEFAULT_CAMERA_ORDER)


def save_camera_order(order: list[str]) -> list[str]:
    normalized = normalize_camera_order(order)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = ORDER_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(
            {
                "camera_order": normalized,
                "updated_at": time.time(),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    tmp_path.replace(ORDER_PATH)
    return normalized


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        timeout: float = SOCKET_TIMEOUT_SECONDS,
    ) -> urllib.response.addinfourl:
        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        return urllib.request.urlopen(req, timeout=timeout)

    def call_service(self, domain: str, service: str, entity_id: str) -> tuple[int, str]:
        return self.call_service_data(domain, service, {"entity_id": entity_id})

    def call_service_data(
        self,
        domain: str,
        service: str,
        payload: dict[str, Any],
    ) -> tuple[int, str]:
        data = json.dumps(payload).encode("utf-8")
        try:
            with self._request(
                f"/api/services/{domain}/{service}",
                method="POST",
                data=data,
                timeout=20,
            ) as response:
                body = response.read(2048).decode("utf-8", errors="replace")
                return response.status, body
        except urllib.error.HTTPError as exc:
            body = exc.read(2048).decode("utf-8", errors="replace")
            return exc.code, body
        except Exception as exc:  # noqa: BLE001 - surfaced to the local monitor UI.
            return 0, f"{type(exc).__name__}: {exc}"

    def open_camera_stream(self, entity_id: str) -> urllib.response.addinfourl:
        quoted_entity = urllib.parse.quote(entity_id, safe=".")
        return self._request(f"/api/camera_proxy_stream/{quoted_entity}", timeout=20)

    def open_camera_snapshot(self, entity_id: str) -> urllib.response.addinfourl:
        quoted_entity = urllib.parse.quote(entity_id, safe=".")
        return self._request(f"/api/camera_proxy/{quoted_entity}", timeout=20)

    def websocket_url(self) -> str:
        parsed = urllib.parse.urlsplit(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        base_path = parsed.path.rstrip("/")
        if base_path.endswith("/core"):
            path = f"{base_path}/websocket"
        else:
            path = f"{base_path}/api/websocket" if base_path else "/api/websocket"
        return urllib.parse.urlunsplit((scheme, parsed.netloc, path, "", ""))

    def get_state(self, entity_id: str) -> dict[str, Any] | None:
        quoted_entity = urllib.parse.quote(entity_id, safe=".")
        try:
            with self._request(f"/api/states/{quoted_entity}", timeout=8) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            return None


class MinimalWebSocket:
    """Small WebSocket client for Home Assistant signaling."""

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, url: str) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"ws", "wss"}:
            raise ValueError(f"unsupported WebSocket scheme: {parsed.scheme}")
        if not parsed.hostname:
            raise ValueError("WebSocket URL must include a host")

        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        raw_sock = socket.create_connection((parsed.hostname, port), timeout=15)
        if parsed.scheme == "wss":
            raw_sock = ssl.create_default_context().wrap_socket(
                raw_sock,
                server_hostname=parsed.hostname,
            )
        raw_sock.settimeout(30)
        self.sock = raw_sock
        self.read_buffer = b""
        self.send_lock = threading.Lock()
        self.closed = False

        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        host = parsed.netloc
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        self.sock.sendall(request)
        response = self._read_http_response()
        expected = base64.b64encode(
            hashlib.sha1((key + self.GUID).encode("ascii")).digest()
        ).decode("ascii")
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise ConnectionError(response.decode("utf-8", errors="replace")[:300])
        if f"sec-websocket-accept: {expected}".lower().encode("ascii") not in response.lower():
            raise ConnectionError("WebSocket accept header did not match")

    def _read_http_response(self) -> bytes:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake closed early")
            data += chunk
            if len(data) > 16384:
                raise ConnectionError("WebSocket handshake response too large")
        response, self.read_buffer = data.split(b"\r\n\r\n", 1)
        return response + b"\r\n\r\n"

    def _read_exact(self, length: int) -> bytes:
        if len(self.read_buffer) >= length:
            data = self.read_buffer[:length]
            self.read_buffer = self.read_buffer[length:]
            return data

        data = self.read_buffer
        self.read_buffer = b""
        while len(data) < length:
            chunk = self.sock.recv(length - len(data))
            if not chunk:
                raise ConnectionError("WebSocket closed")
            data += chunk
        return data

    def recv_json(self) -> dict[str, Any]:
        return json.loads(self.recv_text())

    def recv_text(self) -> str:
        message = bytearray()
        while True:
            first, second = self._read_exact(2)
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]

            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

            if opcode == 0x8:
                raise ConnectionError("WebSocket close received")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode not in {0x0, 0x1}:
                continue

            message.extend(payload)
            if fin:
                return message.decode("utf-8")

    def send_json(self, payload: dict[str, Any]) -> None:
        self.send_text(json.dumps(payload, separators=(",", ":")))

    def send_text(self, payload: str) -> None:
        self._send_frame(0x1, payload.encode("utf-8"))

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        with self.send_lock:
            if self.closed:
                raise ConnectionError("WebSocket is closed")
            header = bytearray([0x80 | opcode])
            length = len(payload)
            if length < 126:
                header.append(0x80 | length)
            elif length < 65536:
                header.append(0x80 | 126)
                header.extend(struct.pack("!H", length))
            else:
                header.append(0x80 | 127)
                header.extend(struct.pack("!Q", length))
            mask = secrets.token_bytes(4)
            masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            self.sock.sendall(bytes(header) + mask + masked)

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        self.closed = True
        try:
            self.sock.close()
        except Exception:
            pass


def ha_ws_connect(ha: HomeAssistantClient) -> MinimalWebSocket:
    ws = MinimalWebSocket(ha.websocket_url())
    auth_required = ws.recv_json()
    if auth_required.get("type") != "auth_required":
        ws.close()
        raise ConnectionError(f"unexpected auth preface: {auth_required}")
    ws.send_json({"type": "auth", "access_token": ha.token})
    auth_result = ws.recv_json()
    if auth_result.get("type") != "auth_ok":
        ws.close()
        raise PermissionError(f"Home Assistant WebSocket auth failed: {auth_result}")
    return ws


def ha_ws_call(ha: HomeAssistantClient, payload: dict[str, Any]) -> dict[str, Any]:
    ws = ha_ws_connect(ha)
    try:
        command = dict(payload)
        command["id"] = 1
        ws.send_json(command)
        while True:
            message = ws.recv_json()
            if message.get("id") == 1:
                return message
    finally:
        ws.close()


class WebRTCSessionProxy:
    def __init__(
        self,
        local_id: str,
        runner: "CameraRunner",
        ha: HomeAssistantClient,
        offer: str,
    ) -> None:
        self.local_id = local_id
        self.runner = runner
        self.ha = ha
        self.offer = offer
        self.ws: MinimalWebSocket | None = None
        self.closed = False
        self.created_at = time.time()
        self.last_seen_at = self.created_at
        self.ha_session_id = ""
        self.next_command_id = 2
        self.pending_candidates: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.ws = ha_ws_connect(self.ha)
        self.ws.sock.settimeout(None)
        self.ws.send_json(
            {
                "id": 1,
                "type": "camera/webrtc/offer",
                "entity_id": self.runner.config.entity_id,
                "offer": self.offer,
            }
        )
        self.thread = threading.Thread(
            target=self._read_loop,
            name=f"webrtc-{self.runner.config.slug}",
            daemon=True,
        )
        self.thread.start()

    def add_candidate(self, candidate: dict[str, Any]) -> None:
        with self.lock:
            self.last_seen_at = time.time()
            if self.closed:
                raise ConnectionError("WebRTC session is closed")
            if not self.ha_session_id:
                self.pending_candidates.append(candidate)
                return
        self._send_candidate(candidate)

    def pop_events(self) -> list[dict[str, Any]]:
        with self.lock:
            self.last_seen_at = time.time()
            events = self.events
            self.events = []
            return events

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
        if self.ws is not None:
            self.ws.close()

    def expired(self) -> bool:
        with self.lock:
            return time.time() - self.last_seen_at > WEBRTC_SESSION_TTL_SECONDS

    def _queue_event(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.events.append(event)
            self.last_seen_at = time.time()

    def _read_loop(self) -> None:
        try:
            while True:
                with self.lock:
                    if self.closed:
                        return
                if self.ws is None:
                    return
                message = self.ws.recv_json()
                if message.get("id") == 1 and message.get("success") is False:
                    error = message.get("error", {})
                    self._queue_event({"type": "error", "message": str(error)})
                    self.runner.set_external_error(f"WebRTC offer failed: {error}")
                    return
                if message.get("type") != "event" or message.get("id") != 1:
                    continue

                event = message.get("event")
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "session":
                    session_id = str(event.get("session_id", ""))
                    with self.lock:
                        self.ha_session_id = session_id
                        pending = self.pending_candidates
                        self.pending_candidates = []
                    self._queue_event({"type": "session", "session_id": session_id})
                    for candidate in pending:
                        self._send_candidate(candidate)
                elif event.get("type") == "answer":
                    self._queue_event({"type": "answer", "answer": event.get("answer", "")})
                elif event.get("type") == "candidate":
                    self._queue_event({"type": "candidate", "candidate": event.get("candidate")})
                elif event.get("type") == "error":
                    message_text = str(event.get("message") or event)
                    self._queue_event({"type": "error", "message": message_text})
                    self.runner.set_external_error(f"WebRTC error: {message_text}")
        except Exception as exc:  # noqa: BLE001 - browser will retry the session.
            with self.lock:
                closed = self.closed
            if not closed:
                message_text = f"{type(exc).__name__}: {exc}"
                self._queue_event({"type": "error", "message": message_text})
                self.runner.set_external_error(f"WebRTC signaling error: {message_text}")
        finally:
            self.close()

    def _send_candidate(self, candidate: dict[str, Any]) -> None:
        with self.lock:
            if self.closed or not self.ha_session_id:
                return
            command_id = self.next_command_id
            self.next_command_id += 1
            session_id = self.ha_session_id
        if self.ws is None:
            return
        self.ws.send_json(
            {
                "id": command_id,
                "type": "camera/webrtc/candidate",
                "entity_id": self.runner.config.entity_id,
                "session_id": session_id,
                "candidate": candidate,
            }
        )


class CameraRunner:
    def __init__(self, config: CameraConfig, ha: HomeAssistantClient) -> None:
        self.config = config
        self.ha = ha
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.wanted_until = 0.0
        self.cache_path = CACHE_DIR / f"{self.config.slug}.jpg"
        self.cache_meta_path = CACHE_DIR / f"{self.config.slug}.json"
        (
            self.latest_frame,
            self.latest_at,
            self.latest_content_type,
        ) = self._load_cached_frame()
        self.cache_written_at = self.latest_at
        self.live = False
        self.last_error = ""
        self.last_start_status: int | None = None
        self.retry_count = 0
        self.first_frame_failure_count = 0
        self.kick_count = 0
        self.addon_restart_count = 0
        self.started_at = 0.0
        self.last_attempt_at = 0.0
        self.last_kick_at = 0.0
        self.last_addon_restart_at = 0.0
        self.webrtc_cooldown_until = 0.0

    def touch(self) -> None:
        with self.lock:
            self.wanted_until = max(self.wanted_until, time.time() + VIEWER_TTL_SECONDS)
            needs_start = self.thread is None or not self.thread.is_alive()
            if needs_start:
                self.thread = threading.Thread(
                    target=self._run,
                    name=f"camera-{self.config.slug}",
                    daemon=True,
                )
                self.thread.start()

    def stop_when_idle(self) -> None:
        with self.lock:
            self.wanted_until = 0.0

    def receive_browser_frame(self, frame: bytes, content_type: str) -> None:
        with self.lock:
            self.wanted_until = max(self.wanted_until, time.time() + VIEWER_TTL_SECONDS)
        self._set_state(live=True, error="", frame=frame, content_type=content_type)

    def set_external_error(self, error: str) -> None:
        lower_error = error.lower()
        if any(
            marker in lower_error
            for marker in ("429", "rate limit", "too many requests", "resource_exhausted")
        ):
            self.webrtc_cooldown_until = time.time() + WEBRTC_RATE_LIMIT_COOLDOWN_SECONDS
        self.retry_count += 1
        self._set_state(live=False, error=error)

    def webrtc_cooldown_seconds(self) -> int:
        return max(0, round(self.webrtc_cooldown_until - time.time()))

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            latest_at = self.latest_at
            return {
                "slug": self.config.slug,
                "name": self.config.name,
                "entity_id": self.config.entity_id,
                "station": self.config.station,
                "lan_ip": self.config.lan_ip,
                "source": self.config.source,
                "live": self.live,
                "wanted": now < self.wanted_until,
                "has_frame": self.latest_frame is not None,
                "age_seconds": None if latest_at <= 0 else round(now - latest_at, 1),
                "latest_at": None if latest_at <= 0 else latest_at,
                "last_error": self.last_error,
                "last_start_status": self.last_start_status,
                "retry_count": self.retry_count,
                "first_frame_failure_count": self.first_frame_failure_count,
                "webrtc_cooldown_seconds": self.webrtc_cooldown_seconds(),
                "kick_count": self.kick_count,
                "last_kick_at": None if self.last_kick_at <= 0 else self.last_kick_at,
                "addon_restart_count": self.addon_restart_count,
                "last_addon_restart_at": (
                    None
                    if self.last_addon_restart_at <= 0
                    else self.last_addon_restart_at
                ),
                "refresh_ms": self.config.refresh_ms,
                "snapshot_interval": self.config.snapshot_interval,
                "stale_ok": self.config.stale_ok,
                "stale_ok_seconds": self.config.stale_ok_seconds,
                "stale_kick_seconds": self.config.stale_kick_seconds,
                "note": self.config.note,
            }

    def get_frame(self) -> tuple[bytes | None, float, str]:
        with self.lock:
            return self.latest_frame, self.latest_at, self.latest_content_type

    def _wanted(self) -> bool:
        with self.lock:
            return time.time() < self.wanted_until

    def _set_state(
        self,
        *,
        live: bool | None = None,
        error: str | None = None,
        start_status: int | None = None,
        frame: bytes | None = None,
        content_type: str = "image/jpeg",
    ) -> None:
        now = time.time()
        if frame is not None:
            content_type = detect_image_content_type(frame, content_type)
        with self.lock:
            if live is not None:
                self.live = live
            if error is not None:
                self.last_error = error[:300]
            if start_status is not None:
                self.last_start_status = start_status
            if frame is not None:
                self.latest_frame = frame
                self.latest_at = now
                self.latest_content_type = content_type
                self.first_frame_failure_count = 0
                if now - self.cache_written_at >= CACHE_WRITE_INTERVAL_SECONDS:
                    self._write_cached_frame(frame, now, content_type)
                    self.cache_written_at = now

    def _load_cached_frame(self) -> tuple[bytes | None, float, str]:
        content_type = "image/jpeg"
        try:
            if not self.cache_path.exists():
                return None, 0.0, content_type
            latest_at = self.cache_path.stat().st_mtime
            if self.cache_meta_path.exists():
                metadata = json.loads(self.cache_meta_path.read_text(encoding="utf-8"))
                latest_at = float(metadata.get("latest_at", latest_at))
                content_type = str(metadata.get("content_type", content_type))
            frame = self.cache_path.read_bytes()
            content_type = detect_image_content_type(frame, content_type)
            if self.config.source == "snapshot" and is_placeholder_snapshot(
                frame,
                content_type,
            ):
                return None, 0.0, content_type
            return frame, latest_at, content_type
        except Exception as exc:  # noqa: BLE001 - cache should never stop live viewing.
            print(f"Unable to load cache for {self.config.slug}: {exc}", flush=True)
            return None, 0.0, content_type

    def _write_cached_frame(
        self,
        frame: bytes,
        latest_at: float,
        content_type: str,
    ) -> None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp_path = self.cache_path.with_suffix(".jpg.tmp")
            tmp_meta_path = self.cache_meta_path.with_suffix(".json.tmp")
            tmp_path.write_bytes(frame)
            tmp_path.replace(self.cache_path)
            tmp_meta_path.write_text(
                json.dumps(
                    {
                        "slug": self.config.slug,
                        "entity_id": self.config.entity_id,
                        "latest_at": latest_at,
                        "content_type": content_type,
                    }
                ),
                encoding="utf-8",
            )
            tmp_meta_path.replace(self.cache_meta_path)
        except Exception as exc:  # noqa: BLE001 - cache should never stop live viewing.
            print(f"Unable to write cache for {self.config.slug}: {exc}", flush=True)

    def _run(self) -> None:
        if self.config.source == "snapshot":
            self._run_snapshot_poll()
            return
        if self.config.source == "webrtc":
            self._run_webrtc_watchdog()
            return

        while self._wanted():
            self.last_attempt_at = time.time()
            self._maybe_kick_stale_stream()
            restart_after_stop = False

            start_gate_released = False

            def release_start_gate() -> None:
                nonlocal start_gate_released
                if not start_gate_released:
                    START_GATE.release()
                    start_gate_released = True

            START_GATE.acquire()
            try:
                already_streaming = self._ha_camera_is_streaming()
                if not already_streaming:
                    status, body = self.ha.call_service(
                        "eufy_security",
                        "start_p2p_livestream",
                        self.config.entity_id,
                    )
                    self._set_state(start_status=status)
                    if status != HTTPStatus.OK:
                        self.retry_count += 1
                        self._set_state(
                            live=False,
                            error=f"start_p2p returned {status}: {body}",
                        )
                        release_start_gate()
                        time.sleep(self.config.retry_delay)
                        continue

                    self.started_at = time.time()
                    self._set_state(
                        live=False,
                        error=f"waiting {int(self.config.start_delay)}s for go2rtc stream",
                    )
                    self._sleep_while_wanted(self.config.start_delay)
                    if not self._wanted():
                        release_start_gate()
                        continue
                else:
                    self._set_state(
                        start_status=HTTPStatus.OK,
                        live=False,
                        error="using existing Home Assistant stream",
                    )

                self._set_state(live=False, error="opening Home Assistant stream")
                self._read_mjpeg_until_idle(on_first_frame=release_start_gate)
            except Exception as exc:  # noqa: BLE001 - local status should show raw failure.
                self.retry_count += 1
                error = f"{type(exc).__name__}: {exc}"
                self._set_state(live=False, error=error)
                restart_after_stop = self._record_first_frame_failure(error)
            finally:
                release_start_gate()
                self._set_state(live=False)
                self.ha.call_service(
                    "eufy_security",
                    "stop_p2p_livestream",
                    self.config.entity_id,
                )
                if restart_after_stop:
                    self._restart_eufy_security_ws(
                        "first-frame watchdog restarting eufy-security-ws add-on "
                        f"after {self.first_frame_failure_count} start failures"
                    )

            if self._wanted():
                time.sleep(self.config.retry_delay)

        self._set_state(live=False)

    def _run_webrtc_watchdog(self) -> None:
        while self._wanted():
            now = time.time()
            with self.lock:
                has_recent_frame = self.latest_at > 0 and now - self.latest_at < 6
                self.live = has_recent_frame
                if self.last_error.startswith("WebRTC") and has_recent_frame:
                    self.last_error = ""
            time.sleep(1.0)
        self._set_state(live=False)

    def _run_snapshot_poll(self) -> None:
        while self._wanted():
            self.last_attempt_at = time.time()
            try:
                with self.ha.open_camera_snapshot(self.config.entity_id) as response:
                    frame = response.read()
                    if not frame:
                        raise ConnectionError("Home Assistant snapshot returned no bytes")
                    content_type = detect_image_content_type(
                        frame,
                        response.headers.get_content_type() or "image/jpeg",
                    )
                    if is_placeholder_snapshot(frame, content_type):
                        raise ConnectionError(
                            "Home Assistant returned a placeholder image, not a camera snapshot"
                        )
                    self._set_state(live=True, error="", frame=frame, content_type=content_type)
            except Exception as exc:  # noqa: BLE001 - local status should show raw failure.
                self.retry_count += 1
                self._set_state(live=False, error=f"{type(exc).__name__}: {exc}")

            self._sleep_while_wanted(self.config.snapshot_interval)

        self._set_state(live=False)

    def _ha_camera_is_streaming(self) -> bool:
        state = self.ha.get_state(self.config.entity_id)
        return bool(state and state.get("state") == "streaming")

    def _maybe_kick_stale_stream(self) -> bool:
        now = time.time()
        wants_addon_restart = False
        with self.lock:
            if self.config.stale_kick_seconds <= 0:
                return False
            if now >= self.wanted_until or self.latest_at <= 0:
                return False

            age = now - self.latest_at
            if age < self.config.stale_kick_seconds:
                return False
            if now - self.last_kick_at < STALE_KICK_COOLDOWN_SECONDS:
                return False

            self.last_kick_at = now
            self.kick_count += 1
            wants_addon_restart = (
                self.config.source == "eufy_p2p"
                and self.kick_count >= ADDON_RESTART_AFTER_STALE_KICKS
            )
            self.live = False
            self.last_error = (
                f"stale watchdog kicked stream after {round(age)}s without a frame"
            )

        should_restart_addon = wants_addon_restart and self._claim_addon_restart(now)
        restart_reason = (
            "stale watchdog restarting eufy-security-ws add-on after "
            f"{self.kick_count} stale kicks"
        )

        self.ha.call_service(
            "eufy_security",
            "stop_p2p_livestream",
            self.config.entity_id,
        )
        if should_restart_addon:
            self._restart_eufy_security_ws(restart_reason)
        else:
            self._sleep_while_wanted(KICK_STOP_SETTLE_SECONDS)
        return True

    def _record_first_frame_failure(self, error: str) -> bool:
        if self.config.source != "eufy_p2p":
            return False

        lower_error = error.lower()
        if not any(marker in lower_error for marker in ("first frame", "timed out")):
            return False

        now = time.time()
        with self.lock:
            if self.latest_at > 0:
                return False
            self.first_frame_failure_count += 1
            if self.first_frame_failure_count < ADDON_RESTART_AFTER_START_FAILURES:
                return False
            self.live = False
            self.last_error = (
                f"first-frame watchdog saw {self.first_frame_failure_count} "
                "start failures"
            )

        return self._claim_addon_restart(now)

    def _restart_eufy_security_ws(self, reason: str) -> None:
        now = time.time()
        with self.lock:
            self.last_addon_restart_at = now
            self.addon_restart_count += 1
            self.last_error = reason

        if not EUFY_SECURITY_WS_ADDON:
            self._set_state(
                live=False,
                error=f"{reason}; eufy_security_ws_addon is not configured",
            )
            self._sleep_while_wanted(KICK_STOP_SETTLE_SECONDS)
            return

        status, body = self.ha.call_service_data(
            "hassio",
            "addon_restart",
            {"addon": EUFY_SECURITY_WS_ADDON},
        )
        if status != HTTPStatus.OK:
            self._set_state(
                live=False,
                error=f"hassio addon_restart returned {status}: {body}",
            )
            self._sleep_while_wanted(KICK_STOP_SETTLE_SECONDS)
        else:
            self._sleep_while_wanted(ADDON_RESTART_SETTLE_SECONDS)

    def _claim_addon_restart(self, now: float) -> bool:
        global LAST_ADDON_RESTART_AT
        with ADDON_RESTART_LOCK:
            if now - LAST_ADDON_RESTART_AT < ADDON_RESTART_COOLDOWN_SECONDS:
                return False
            LAST_ADDON_RESTART_AT = now
            return True

    def _sleep_while_wanted(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while self._wanted() and time.time() < deadline:
            time.sleep(min(0.5, deadline - time.time()))

    def _read_mjpeg_until_idle(self, on_first_frame: Callable[[], None] | None = None) -> None:
        with self.ha.open_camera_stream(self.config.entity_id) as response:
            buffer = b""
            frames_seen = 0
            while self._wanted():
                if self._maybe_kick_stale_stream():
                    raise ConnectionError("stale watchdog kicked stream")
                chunk = response.read(8192)
                if not chunk:
                    if frames_seen == 0:
                        raise ConnectionError(
                            "Home Assistant stream closed before first frame; "
                            "eufy/go2rtc may still be preparing or the camera timed out"
                        )
                    raise ConnectionError("Home Assistant stream closed")
                buffer += chunk

                while True:
                    start = buffer.find(b"\xff\xd8")
                    if start == -1:
                        buffer = buffer[-4096:]
                        break
                    end = buffer.find(b"\xff\xd9", start + 2)
                    if end == -1:
                        buffer = buffer[start:]
                        if len(buffer) > 2_000_000:
                            raise ValueError("MJPEG buffer grew without a full JPEG frame")
                        break

                    frame = buffer[start : end + 2]
                    buffer = buffer[end + 2 :]
                    frames_seen += 1
                    if frames_seen == 1 and on_first_frame is not None:
                        on_first_frame()
                    self._set_state(live=True, error="", frame=frame)


def make_placeholder_svg() -> bytes:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <defs>
    <radialGradient id="g" cx="50%" cy="45%" r="75%">
      <stop offset="0%" stop-color="#1e252c"/>
      <stop offset="100%" stop-color="#07090b"/>
    </radialGradient>
  </defs>
  <rect width="1280" height="720" fill="url(#g)"/>
  <circle cx="640" cy="326" r="42" fill="none" stroke="#5f6b78" stroke-width="6" opacity=".55"/>
  <circle cx="640" cy="326" r="42" fill="none" stroke="#d8e2ec" stroke-width="6" stroke-dasharray="72 192" stroke-linecap="round"/>
  <text x="640" y="414" fill="#d8e2ec" font-size="38" font-family="Arial, sans-serif" font-weight="700" text-anchor="middle">waiting</text>
</svg>""".encode(
        "utf-8"
    )


def render_index(camera_payload: list[dict[str, Any]]) -> bytes:
    cameras_json = json.dumps(camera_payload)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Brightwater Camera Monitor</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #000;
      --text: #edf2f7;
      --good: #42d392;
      --warn: #ffcc66;
      --bad: #ff6b6b;
    }}
    * {{ box-sizing: border-box; }}
    html {{
      width: 100%;
      height: 100%;
      background: #000;
    }}
    body {{
      margin: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      user-select: none;
    }}
    main {{
      width: 100vw;
      height: 100dvh;
      display: grid;
      gap: 2px;
      padding: 0;
      background: #000;
      overflow: hidden;
    }}
    main.layout-5-feature {{
      grid-template-columns: repeat(4, minmax(0, 1fr));
      grid-template-rows: minmax(0, .62fr) minmax(0, 1.38fr);
    }}
    main.layout-5-feature .tile:nth-child(5) {{
      grid-column: 1 / 5;
      grid-row: 2;
    }}
    main.layout-5-compact {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      grid-template-rows: minmax(0, .9fr) minmax(0, .9fr) minmax(0, 1.2fr);
    }}
    main.layout-5-compact .tile:nth-child(5) {{
      grid-column: 1 / 3;
    }}
    main.count-4 {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      grid-template-rows: repeat(2, minmax(0, 1fr));
    }}
    main.count-3 {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      grid-template-rows: repeat(2, minmax(0, 1fr));
    }}
    main.count-3 .tile:nth-child(1) {{ grid-row: 1 / 3; }}
    main.count-2 {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      grid-template-rows: 1fr;
    }}
    main.count-1 {{
      grid-template-columns: 1fr;
      grid-template-rows: 1fr;
    }}
    .tile {{
      min-width: 0;
      min-height: 0;
      position: relative;
      isolation: isolate;
      background: #050607;
      overflow: hidden;
      cursor: pointer;
      transform-origin: top left;
      touch-action: manipulation;
    }}
    .tile.dragging {{
      opacity: .62;
    }}
    .tile.drop-target {{
      outline: 3px solid rgba(66, 211, 146, .82);
      outline-offset: -4px;
    }}
    .tile img,
    .tile video {{
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
      background: #050607;
      pointer-events: none;
      -webkit-user-drag: none;
    }}
    .tile video {{
      display: none;
    }}
    .tile.webrtc-live img {{
      display: none;
    }}
    .tile.webrtc-live video {{
      display: block;
    }}
    .tile::after {{
      content: "";
      position: absolute;
      inset: auto 0 0;
      height: 32%;
      pointer-events: none;
      background: linear-gradient(to top, rgba(0, 0, 0, .44), rgba(0, 0, 0, 0));
      opacity: .78;
      z-index: 1;
    }}
    .tile.expanded {{
      position: fixed;
      inset: 0;
      z-index: 20;
      background: #000;
    }}
    .tile.expanded img,
    .tile.expanded video {{
      object-fit: contain;
    }}
    .hud {{
      position: absolute;
      right: 16px;
      bottom: 14px;
      z-index: 2;
      display: flex;
      justify-content: flex-end;
      align-items: center;
      gap: 8px;
      max-width: min(72%, 520px);
      pointer-events: none;
    }}
    .badge {{
      min-width: 0;
      border-radius: 999px;
      padding: 7px 10px;
      text-align: center;
      font-size: 13px;
      line-height: 1;
      font-weight: 800;
      letter-spacing: .02em;
      text-transform: uppercase;
      color: rgba(255, 255, 255, .92);
      background: rgba(11, 13, 16, .62);
      border: 1px solid rgba(255, 255, 255, .18);
      box-shadow: 0 8px 28px rgba(0, 0, 0, .32);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
    }}
    .badge.live {{ color: #04140b; background: rgba(66, 211, 146, .92); border-color: rgba(66, 211, 146, .24); }}
    .badge.stale {{ color: #231900; background: rgba(255, 204, 102, .92); border-color: rgba(255, 204, 102, .24); }}
    .badge.retrying {{ color: #2b0808; background: rgba(255, 107, 107, .92); border-color: rgba(255, 107, 107, .24); }}
    .badge.waiting {{ color: rgba(255, 255, 255, .88); background: rgba(43, 49, 56, .72); }}
    .badge[hidden] {{ display: none; }}
    .badge-age {{ text-transform: none; }}
    @media (max-width: 780px) {{
      html {{
        height: auto;
        min-height: 100%;
      }}
      body {{
        height: auto;
        min-height: 100%;
        overflow-x: hidden;
        overflow-y: auto;
        -webkit-overflow-scrolling: touch;
      }}
      main,
      main.layout-5-feature,
      main.layout-5-compact,
      main.count-4,
      main.count-3,
      main.count-2 {{
        width: 100%;
        height: auto;
        min-height: 100dvh;
        overflow: visible;
        grid-template-columns: 1fr;
        grid-template-rows: none;
        grid-auto-rows: minmax(190px, 56vw);
      }}
      main.layout-5-feature .tile,
      main.layout-5-compact .tile,
      main.count-4 .tile,
      main.count-3 .tile,
      main.count-2 .tile {{
        grid-column: auto;
        grid-row: auto;
      }}
      .tile {{
        min-height: 190px;
        touch-action: pan-y manipulation;
      }}
      .tile.expanded {{
        touch-action: manipulation;
      }}
      .hud {{ right: 10px; bottom: 10px; }}
      .badge {{ font-size: 11px; padding: 6px 8px; }}
    }}
  </style>
</head>
<body>
  <main id="grid"></main>
  <script>
    const cameras = {cameras_json};
    const grid = document.getElementById("grid");
    let cameraOrder = cameras.map((camera) => camera.slug);
    let paused = false;
    let expandedTile = null;
    let draggedSlug = null;
    let didDrag = false;
    let suppressNextClick = false;
    let orderSaveTimer = null;
    const imageTimers = new Map();
    const webrtcStates = new Map();
    const consoleThrottle = new Map();
    const errorLogIntervalMs = 60000;
    const webrtcFrameIntervalMs = 2000;
    const webrtcRetryMs = 60000;
    const webrtcRateLimitRetryMs = 300000;

    function applyLayout() {{
      const countClass = `count-${{cameraOrder.length}}`;
      grid.className = countClass;
      if (cameraOrder.length === 5) {{
        const viewportAspect = window.innerWidth / Math.max(window.innerHeight, 1);
        const roomy = window.innerWidth >= 1000 && viewportAspect >= 1.05;
        grid.classList.add(roomy ? "layout-5-feature" : "layout-5-compact");
      }} else if (cameraOrder.length > 5) {{
        grid.classList.add("layout-feature-many");
        const mobile = window.innerWidth <= 780;
        const topCount = cameraOrder.length - 1;
        const cols = mobile ? 1 : Math.min(4, Math.max(2, Math.ceil(topCount / 2)));
        const topRows = mobile ? cameraOrder.length : Math.ceil(topCount / cols);
        grid.style.gridTemplateColumns = `repeat(${{cols}}, minmax(0, 1fr))`;
        grid.style.gridTemplateRows = mobile
          ? `repeat(${{cameraOrder.length}}, minmax(190px, 56vw))`
          : `repeat(${{topRows}}, minmax(0, .58fr)) minmax(0, 1.28fr)`;
        for (const tile of grid.querySelectorAll(".tile")) {{
          tile.style.gridColumn = "";
          tile.style.gridRow = "";
        }}
        if (!mobile) {{
          const featured = getTile(cameraOrder[cameraOrder.length - 1]);
          if (featured) {{
            featured.style.gridColumn = `1 / ${{cols + 1}}`;
            featured.style.gridRow = `${{topRows + 1}}`;
          }}
        }}
        return;
      }}
      grid.style.gridTemplateColumns = "";
      grid.style.gridTemplateRows = "";
      for (const tile of grid.querySelectorAll(".tile")) {{
        tile.style.gridColumn = "";
        tile.style.gridRow = "";
      }}
    }}

    function buildTile(camera) {{
      const tile = document.createElement("section");
      tile.className = "tile";
      tile.dataset.slug = camera.slug;
      tile.draggable = true;
      tile.innerHTML = `
        <img alt="" data-role="image" draggable="false">
        <video data-role="video" autoplay muted playsinline></video>
        <div class="hud">
          <div class="badge waiting" data-role="badge">waiting</div>
        </div>
      `;
      tile.addEventListener("click", () => {{
        if (suppressNextClick) {{
          suppressNextClick = false;
          return;
        }}
        toggleExpanded(tile);
      }});
      tile.addEventListener("dragstart", handleDragStart);
      tile.addEventListener("dragenter", handleDragEnter);
      tile.addEventListener("dragover", handleDragOver);
      tile.addEventListener("dragleave", handleDragLeave);
      tile.addEventListener("drop", handleDrop);
      tile.addEventListener("dragend", handleDragEnd);
      grid.appendChild(tile);
    }}

    function scheduleImage(camera, delay) {{
      clearTimeout(imageTimers.get(camera.slug));
      imageTimers.set(camera.slug, setTimeout(() => refreshImage(camera), delay));
    }}

    function refreshImage(camera) {{
      if (paused) return;
      const img = document.querySelector(`[data-slug="${{camera.slug}}"] img`);
      img.onload = () => scheduleImage(camera, camera.refresh_ms);
      img.onerror = () => scheduleImage(camera, Math.max(camera.refresh_ms, 3000));
      img.src = `/snapshot/${{camera.slug}}.jpg?t=${{Date.now()}}`;
    }}

    function sleep(ms) {{
      return new Promise((resolve) => setTimeout(resolve, ms));
    }}

    async function fetchJson(url, options = {{}}) {{
      const response = await fetch(url, {{
        cache: "no-store",
        ...options,
      }});
      const payload = await response.json().catch(() => ({{}}));
      if (!response.ok) {{
        throw new Error(payload.error || `${{response.status}} ${{response.statusText}}`);
      }}
      return payload;
    }}

    function logWebRTCError(camera, error) {{
      const now = Date.now();
      const key = `webrtc:${{camera.slug}}`;
      const last = consoleThrottle.get(key) || 0;
      if (now - last < errorLogIntervalMs) return;
      console.warn(`[camera-wall] ${{camera.slug}} WebRTC: ${{error}}`);
      consoleThrottle.set(key, now);
    }}

    async function startWebRTC(camera) {{
      if (camera.source !== "webrtc" || paused) return;
      const existing = webrtcStates.get(camera.slug);
      if (existing?.active || existing?.connecting) return;

      const tile = getTile(camera.slug);
      const video = tile?.querySelector("[data-role=video]");
      if (!tile || !video || !window.RTCPeerConnection) {{
        logWebRTCError(camera, "RTCPeerConnection is unavailable in this browser");
        return;
      }}

      const state = {{
        active: false,
        connecting: true,
        sessionId: "",
        pc: null,
        pendingLocalCandidates: [],
        pendingRemoteCandidates: [],
        remoteReady: false,
        lastCaptureAt: 0,
        captureTimer: null,
        watchdogTimer: null,
        pollTimer: null,
        restartTimer: null,
      }};
      webrtcStates.set(camera.slug, state);

      try {{
        const clientConfig = await fetchJson(`/api/webrtc/client-config/${{camera.slug}}`);
        const pc = new RTCPeerConnection(clientConfig.configuration || {{}});
        state.pc = pc;
        state.active = true;
        state.connecting = false;

        pc.addTransceiver("audio", {{ direction: "recvonly" }});
        pc.addTransceiver("video", {{ direction: "recvonly" }});
        if (clientConfig.dataChannel) {{
          pc.createDataChannel(clientConfig.dataChannel);
        }}
        pc.ontrack = (event) => {{
          const stream = event.streams?.[0] || new MediaStream([event.track]);
          if (video.srcObject !== stream) video.srcObject = stream;
          video.play().catch((error) => logWebRTCError(camera, error));
          tile.classList.add("webrtc-live");
          startFrameCapture(camera, video, state);
        }};
        pc.onicecandidate = (event) => {{
          if (!event.candidate) return;
          const candidate = event.candidate.toJSON();
          if (!state.sessionId) {{
            state.pendingLocalCandidates.push(candidate);
          }} else {{
            sendWebRTCCandidate(camera, state, candidate);
          }}
        }};
        pc.onconnectionstatechange = () => {{
          if (["failed", "closed"].includes(pc.connectionState)) {{
            restartWebRTC(camera, `connection ${{pc.connectionState}}`);
          }}
        }};
        pc.oniceconnectionstatechange = () => {{
          if (["failed", "closed"].includes(pc.iceConnectionState)) {{
            restartWebRTC(camera, `ice ${{pc.iceConnectionState}}`);
          }}
        }};

        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        const start = await fetchJson(`/api/webrtc/start/${{camera.slug}}`, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ offer: pc.localDescription.sdp }}),
        }});
        state.sessionId = start.session_id;
        flushLocalCandidates(camera, state);
        pollWebRTCEvents(camera, state);
      }} catch (error) {{
        cleanupWebRTC(camera);
        logWebRTCError(camera, error);
        scheduleWebRTCRestart(camera, error);
      }}
    }}

    function flushLocalCandidates(camera, state) {{
      const pending = state.pendingLocalCandidates.splice(0);
      for (const candidate of pending) {{
        sendWebRTCCandidate(camera, state, candidate);
      }}
    }}

    async function sendWebRTCCandidate(camera, state, candidate) {{
      if (!state.sessionId) return;
      try {{
        await fetchJson(`/api/webrtc/candidate/${{state.sessionId}}`, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ candidate }}),
        }});
      }} catch (error) {{
        logWebRTCError(camera, error);
      }}
    }}

    async function pollWebRTCEvents(camera, state) {{
      while (state.active && state.sessionId) {{
        try {{
          const payload = await fetchJson(`/api/webrtc/events/${{state.sessionId}}`);
          for (const event of payload.events || []) {{
            await handleWebRTCEvent(camera, state, event);
          }}
        }} catch (error) {{
          logWebRTCError(camera, error);
          restartWebRTC(camera, error);
          return;
        }}
        await sleep(250);
      }}
    }}

    async function handleWebRTCEvent(camera, state, event) {{
      if (!state.pc) return;
      if (event.type === "answer" && event.answer) {{
        await state.pc.setRemoteDescription({{ type: "answer", sdp: event.answer }});
        state.remoteReady = true;
        const pending = state.pendingRemoteCandidates.splice(0);
        for (const candidate of pending) {{
          await state.pc.addIceCandidate(candidate);
        }}
      }} else if (event.type === "candidate" && event.candidate) {{
        if (state.remoteReady) {{
          await state.pc.addIceCandidate(event.candidate);
        }} else {{
          state.pendingRemoteCandidates.push(event.candidate);
        }}
      }} else if (event.type === "error") {{
        throw new Error(event.message || "WebRTC signaling error");
      }}
    }}

    function startFrameCapture(camera, video, state) {{
      if (state.captureTimer) return;
      state.lastCaptureAt = Date.now();
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d", {{ alpha: false }});
      state.captureTimer = setInterval(() => {{
        if (!state.active || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
        if (!video.videoWidth || !video.videoHeight || !context) return;
        state.lastCaptureAt = Date.now();
        const maxWidth = 1280;
        const scale = Math.min(1, maxWidth / video.videoWidth);
        canvas.width = Math.max(2, Math.round(video.videoWidth * scale));
        canvas.height = Math.max(2, Math.round(video.videoHeight * scale));
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((blob) => {{
          if (!blob) return;
          fetch(`/api/webrtc/frame/${{camera.slug}}`, {{
            method: "POST",
            headers: {{ "Content-Type": "image/jpeg" }},
            body: blob,
          }}).catch((error) => logWebRTCError(camera, error));
        }}, "image/jpeg", 0.78);
      }}, webrtcFrameIntervalMs);
      state.watchdogTimer = setInterval(() => {{
        if (!state.active) return;
        if (Date.now() - state.lastCaptureAt > 15000) {{
          restartWebRTC(camera, "frame capture stalled");
        }}
      }}, 10000);
    }}

    function restartWebRTC(camera, reason) {{
      logWebRTCError(camera, reason);
      cleanupWebRTC(camera);
      scheduleWebRTCRestart(camera, reason);
    }}

    function scheduleWebRTCRestart(camera, reason = "") {{
      const state = webrtcStates.get(camera.slug) || {{}};
      if (state.restartTimer) clearTimeout(state.restartTimer);
      const message = String(reason);
      const delay = /429|rate limit|too many requests|resource_exhausted|cooling down/i.test(message)
        ? webrtcRateLimitRetryMs
        : webrtcRetryMs;
      state.restartTimer = setTimeout(() => startWebRTC(camera), delay);
      webrtcStates.set(camera.slug, state);
    }}

    function cleanupWebRTC(camera) {{
      const state = webrtcStates.get(camera.slug);
      if (!state) return;
      state.active = false;
      state.connecting = false;
      if (state.captureTimer) clearInterval(state.captureTimer);
      if (state.watchdogTimer) clearInterval(state.watchdogTimer);
      if (state.pc) {{
        try {{ state.pc.close(); }} catch (_) {{}}
      }}
      const tile = getTile(camera.slug);
      const video = tile?.querySelector("[data-role=video]");
      if (video) {{
        video.pause();
        video.srcObject = null;
      }}
      if (tile) tile.classList.remove("webrtc-live");
      if (state.sessionId) {{
        fetch(`/api/webrtc/close/${{state.sessionId}}`, {{ method: "POST" }}).catch(() => {{}});
      }}
      webrtcStates.delete(camera.slug);
    }}

    function formatRelativeAge(age) {{
      if (age === null || age === undefined) return "";
      const seconds = Math.max(0, age);
      if (seconds < 60) return `${{Math.round(seconds)}}s`;
      const minutes = seconds / 60;
      if (minutes < 60) return `${{Math.round(minutes)}}m`;
      const hours = minutes / 60;
      if (hours < 48) return `${{Math.round(hours)}}h`;
      return `${{Math.round(hours / 24)}}d`;
    }}

    function setBadge(tile, status, label) {{
      const badge = tile.querySelector("[data-role=badge]");
      badge.className = `badge ${{status}}`;
      badge.textContent = label;
      badge.hidden = status === "waiting";
    }}

    function setStaleBadge(tile, ageText) {{
      const badge = tile.querySelector("[data-role=badge]");
      badge.className = "badge stale";
      badge.innerHTML = `STALE <span class="badge-age">${{ageText}}</span>`;
      badge.hidden = false;
    }}

    function logCameraError(status) {{
      if (!status.last_error) return;
      if (status.last_error.startsWith("waiting ")) return;
      const now = Date.now();
      const last = consoleThrottle.get(status.slug) || 0;
      if (now - last < errorLogIntervalMs) return;
      console.warn(`[camera-wall] ${{status.slug}}: ${{status.last_error}}`);
      consoleThrottle.set(status.slug, now);
    }}

    function logOrderError(error) {{
      const now = Date.now();
      const last = consoleThrottle.get("__order") || 0;
      if (now - last < errorLogIntervalMs) return;
      console.warn(`[camera-wall] order save error: ${{error}}`);
      consoleThrottle.set("__order", now);
    }}

    function getTile(slug) {{
      return grid.querySelector(`[data-slug="${{slug}}"]`);
    }}

    function animateOrderChange(nextOrder) {{
      const firstRects = new Map(
        cameraOrder
          .map((slug) => [slug, getTile(slug)])
          .filter((entry) => entry[1])
          .map(([slug, tile]) => [slug, tile.getBoundingClientRect()])
      );

      cameraOrder = nextOrder;
      for (const slug of cameraOrder) {{
        const tile = getTile(slug);
        if (tile) grid.appendChild(tile);
      }}
      applyLayout();

      requestAnimationFrame(() => {{
        for (const slug of cameraOrder) {{
          const tile = getTile(slug);
          const first = firstRects.get(slug);
          if (!tile || !first) continue;
          const last = tile.getBoundingClientRect();
          const dx = first.left - last.left;
          const dy = first.top - last.top;
          const sx = first.width / Math.max(last.width, 1);
          const sy = first.height / Math.max(last.height, 1);
          tile.animate(
            [
              {{ transform: `translate(${{dx}}px, ${{dy}}px) scale(${{sx}}, ${{sy}})` }},
              {{ transform: "translate(0, 0) scale(1, 1)" }},
            ],
            {{
              duration: 320,
              easing: "cubic-bezier(.2, .9, .2, 1)",
            }}
          );
        }}
      }});
    }}

    function orderAfterDrop(fromSlug, targetSlug) {{
      if (!fromSlug || !targetSlug || fromSlug === targetSlug) return cameraOrder;
      if (!cameraOrder.includes(fromSlug) || !cameraOrder.includes(targetSlug)) {{
        return cameraOrder;
      }}
      const targetIsFeatured = targetSlug === cameraOrder[cameraOrder.length - 1];
      const nextOrder = cameraOrder.filter((slug) => slug !== fromSlug);
      const insertIndex = targetIsFeatured ? nextOrder.length : nextOrder.indexOf(targetSlug);
      nextOrder.splice(Math.max(insertIndex, 0), 0, fromSlug);
      return nextOrder;
    }}

    function persistOrder() {{
      clearTimeout(orderSaveTimer);
      orderSaveTimer = setTimeout(async () => {{
        try {{
          const response = await fetch("/api/order", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ order: cameraOrder }}),
          }});
          if (!response.ok) throw new Error(`${{response.status}} ${{response.statusText}}`);
        }} catch (error) {{
          logOrderError(error);
        }}
      }}, 250);
    }}

    function clearDropTarget() {{
      for (const tile of grid.querySelectorAll(".drop-target")) {{
        tile.classList.remove("drop-target");
      }}
    }}

    function handleDragStart(event) {{
      if (expandedTile) {{
        event.preventDefault();
        return;
      }}
      draggedSlug = event.currentTarget.dataset.slug;
      didDrag = true;
      if (event.dataTransfer) {{
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", draggedSlug);
      }}
      requestAnimationFrame(() => event.currentTarget.classList.add("dragging"));
    }}

    function handleDragEnter(event) {{
      if (!draggedSlug) return;
      event.preventDefault();
      clearDropTarget();
      event.currentTarget.classList.add("drop-target");
    }}

    function handleDragOver(event) {{
      if (!draggedSlug) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    }}

    function handleDragLeave(event) {{
      if (!event.currentTarget.contains(event.relatedTarget)) {{
        event.currentTarget.classList.remove("drop-target");
      }}
    }}

    function handleDrop(event) {{
      event.preventDefault();
      const fromSlug = draggedSlug || event.dataTransfer?.getData("text/plain");
      const targetSlug = event.currentTarget.dataset.slug;
      const nextOrder = orderAfterDrop(fromSlug, targetSlug);
      if (nextOrder !== cameraOrder) {{
        animateOrderChange(nextOrder);
        persistOrder();
      }}
      handleDragEnd();
    }}

    function handleDragEnd() {{
      clearDropTarget();
      for (const tile of grid.querySelectorAll(".dragging")) {{
        tile.classList.remove("dragging");
      }}
      if (didDrag) {{
        suppressNextClick = true;
        setTimeout(() => {{
          suppressNextClick = false;
        }}, 250);
      }}
      draggedSlug = null;
      didDrag = false;
    }}

    function toggleExpanded(tile) {{
      const first = tile.getBoundingClientRect();
      if (expandedTile && expandedTile !== tile) {{
        expandedTile.classList.remove("expanded");
      }}
      const willExpand = !tile.classList.contains("expanded");
      tile.classList.toggle("expanded", willExpand);
      expandedTile = willExpand ? tile : null;
      const last = tile.getBoundingClientRect();
      const dx = first.left - last.left;
      const dy = first.top - last.top;
      const sx = first.width / Math.max(last.width, 1);
      const sy = first.height / Math.max(last.height, 1);
      tile.animate(
        [
          {{ transform: `translate(${{dx}}px, ${{dy}}px) scale(${{sx}}, ${{sy}})` }},
          {{ transform: "translate(0, 0) scale(1, 1)" }},
        ],
        {{
          duration: 430,
          easing: "cubic-bezier(.2, .9, .2, 1)",
        }}
      );
    }}

    async function updateStatus() {{
      try {{
        const response = await fetch("/api/status", {{cache: "no-store"}});
        const statuses = await response.json();
        for (const status of statuses.cameras) {{
          const tile = document.querySelector(`[data-slug="${{status.slug}}"]`);
          if (!tile) continue;
          logCameraError(status);
          const age = status.age_seconds;
          const ageText = formatRelativeAge(age);

          const liveWindow = status.source === "snapshot" ? 25 : status.source === "webrtc" ? 8 : 8;
          if (status.live && status.has_frame && age !== null && age < liveWindow) {{
            setBadge(tile, "live", "live");
          }} else if (status.has_frame) {{
            setStaleBadge(tile, ageText);
          }} else {{
            setBadge(tile, status.wanted ? "waiting" : "waiting", "waiting");
          }}
        }}
      }} catch (error) {{
        const now = Date.now();
        const last = consoleThrottle.get("__status") || 0;
        if (now - last >= errorLogIntervalMs) {{
          console.warn(`[camera-wall] status error: ${{error}}`);
          consoleThrottle.set("__status", now);
        }}
      }} finally {{
        setTimeout(updateStatus, 2000);
      }}
    }}

    cameras.forEach(buildTile);
    applyLayout();
    window.addEventListener("resize", applyLayout);
    window.addEventListener("beforeunload", () => {{
      for (const camera of cameras) {{
        cleanupWebRTC(camera);
      }}
    }});
    cameras.forEach((camera, index) => {{
      scheduleImage(camera, index * 1200);
      if (camera.source === "webrtc") {{
        setTimeout(() => startWebRTC(camera), index * 1200 + 600);
      }}
    }});
    updateStatus();
  </script>
</body>
</html>
""".encode(
        "utf-8"
    )


class MonitorServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        ha: HomeAssistantClient,
        runners: dict[str, CameraRunner],
        camera_payload: list[dict[str, Any]],
        camera_order: list[str],
    ) -> None:
        super().__init__(server_address, handler_class)
        self.ha = ha
        self.runners = runners
        self.camera_payload_by_slug = {
            camera["slug"]: camera for camera in camera_payload
        }
        self.camera_order = normalize_camera_order(camera_order)
        self.state_lock = threading.Lock()
        self.webrtc_sessions: dict[str, WebRTCSessionProxy] = {}
        self.paused = False

    def get_camera_order(self) -> list[str]:
        with self.state_lock:
            return list(self.camera_order)

    def get_camera_payload(self) -> list[dict[str, Any]]:
        with self.state_lock:
            return [
                self.camera_payload_by_slug[slug]
                for slug in self.camera_order
                if slug in self.camera_payload_by_slug
            ]

    def get_runner_snapshots(self) -> list[dict[str, Any]]:
        with self.state_lock:
            order = list(self.camera_order)
        return [self.runners[slug].snapshot() for slug in order if slug in self.runners]

    def set_camera_order(self, order: list[str]) -> list[str]:
        normalized = save_camera_order(order)
        with self.state_lock:
            self.camera_order = normalized
        return normalized

    def get_webrtc_client_config(self, slug: str) -> dict[str, Any]:
        runner = self.runners.get(slug)
        if runner is None:
            raise KeyError("unknown camera")
        response = ha_ws_call(
            self.ha,
            {
                "type": "camera/webrtc/get_client_config",
                "entity_id": runner.config.entity_id,
            },
        )
        if not response.get("success"):
            raise RuntimeError(str(response.get("error") or response))
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def start_webrtc_session(self, slug: str, offer: str) -> str:
        runner = self.runners.get(slug)
        if runner is None:
            raise KeyError("unknown camera")
        if runner.config.source != "webrtc":
            raise ValueError("camera is not configured for WebRTC")
        cooldown = runner.webrtc_cooldown_seconds()
        if cooldown > 0:
            raise ValueError(f"WebRTC is cooling down for {cooldown}s after a Nest rate limit")

        self.cleanup_webrtc_sessions()
        local_id = uuid.uuid4().hex
        session = WebRTCSessionProxy(local_id, runner, self.ha, offer)
        with self.state_lock:
            for old_id, old_session in list(self.webrtc_sessions.items()):
                if old_session.runner.config.slug == slug:
                    old_session.close()
                    self.webrtc_sessions.pop(old_id, None)
            self.webrtc_sessions[local_id] = session
        try:
            session.start()
        except Exception:
            with self.state_lock:
                self.webrtc_sessions.pop(local_id, None)
            session.close()
            raise
        runner.touch()
        return local_id

    def get_webrtc_events(self, session_id: str) -> list[dict[str, Any]]:
        session = self.get_webrtc_session(session_id)
        return session.pop_events()

    def add_webrtc_candidate(self, session_id: str, candidate: dict[str, Any]) -> None:
        session = self.get_webrtc_session(session_id)
        session.add_candidate(candidate)

    def close_webrtc_session(self, session_id: str) -> None:
        with self.state_lock:
            session = self.webrtc_sessions.pop(session_id, None)
        if session is not None:
            session.close()

    def get_webrtc_session(self, session_id: str) -> WebRTCSessionProxy:
        with self.state_lock:
            session = self.webrtc_sessions.get(session_id)
        if session is None:
            raise KeyError("unknown WebRTC session")
        return session

    def cleanup_webrtc_sessions(self) -> None:
        with self.state_lock:
            expired = [
                session_id
                for session_id, session in self.webrtc_sessions.items()
                if session.expired()
            ]
            sessions = [self.webrtc_sessions.pop(session_id) for session_id in expired]
        for session in sessions:
            session.close()


class Handler(BaseHTTPRequestHandler):
    server: MonitorServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_bytes(
                HTTPStatus.OK,
                "text/html; charset=utf-8",
                render_index(self.server.get_camera_payload()),
                cache=False,
            )
            return

        if parsed.path == "/favicon.ico":
            self._send_bytes(HTTPStatus.NO_CONTENT, "image/x-icon", b"", cache=True)
            return

        if parsed.path == "/.well-known/appspecific/com.chrome.devtools.json":
            self._send_json(HTTPStatus.OK, {})
            return

        if parsed.path == "/api/status":
            payload = {
                "paused": self.server.paused,
                "order": self.server.get_camera_order(),
                "cameras": self.server.get_runner_snapshots(),
            }
            self._send_json(HTTPStatus.OK, payload)
            return

        if parsed.path.startswith("/api/webrtc/client-config/"):
            slug = parsed.path.removeprefix("/api/webrtc/client-config/")
            try:
                self._send_json(HTTPStatus.OK, self.server.get_webrtc_client_config(slug))
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - surfaced to browser console.
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
            return

        if parsed.path.startswith("/api/webrtc/events/"):
            session_id = parsed.path.removeprefix("/api/webrtc/events/")
            try:
                events = self.server.get_webrtc_events(session_id)
                self._send_json(HTTPStatus.OK, {"events": events})
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return

        if parsed.path.startswith("/snapshot/") and parsed.path.endswith(".jpg"):
            slug = parsed.path.removeprefix("/snapshot/").removesuffix(".jpg")
            runner = self.server.runners.get(slug)
            if runner is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown camera"})
                return

            if not self.server.paused:
                runner.touch()

            frame, latest_at, content_type = runner.get_frame()
            if frame is None:
                svg = make_placeholder_svg()
                self._send_bytes(HTTPStatus.OK, "image/svg+xml", svg, cache=False)
                return

            age = time.time() - latest_at
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(frame)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Age-Seconds", f"{age:.1f}")
            self.end_headers()
            self.wfile.write(frame)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/pause":
            self.server.paused = True
            for runner in self.server.runners.values():
                runner.stop_when_idle()
            self._send_json(HTTPStatus.OK, {"paused": True})
            return
        if parsed.path == "/api/resume":
            self.server.paused = False
            self._send_json(HTTPStatus.OK, {"paused": False})
            return
        if parsed.path == "/api/order":
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return

            order = payload.get("order")
            if not isinstance(order, list):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "order must be a list"})
                return

            self._send_json(
                HTTPStatus.OK,
                {
                    "order": self.server.set_camera_order(order),
                },
            )
            return
        if parsed.path.startswith("/api/webrtc/start/"):
            slug = parsed.path.removeprefix("/api/webrtc/start/")
            try:
                payload = self._read_json_body()
                offer = payload.get("offer")
                if not isinstance(offer, str) or not offer:
                    raise ValueError("offer must be a non-empty string")
                session_id = self.server.start_webrtc_session(slug, offer)
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001 - surfaced to browser console.
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
                return
            self._send_json(HTTPStatus.OK, {"session_id": session_id})
            return
        if parsed.path.startswith("/api/webrtc/candidate/"):
            session_id = parsed.path.removeprefix("/api/webrtc/candidate/")
            try:
                payload = self._read_json_body()
                candidate = payload.get("candidate")
                if not isinstance(candidate, dict):
                    raise ValueError("candidate must be an object")
                self.server.add_webrtc_candidate(session_id, candidate)
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001 - surfaced to browser console.
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
                return
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if parsed.path.startswith("/api/webrtc/close/"):
            session_id = parsed.path.removeprefix("/api/webrtc/close/")
            self.server.close_webrtc_session(session_id)
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if parsed.path.startswith("/api/webrtc/frame/"):
            slug = parsed.path.removeprefix("/api/webrtc/frame/")
            runner = self.server.runners.get(slug)
            if runner is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown camera"})
                return
            content_type = self.headers.get("Content-Type", "image/jpeg").split(";", 1)[0]
            try:
                frame = self._read_body(MAX_BROWSER_FRAME_BYTES)
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            detected = detect_image_content_type(frame, content_type)
            if detected not in {"image/jpeg", "image/png"}:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "unsupported image type"})
                return
            runner.receive_browser_frame(frame, detected)
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _read_json_body(self) -> dict[str, Any]:
        body = self._read_body(65536)
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _read_body(self, max_bytes: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length <= 0:
            raise ValueError("empty request body")
        if length > max_bytes:
            raise ValueError("request body too large")
        return self.rfile.read(length)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._send_bytes(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload).encode("utf-8"),
            cache=False,
        )

    def _send_bytes(
        self,
        status: HTTPStatus,
        content_type: str,
        body: bytes,
        *,
        cache: bool,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if not cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def find_port(host: str, preferred_port: int) -> int:
    for port in range(preferred_port, preferred_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No open port found starting at {preferred_port}")


def main() -> None:
    global CACHE_DIR, ORDER_PATH, DEFAULT_CAMERA_ORDER, EUFY_SECURITY_WS_ADDON

    parser = argparse.ArgumentParser(description="Run a local camera monitor wall.")
    parser.add_argument("--ha-url", default=os.environ.get("CAMERA_MONITOR_HA_URL"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--config",
        default=os.environ.get("CAMERA_MONITOR_CONFIG", str(DEFAULT_CONFIG_PATH)),
    )
    parser.add_argument(
        "--cache-dir",
        default=os.environ.get("CAMERA_MONITOR_CACHE_DIR", str(CACHE_DIR)),
    )
    args = parser.parse_args()

    CACHE_DIR = Path(args.cache_dir)
    ORDER_PATH = CACHE_DIR / "layout.json"
    config_ha_url, eufy_addon, cameras = load_monitor_config(Path(args.config))
    DEFAULT_CAMERA_ORDER = tuple(camera.slug for camera in cameras)
    EUFY_SECURITY_WS_ADDON = os.environ.get("CAMERA_MONITOR_EUFY_ADDON") or eufy_addon
    ha_url = args.ha_url or config_ha_url or DEFAULT_HA_URL

    token = os.environ.get("CABIN_HOME_ASSISTANT_TOKEN") or os.environ.get(
        "SUPERVISOR_TOKEN"
    )
    if not token:
        raise SystemExit("CABIN_HOME_ASSISTANT_TOKEN or SUPERVISOR_TOKEN is not set")

    prepare_cache_dir()
    ha = HomeAssistantClient(ha_url, token)
    runners = {camera.slug: CameraRunner(camera, ha) for camera in cameras}
    camera_order = load_camera_order()
    camera_payload = [
        {
            "slug": camera.slug,
            "name": camera.name,
            "entity_id": camera.entity_id,
            "station": camera.station,
            "lan_ip": camera.lan_ip,
            "refresh_ms": camera.refresh_ms,
            "source": camera.source,
            "snapshot_interval": camera.snapshot_interval,
            "stale_ok": camera.stale_ok,
            "stale_ok_seconds": camera.stale_ok_seconds,
            "stale_kick_seconds": camera.stale_kick_seconds,
            "note": camera.note,
        }
        for camera in cameras
    ]

    port = find_port(args.host, args.port)
    server = MonitorServer(
        (args.host, port),
        Handler,
        ha,
        runners,
        camera_payload,
        camera_order,
    )
    print(f"Serving camera monitor at http://{args.host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
