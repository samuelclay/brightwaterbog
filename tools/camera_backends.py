#!/usr/bin/env python3
"""Direct Eufy and Nest backends for the portable camera monitor."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import queue
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
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


EUFY_CONNECT_TIMEOUT_SECONDS = 30.0
EUFY_COMMAND_TIMEOUT_SECONDS = 30.0
EUFY_RECONNECT_SECONDS = 5.0
EUFY_MAX_MESSAGE_BYTES = 16 * 1024 * 1024
EUFY_VIDEO_QUEUE_CHUNKS = 256


class JsonWebSocket:
    """Minimal RFC 6455 text client with bounded message assembly."""

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, url: str) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise ValueError("WebSocket URL must use ws:// or wss:// and include a host")

        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        raw_sock = socket.create_connection(
            (parsed.hostname, port),
            timeout=EUFY_CONNECT_TIMEOUT_SECONDS,
        )
        if parsed.scheme == "wss":
            raw_sock = ssl.create_default_context().wrap_socket(
                raw_sock,
                server_hostname=parsed.hostname,
            )
        raw_sock.settimeout(EUFY_CONNECT_TIMEOUT_SECONDS)
        self.sock = raw_sock
        self.read_buffer = b""
        self.send_lock = threading.Lock()
        self.closed = False

        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.netloc}\r\n"
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
        if (
            f"sec-websocket-accept: {expected}".lower().encode("ascii")
            not in response.lower()
        ):
            raise ConnectionError("WebSocket accept header did not match")
        self.sock.settimeout(None)

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
        message = bytearray()
        while True:
            first, second = self._read_exact(2)
            finished = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            if len(message) + length > EUFY_MAX_MESSAGE_BYTES:
                raise ValueError("WebSocket message exceeded the 16 MiB limit")
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length) if length else b""
            if masked:
                payload = bytes(
                    value ^ mask[index % 4]
                    for index, value in enumerate(payload)
                )
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
            if finished:
                decoded = json.loads(message.decode("utf-8"))
                if not isinstance(decoded, dict):
                    raise ValueError("WebSocket message must be a JSON object")
                return decoded

    def send_json(self, payload: dict[str, Any]) -> None:
        self._send_frame(
            0x1,
            json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        )

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
            masked = bytes(
                value ^ mask[index % 4]
                for index, value in enumerate(payload)
            )
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


@dataclass
class _PendingCommand:
    event: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None


@dataclass
class _EufyStream:
    serial: str
    chunks: queue.Queue[bytes | None] = field(
        default_factory=lambda: queue.Queue(maxsize=EUFY_VIDEO_QUEUE_CHUNKS)
    )
    started: threading.Event = field(default_factory=threading.Event)
    first_video: threading.Event = field(default_factory=threading.Event)
    active: threading.Event = field(default_factory=threading.Event)
    uploader: threading.Thread | None = None
    last_video_at: float = 0.0
    dropped_chunks: int = 0


class DirectEufyClient:
    """Own Eufy P2P sessions and publish their compressed video into go2rtc."""

    def __init__(self, websocket_url: str, go2rtc_url: str) -> None:
        self.websocket_url = websocket_url
        self.go2rtc_url = go2rtc_url.rstrip("/")
        self.connected = threading.Event()
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.ws: JsonWebSocket | None = None
        self.pending: dict[str, _PendingCommand] = {}
        self.streams: dict[str, _EufyStream] = {}
        self.last_error = ""
        self.thread = threading.Thread(
            target=self._connection_loop,
            name="eufy-direct-client",
            daemon=True,
        )

    def start(self) -> None:
        if not self.thread.is_alive():
            self.thread.start()

    def close(self) -> None:
        self.stopping.set()
        with self.lock:
            websocket = self.ws
            serials = list(self.streams)
        for serial in serials:
            self.stop_stream(serial)
        if websocket is not None:
            websocket.close()

    def start_stream(
        self,
        serial: str,
        *,
        wanted: Callable[[], bool] | None = None,
    ) -> None:
        if not serial:
            raise ValueError("Eufy camera needs a device_id")
        if not self.connected.wait(EUFY_CONNECT_TIMEOUT_SECONDS):
            raise ConnectionError(
                f"Eufy service is unavailable: {self.last_error or 'not connected'}"
            )
        with self.lock:
            existing = self.streams.get(serial)
            if existing is not None and existing.active.is_set():
                return
            stream = _EufyStream(serial)
            stream.active.set()
            self.streams[serial] = stream

        livestream_requested = False
        try:
            self._prepare_go2rtc_stream(serial)
            stream.uploader = threading.Thread(
                target=self._upload_stream,
                args=(stream,),
                name=f"eufy-upload-{serial[-6:]}",
                daemon=True,
            )
            stream.uploader.start()
            self._command("device.start_livestream", serial)
            livestream_requested = True
            deadline = time.monotonic() + EUFY_COMMAND_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                if stream.started.wait(0.1) or stream.first_video.is_set():
                    return
                if wanted is not None and not wanted():
                    raise InterruptedError("Eufy stream start was no longer needed")
                if not self.connected.is_set():
                    raise ConnectionError("Eufy service disconnected during stream start")
            raise TimeoutError("Eufy camera did not start sending video")
        except Exception:
            if livestream_requested and self.connected.is_set():
                try:
                    self._command("device.stop_livestream", serial, timeout=5.0)
                except Exception:
                    pass
            self._release_stream(stream)
            raise

    def stop_stream(self, serial: str) -> None:
        with self.lock:
            stream = self.streams.pop(serial, None)
        if stream is None:
            return
        if self.connected.is_set():
            try:
                self._command("device.stop_livestream", serial, timeout=10.0)
            except Exception:
                pass
        self._release_stream(stream)

    def stream_status(self, serial: str) -> dict[str, Any]:
        with self.lock:
            stream = self.streams.get(serial)
            error = self.last_error
        if stream is None:
            return {"active": False, "last_video_at": 0.0, "dropped_chunks": 0, "error": error}
        return {
            "active": stream.active.is_set(),
            "last_video_at": stream.last_video_at,
            "dropped_chunks": stream.dropped_chunks,
            "error": error,
        }

    def _connection_loop(self) -> None:
        while not self.stopping.is_set():
            websocket: JsonWebSocket | None = None
            try:
                websocket = JsonWebSocket(self.websocket_url)
                version = websocket.recv_json()
                if version.get("type") != "version":
                    raise ConnectionError("Eufy service sent an invalid greeting")
                schema = int(version.get("maxSchemaVersion", 2))
                websocket.send_json(
                    {
                        "command": "set_api_schema",
                        "messageId": f"schema.{uuid.uuid4().hex}",
                        "schemaVersion": schema,
                    }
                )
                websocket.send_json(
                    {
                        "command": "start_listening",
                        "messageId": f"listen.{uuid.uuid4().hex}",
                    }
                )
                with self.lock:
                    self.ws = websocket
                    self.last_error = ""
                self.connected.set()
                while not self.stopping.is_set():
                    self._handle_message(websocket.recv_json())
            except Exception as exc:  # noqa: BLE001 - reconnect is intentional.
                with self.lock:
                    self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            finally:
                self.connected.clear()
                with self.lock:
                    if self.ws is websocket:
                        self.ws = None
                    pending = list(self.pending.values())
                    self.pending.clear()
                for command in pending:
                    command.event.set()
                if websocket is not None:
                    websocket.close()
            self.stopping.wait(EUFY_RECONNECT_SECONDS)

    def _handle_message(self, message: dict[str, Any]) -> None:
        if message.get("type") == "result":
            message_id = str(message.get("messageId", ""))
            with self.lock:
                pending = self.pending.get(message_id)
            if pending is not None:
                pending.response = message
                pending.event.set()
            return
        event = message.get("event")
        if message.get("type") != "event" or not isinstance(event, dict):
            return
        if event.get("source") == "driver":
            name = str(event.get("event", ""))
            if name == "connected":
                self.last_error = ""
            elif name == "verify code":
                self.last_error = "Eufy account verification is required"
            elif name == "captcha request":
                self.last_error = "Eufy account captcha verification is required"
            elif name == "connection error":
                self.last_error = "Eufy account authentication failed"
            return
        serial = str(event.get("serialNumber", ""))
        name = str(event.get("event", ""))
        with self.lock:
            stream = self.streams.get(serial)
        if stream is None:
            return
        if name == "livestream started":
            stream.started.set()
            return
        if name == "livestream stopped":
            stream.active.clear()
            self._queue_chunk(stream, None)
            return
        if name != "livestream video data":
            return
        buffer = event.get("buffer")
        values = buffer.get("data") if isinstance(buffer, dict) else None
        if not isinstance(values, list):
            return
        try:
            chunk = bytes(values)
        except ValueError:
            return
        stream.last_video_at = time.time()
        stream.first_video.set()
        self._queue_chunk(stream, chunk)

    def _queue_chunk(self, stream: _EufyStream, chunk: bytes | None) -> None:
        try:
            stream.chunks.put_nowait(chunk)
            return
        except queue.Full:
            pass
        try:
            stream.chunks.get_nowait()
            stream.dropped_chunks += 1
        except queue.Empty:
            pass
        try:
            stream.chunks.put_nowait(chunk)
        except queue.Full:
            stream.dropped_chunks += 1

    def _command(
        self,
        command: str,
        serial: str,
        *,
        timeout: float = EUFY_COMMAND_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        message_id = f"{command}.{uuid.uuid4().hex}"
        pending = _PendingCommand()
        with self.lock:
            websocket = self.ws
            if websocket is None:
                raise ConnectionError("Eufy service is not connected")
            self.pending[message_id] = pending
        try:
            websocket.send_json(
                {
                    "command": command,
                    "messageId": message_id,
                    "serialNumber": serial,
                }
            )
            if not pending.event.wait(timeout):
                raise TimeoutError(f"Eufy command timed out: {command}")
            response = pending.response
            if response is None:
                raise ConnectionError("Eufy service disconnected during command")
            if response.get("success") is not True:
                error = response.get("errorCode") or response.get("error") or "failed"
                raise RuntimeError(f"Eufy command {command} {error}")
            return response
        finally:
            with self.lock:
                self.pending.pop(message_id, None)

    def _prepare_go2rtc_stream(self, serial: str) -> None:
        self._go2rtc_request("DELETE", "/api/streams", {"src": serial})
        self._go2rtc_request(
            "PUT",
            "/api/streams",
            {"name": serial, "src": "tcp://127.0.0.1:65535"},
        )

    def _go2rtc_request(
        self,
        method: str,
        path: str,
        params: dict[str, str],
    ) -> None:
        url = f"{self.go2rtc_url}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read(2048)
        except urllib.error.HTTPError as exc:
            if method == "DELETE" and exc.code == 404:
                return
            raise

    def _upload_stream(self, stream: _EufyStream) -> None:
        target = urllib.parse.urlsplit(self.go2rtc_url)
        connection_class = (
            http.client.HTTPSConnection
            if target.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_class(
            target.hostname,
            target.port or (443 if target.scheme == "https" else 80),
            timeout=30,
        )
        path = "/api/stream?" + urllib.parse.urlencode({"dst": stream.serial})
        try:
            connection.putrequest("POST", path)
            connection.putheader("Content-Type", "application/octet-stream")
            connection.putheader("Transfer-Encoding", "chunked")
            connection.endheaders()
            while stream.active.is_set() or not stream.chunks.empty():
                try:
                    chunk = stream.chunks.get(timeout=0.5)
                except queue.Empty:
                    continue
                if chunk is None:
                    break
                connection.send(f"{len(chunk):X}\r\n".encode("ascii"))
                connection.send(chunk)
                connection.send(b"\r\n")
            connection.send(b"0\r\n\r\n")
            response = connection.getresponse()
            response.read(2048)
            if response.status >= 400 and stream.active.is_set():
                self.last_error = f"go2rtc publish returned {response.status}"
        except Exception as exc:  # noqa: BLE001 - runner observes producer stall.
            if stream.active.is_set():
                self.last_error = f"go2rtc publish: {type(exc).__name__}: {exc}"[:300]
        finally:
            connection.close()

    def _release_stream(self, stream: _EufyStream) -> None:
        stream.active.clear()
        self._queue_chunk(stream, None)
        if stream.uploader is not None and stream.uploader is not threading.current_thread():
            stream.uploader.join(timeout=5)
        with self.lock:
            if self.streams.get(stream.serial) is stream:
                self.streams.pop(stream.serial, None)


@dataclass(frozen=True)
class NestCredentials:
    client_id: str
    client_secret: str
    refresh_token: str
    project_id: str

    @classmethod
    def from_environment(cls) -> "NestCredentials":
        names = {
            "client_id": "CAMERA_NEST_CLIENT_ID",
            "client_secret": "CAMERA_NEST_CLIENT_SECRET",
            "refresh_token": "CAMERA_NEST_REFRESH_TOKEN",
            "project_id": "CAMERA_NEST_PROJECT_ID",
        }
        values = {field: os.environ.get(name, "").strip() for field, name in names.items()}
        missing = [names[field] for field, value in values.items() if not value]
        if missing:
            raise RuntimeError("Missing direct Nest credentials: " + ", ".join(missing))
        return cls(**values)


def nest_stream_name(slug: str) -> str:
    return f"nest_{slug}"


def nest_device_key(device_id: str) -> str:
    """Return the bare SDM device key expected by go2rtc's Nest source."""
    value = device_id.strip().strip("/")
    marker = "/devices/"
    if marker in value:
        value = value.rsplit(marker, 1)[1]
    if not value or "/" in value:
        raise ValueError("Nest camera has an invalid device_id")
    return value


def build_nest_source(credentials: NestCredentials, device_id: str) -> str:
    device_key = nest_device_key(device_id)
    return "nest:?" + urllib.parse.urlencode(
        {
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "refresh_token": credentials.refresh_token,
            "project_id": credentials.project_id,
            "device_id": device_key,
        }
    )


def configure_nest_streams(
    go2rtc_url: str,
    cameras: Iterable[Any],
    credentials: NestCredentials,
) -> None:
    for camera in cameras:
        if camera.source != "nest":
            continue
        params = urllib.parse.urlencode(
            {
                "name": nest_stream_name(camera.slug),
                "src": build_nest_source(credentials, camera.device_id),
            }
        )
        request = urllib.request.Request(
            f"{go2rtc_url.rstrip('/')}/api/streams?{params}",
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read(2048)
