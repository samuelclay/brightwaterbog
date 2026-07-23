#!/usr/bin/env python3
"""Portable camera wall with direct Eufy and Nest backends."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import select
import socket
import ssl
import struct
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, fields
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from camera_backends import (
    DirectEufyClient,
    JsonWebSocket,
    NestCredentials,
    configure_nest_streams,
    nest_stream_name,
)


DEFAULT_CONFIG_PATH = Path(__file__).resolve().with_name("camera_monitor.local.json")
VIEWER_TTL_SECONDS = 90
SOCKET_TIMEOUT_SECONDS = 12
CACHE_WRITE_INTERVAL_SECONDS = 30.0
VIEWER_ACTIVITY_WRITE_INTERVAL_SECONDS = 60.0
DEFAULT_WARM_IDLE_HOURS = 48.0
DEFAULT_EUFY_VIEWER_SLOTS = 2
DEFAULT_EUFY_THUMBNAIL_REFRESH_SECONDS = 20.0
EUFY_THUMBNAIL_ATTEMPT_TIMEOUT_SECONDS = 15.0
EUFY_THUMBNAIL_RETRY_BASE_SECONDS = 20.0
EUFY_THUMBNAIL_RETRY_MAX_SECONDS = 5 * 60.0
DIRECT_STATUS_INTERVAL_SECONDS = 2.0
DIRECT_START_TIMEOUT_SECONDS = 25.0
DIRECT_ACTIVITY_STALE_SECONDS = 6.0
DIRECT_STABILITY_SECONDS = 8.0
NEST_START_TIMEOUT_SECONDS = 10 * 60.0
NATIVE_STARTER_MSE_CODECS = (
    "avc1.640029,avc1.64002A,avc1.640033,avc1.4D401F,avc1.42E01E"
)
MAX_BROWSER_FRAME_BYTES = 2_500_000
MAX_MJPEG_FRAME_BYTES = 5_000_000
WEBSOCKET_PROXY_IDLE_SECONDS = 90
GO2RTC_BROWSER_MODULES = frozenset(
    {"/go2rtc/video-stream.js", "/go2rtc/video-rtc.js"}
)
MAX_GO2RTC_BROWSER_MODULE_BYTES = 256 * 1024
WARM_AGENT_HEARTBEAT_SECONDS = 15
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "camera_monitor"
LEGACY_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "eufy_monitor"
CACHE_DIR = Path(os.environ.get("CAMERA_MONITOR_CACHE_DIR", DEFAULT_CACHE_DIR))
ORDER_PATH = CACHE_DIR / "layout.json"
VIEWER_ACTIVITY_PATH = CACHE_DIR / "viewer_activity.json"
GO2RTC_URL = "http://go2rtc:1984"
STALE_KICK_SECONDS = 5 * 60
STALE_KICK_COOLDOWN_SECONDS = 3 * 60
KICK_STOP_SETTLE_SECONDS = 3.0
EUFY_RETRY_BACKOFF_MAX_SECONDS = 5 * 60
START_GATE = threading.Semaphore(1)


def prepare_cache_dir() -> None:
    if CACHE_DIR.exists() or not LEGACY_CACHE_DIR.exists():
        return
    try:
        LEGACY_CACHE_DIR.rename(CACHE_DIR)
    except Exception as exc:  # noqa: BLE001 - losing cache should not block viewing.
        print(f"Unable to migrate legacy cache directory: {exc}", flush=True)


def load_viewer_activity() -> float:
    try:
        payload = json.loads(VIEWER_ACTIVITY_PATH.read_text(encoding="utf-8"))
        last_viewed_at = float(payload.get("last_viewed_at", 0))
        if last_viewed_at > 0:
            return last_viewed_at
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return time.time()


def save_viewer_activity(last_viewed_at: float) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = VIEWER_ACTIVITY_PATH.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps({"last_viewed_at": last_viewed_at}),
            encoding="utf-8",
        )
        tmp_path.replace(VIEWER_ACTIVITY_PATH)
    except Exception as exc:  # noqa: BLE001 - activity persistence is best effort.
        print(f"Unable to persist viewer activity: {exc}", flush=True)


def detect_image_content_type(frame: bytes, fallback: str = "image/jpeg") -> str:
    if frame.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if frame.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if frame.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return fallback.split(";", 1)[0] or "application/octet-stream"


def is_websocket_close_frame(data: bytes, *, masked: bool) -> bool:
    return (
        len(data) >= 2
        and data[0] & 0x8F == 0x88
        and bool(data[1] & 0x80) is masked
    )


def is_placeholder_snapshot(frame: bytes, content_type: str) -> bool:
    return content_type == "image/png" and len(frame) < 10_000


def frame_fingerprint(frame: bytes) -> str:
    return hashlib.blake2b(frame, digest_size=16).hexdigest()


def resolve_go2rtc_url() -> str:
    return os.environ.get("CAMERA_MONITOR_GO2RTC_URL", GO2RTC_URL).strip().rstrip("/")


def direct_websocket_url(camera: "CameraConfig") -> str:
    if not GO2RTC_URL:
        return ""
    query = urllib.parse.urlencode({"src": browser_stream_name(camera)})
    return f"/go2rtc/api/ws?{query}"


def browser_stream_name(camera: "CameraConfig") -> str:
    return f"camera_{camera.slug}_native"


def upstream_stream_name(camera: "CameraConfig") -> str:
    return camera.device_id if camera.source == "eufy" else nest_stream_name(camera.slug)


@dataclass(frozen=True)
class CameraConfig:
    slug: str
    name: str
    device_id: str
    lan_ip: str = ""
    retry_delay: float = 6.0
    start_delay: float = 12.0
    refresh_ms: int = 1000
    source: str = "eufy"
    snapshot_interval: float = 10.0
    stale_ok: bool = False
    stale_ok_seconds: int = 120
    stale_kick_seconds: int = STALE_KICK_SECONDS
    keep_warm: bool = False
    auto_start: bool = True
    note: str = ""


CAMERA_CONFIG_FIELDS = {field.name for field in fields(CameraConfig)}
CAMERA_SOURCES = {"eufy", "nest"}
DEFAULT_CAMERA_ORDER: tuple[str, ...] = ()


def load_monitor_config(config_path: Path) -> tuple[CameraConfig, ...]:
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
        if not camera.slug or not camera.name or not camera.device_id:
            raise SystemExit(
                f"Camera config entry {index} must include slug, name, and device_id"
            )
        if camera.slug in seen_slugs:
            raise SystemExit(f"Duplicate camera slug in config: {camera.slug}")
        if camera.source not in CAMERA_SOURCES:
            raise SystemExit(
                f"Camera {camera.slug} has unsupported source {camera.source!r}"
            )
        seen_slugs.add(camera.slug)
        cameras.append(camera)

    return tuple(cameras)


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


class CameraRunner:
    def __init__(
        self,
        config: CameraConfig,
        eufy: DirectEufyClient | None,
    ) -> None:
        self.config = config
        self.eufy = eufy
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.viewer_wanted_until = 0.0
        self.warm_wanted_until = 0.0
        self.cache_path = CACHE_DIR / f"{self.config.slug}.jpg"
        self.cache_meta_path = CACHE_DIR / f"{self.config.slug}.json"
        (
            self.latest_frame,
            self.latest_at,
            self.latest_content_type,
            self.latest_fingerprint,
            self.latest_changed_at,
        ) = self._load_cached_frame()
        self.cache_written_at = self.latest_at
        self.latest_transport_at = 0.0
        self.live = False
        self.last_error = ""
        self.last_start_status: int | None = None
        self.retry_count = 0
        self.consecutive_failure_count = 0
        self.kick_count = 0
        self.started_at = 0.0
        self.last_attempt_at = 0.0
        self.last_kick_at = 0.0

    def touch(self, role: str = "viewer") -> None:
        with self.lock:
            if role == "warm":
                self.warm_wanted_until = max(
                    self.warm_wanted_until,
                    time.time() + VIEWER_TTL_SECONDS,
                )
            else:
                self.viewer_wanted_until = max(
                    self.viewer_wanted_until,
                    time.time() + VIEWER_TTL_SECONDS,
                )
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
            self.viewer_wanted_until = 0.0
            self.warm_wanted_until = 0.0

    def stop_warm(self) -> None:
        with self.lock:
            self.warm_wanted_until = 0.0

    def receive_browser_frame(self, frame: bytes, content_type: str) -> None:
        self._set_state(live=True, error="", frame=frame, content_type=content_type)

    def set_external_error(self, error: str) -> None:
        self._record_failure()
        self._set_state(live=False, error=error)

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            latest_received_at = self.latest_at
            latest_changed_at = self.latest_changed_at
            return {
                "slug": self.config.slug,
                "name": self.config.name,
                "lan_ip": self.config.lan_ip,
                "source": self.config.source,
                "go2rtc_mode": "webrtc" if self.config.source == "nest" else "mse",
                "live": self.live,
                "wanted": now < max(
                    self.viewer_wanted_until,
                    self.warm_wanted_until,
                ),
                "viewer_wanted": now < self.viewer_wanted_until,
                "warm_wanted": now < self.warm_wanted_until,
                "has_frame": self.latest_frame is not None,
                "age_seconds": (
                    None if latest_changed_at <= 0 else round(now - latest_changed_at, 1)
                ),
                "latest_at": None if latest_changed_at <= 0 else latest_changed_at,
                "received_age_seconds": (
                    None
                    if latest_received_at <= 0
                    else round(now - latest_received_at, 1)
                ),
                "latest_received_at": (
                    None if latest_received_at <= 0 else latest_received_at
                ),
                "transport_age_seconds": (
                    None
                    if self.latest_transport_at <= 0
                    else round(now - self.latest_transport_at, 1)
                ),
                "latest_transport_at": (
                    None
                    if self.latest_transport_at <= 0
                    else self.latest_transport_at
                ),
                "last_error": self.last_error,
                "last_start_status": self.last_start_status,
                "retry_count": self.retry_count,
                "consecutive_failure_count": self.consecutive_failure_count,
                "kick_count": self.kick_count,
                "last_kick_at": None if self.last_kick_at <= 0 else self.last_kick_at,
                "refresh_ms": self.config.refresh_ms,
                "snapshot_interval": self.config.snapshot_interval,
                "stale_ok": self.config.stale_ok,
                "stale_ok_seconds": self.config.stale_ok_seconds,
                "stale_kick_seconds": self.config.stale_kick_seconds,
                "keep_warm": self.config.keep_warm,
                "auto_start": self.config.auto_start,
                "note": self.config.note,
            }

    def get_frame(self) -> tuple[bytes | None, float, str]:
        with self.lock:
            return self.latest_frame, self.latest_changed_at, self.latest_content_type

    def _wanted(self) -> bool:
        with self.lock:
            return time.time() < max(
                self.viewer_wanted_until,
                self.warm_wanted_until,
            )

    def _set_state(
        self,
        *,
        live: bool | None = None,
        error: str | None = None,
        start_status: int | None = None,
        frame: bytes | None = None,
        content_type: str = "image/jpeg",
        transport_activity: bool = False,
    ) -> None:
        now = time.time()
        fingerprint = frame_fingerprint(frame) if frame is not None else ""
        if frame is not None:
            content_type = detect_image_content_type(frame, content_type)
        with self.lock:
            if live is not None:
                self.live = live
            if error is not None:
                self.last_error = error[:300]
            if start_status is not None:
                self.last_start_status = start_status
            if transport_activity:
                self.latest_transport_at = now
                self.consecutive_failure_count = 0
            if frame is not None:
                frame_changed = fingerprint != self.latest_fingerprint
                self.latest_frame = frame
                self.latest_at = now
                self.latest_content_type = content_type
                if frame_changed:
                    self.latest_fingerprint = fingerprint
                    self.latest_changed_at = now
                    self.kick_count = 0
                self.consecutive_failure_count = 0
                if now - self.cache_written_at >= CACHE_WRITE_INTERVAL_SECONDS:
                    self._write_cached_frame(
                        frame,
                        now,
                        content_type,
                        self.latest_fingerprint,
                        self.latest_changed_at,
                    )
                    self.cache_written_at = now

    def _load_cached_frame(self) -> tuple[bytes | None, float, str, str, float]:
        content_type = "image/jpeg"
        try:
            if not self.cache_path.exists():
                return None, 0.0, content_type, "", 0.0
            latest_at = self.cache_path.stat().st_mtime
            latest_changed_at = latest_at
            fingerprint = ""
            if self.cache_meta_path.exists():
                metadata = json.loads(self.cache_meta_path.read_text(encoding="utf-8"))
                latest_at = float(
                    metadata.get("latest_received_at", metadata.get("latest_at", latest_at))
                )
                latest_changed_at = float(
                    metadata.get("latest_changed_at", metadata.get("latest_at", latest_at))
                )
                content_type = str(metadata.get("content_type", content_type))
                fingerprint = str(metadata.get("fingerprint", ""))
            frame = self.cache_path.read_bytes()
            content_type = detect_image_content_type(frame, content_type)
            if not fingerprint:
                fingerprint = frame_fingerprint(frame)
            return frame, latest_at, content_type, fingerprint, latest_changed_at
        except Exception as exc:  # noqa: BLE001 - cache should never stop live viewing.
            print(f"Unable to load cache for {self.config.slug}: {exc}", flush=True)
            return None, 0.0, content_type, "", 0.0

    def _write_cached_frame(
        self,
        frame: bytes,
        latest_received_at: float,
        content_type: str,
        fingerprint: str,
        latest_changed_at: float,
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
                        "latest_at": latest_changed_at,
                        "latest_changed_at": latest_changed_at,
                        "latest_received_at": latest_received_at,
                        "fingerprint": fingerprint,
                        "content_type": content_type,
                    }
                ),
                encoding="utf-8",
            )
            tmp_meta_path.replace(self.cache_meta_path)
        except Exception as exc:  # noqa: BLE001 - cache should never stop live viewing.
            print(f"Unable to write cache for {self.config.slug}: {exc}", flush=True)

    def _run(self) -> None:
        if self.config.source == "nest":
            self._run_native_watchdog()
            return

        if self.eufy is None:
            self._set_state(live=False, error="direct Eufy service is unavailable")
            return

        while self._wanted():
            self.last_attempt_at = time.time()
            stream_claimed = False
            gate_released = False

            def release_start_gate() -> None:
                nonlocal gate_released
                if not gate_released:
                    START_GATE.release()
                    gate_released = True

            START_GATE.acquire()
            try:
                self._set_state(live=False, error="starting direct Eufy stream")
                self.eufy.start_stream(self.config.device_id, wanted=self._wanted)
                stream_claimed = True
                self.started_at = time.time()
                self._set_state(live=False, error="waiting for stable go2rtc media")
                self._sleep_while_wanted(self.config.start_delay)
                if self._wanted():
                    self._read_go2rtc_until_idle(on_first_frame=release_start_gate)
            except Exception as exc:  # noqa: BLE001 - status shows backend failures.
                if self._wanted():
                    self._record_failure()
                    self._set_state(live=False, error=f"{type(exc).__name__}: {exc}")
            finally:
                release_start_gate()
                self._set_state(live=False)
                if stream_claimed:
                    self.eufy.stop_stream(self.config.device_id)

            if self._wanted():
                self._sleep_while_wanted(self._retry_delay())

        self._set_state(live=False)

    def _run_native_watchdog(self) -> None:
        while self._wanted():
            starter: JsonWebSocket | None = None
            try:
                starter, starter_closed = self._start_native_source()
                self._wait_for_native_handoff(starter_closed)
                self._read_go2rtc_until_idle(assume_stable=True)
            except Exception as exc:  # noqa: BLE001 - status shows backend failures.
                self._record_failure()
                self._set_state(live=False, error=f"{type(exc).__name__}: {exc}")
            finally:
                if starter is not None:
                    starter.close()
            if self._wanted():
                self._sleep_while_wanted(self._retry_delay())
        self._set_state(live=False)

    def _start_native_source(self) -> tuple[JsonWebSocket, threading.Event]:
        target = urllib.parse.urlsplit(GO2RTC_URL)
        if target.scheme not in {"http", "https"} or not target.hostname:
            raise RuntimeError("direct go2rtc URL is not configured")
        scheme = "wss" if target.scheme == "https" else "ws"
        path = target.path.rstrip("/") + "/api/ws"
        query = urllib.parse.urlencode({"src": upstream_stream_name(self.config)})
        websocket_url = urllib.parse.urlunsplit(
            (scheme, target.netloc, path, query, "")
        )
        starter = JsonWebSocket(websocket_url)
        starter.send_json(
            {"type": "mse", "value": NATIVE_STARTER_MSE_CODECS}
        )
        closed = threading.Event()

        def drain() -> None:
            try:
                while True:
                    starter.recv_json()
            except Exception:
                pass
            finally:
                closed.set()

        threading.Thread(
            target=drain,
            name=f"camera-starter-{self.config.slug}",
            daemon=True,
        ).start()
        return starter, closed

    def _wait_for_native_handoff(self, starter_closed: threading.Event) -> None:
        stream_status_url = f"{GO2RTC_URL}/api/streams"
        started_at = time.monotonic()
        activity_started_at = 0.0
        last_producer_signature: tuple[str, ...] = ()
        last_bytes_received = -1
        while self._wanted():
            if starter_closed.is_set():
                raise ConnectionError("go2rtc starter closed before media was ready")
            with urllib.request.urlopen(stream_status_url, timeout=5) as response:
                payload = json.load(response)
            stream_status = payload.get(upstream_stream_name(self.config), {})
            if not isinstance(stream_status, dict):
                stream_status = {}
            active_producers = [
                producer
                for producer in (stream_status.get("producers") or [])
                if isinstance(producer, dict) and producer.get("id")
            ]
            signature = tuple(
                sorted(str(producer["id"]) for producer in active_producers)
            )
            bytes_received = sum(
                max(0, int(producer.get("bytes_recv") or 0))
                for producer in active_producers
            )
            now = time.monotonic()
            has_activity = bool(active_producers) and (
                signature != last_producer_signature
                or bytes_received > last_bytes_received
            )
            if has_activity:
                if activity_started_at <= 0:
                    activity_started_at = now
                stable = now - activity_started_at >= DIRECT_STABILITY_SECONDS
                self._set_state(
                    live=stable,
                    error="" if stable else "waiting for stable go2rtc media",
                    transport_activity=True,
                )
                consumers = stream_status.get("consumers") or []
                if stable and len(consumers) >= 2:
                    return
            else:
                activity_started_at = 0.0
                self._set_state(live=False, error="waiting for go2rtc producer")
            if now - started_at >= NEST_START_TIMEOUT_SECONDS:
                raise TimeoutError("Nest stream did not warm before the timeout")
            last_producer_signature = signature
            last_bytes_received = bytes_received
            self._sleep_while_wanted(DIRECT_STATUS_INTERVAL_SECONDS)

    def _record_failure(self) -> None:
        with self.lock:
            self.retry_count += 1
            self.consecutive_failure_count += 1

    def _retry_delay(self) -> float:
        with self.lock:
            failure_count = self.consecutive_failure_count
        exponent = max(0, min(failure_count - 1, 8))
        return min(
            max(1.0, self.config.retry_delay) * (2**exponent),
            EUFY_RETRY_BACKOFF_MAX_SECONDS,
        )

    def _sleep_while_wanted(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while self._wanted() and time.time() < deadline:
            time.sleep(min(0.5, deadline - time.time()))

    def _read_go2rtc_until_idle(
        self,
        on_first_frame: Callable[[], None] | None = None,
        assume_stable: bool = False,
    ) -> None:
        if not GO2RTC_URL:
            raise RuntimeError("direct go2rtc URL is not configured")

        stream_status_url = f"{GO2RTC_URL}/api/streams"
        started_waiting_at = time.monotonic()
        last_activity_at = started_waiting_at
        activity_started_at = (
            started_waiting_at - DIRECT_STABILITY_SECONDS if assume_stable else 0.0
        )
        last_producer_signature: tuple[str, ...] = ()
        last_bytes_received = -1
        ready = assume_stable
        while self._wanted():
            with urllib.request.urlopen(stream_status_url, timeout=5) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise ValueError("go2rtc returned invalid stream status")
            stream_status = payload.get(upstream_stream_name(self.config), {})
            if not isinstance(stream_status, dict):
                stream_status = {}

            active_producers = [
                producer
                for producer in (stream_status.get("producers") or [])
                if isinstance(producer, dict) and producer.get("id")
            ]
            signature = tuple(
                sorted(str(producer["id"]) for producer in active_producers)
            )
            bytes_received = sum(
                max(0, int(producer.get("bytes_recv") or 0))
                for producer in active_producers
            )
            now = time.monotonic()
            has_activity = bool(active_producers) and (
                signature != last_producer_signature
                or bytes_received > last_bytes_received
            )
            if has_activity:
                if activity_started_at <= 0:
                    activity_started_at = now
                last_activity_at = now
                stable = now - activity_started_at >= DIRECT_STABILITY_SECONDS
                self._set_state(
                    live=stable,
                    error="" if stable else "waiting for stable go2rtc media",
                    transport_activity=True,
                )
                if not ready:
                    ready = True
                    if on_first_frame is not None:
                        on_first_frame()
            else:
                activity_started_at = 0.0
                error = (
                    "waiting for go2rtc producer"
                    if not active_producers
                    else "go2rtc producer stopped receiving media"
                )
                self._set_state(live=False, error=error)
                timeout = (
                    DIRECT_ACTIVITY_STALE_SECONDS
                    if ready
                    else DIRECT_START_TIMEOUT_SECONDS
                )
                if now - last_activity_at >= timeout:
                    raise ConnectionError(error)

            last_producer_signature = signature
            last_bytes_received = bytes_received
            self._sleep_while_wanted(DIRECT_STATUS_INTERVAL_SECONDS)


APP_ICON_TOUCH_SIZE = 180
_ICON_CACHE: dict[int, bytes] = {}
_ICON_CACHE_LOCK = threading.Lock()


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack("!I", len(data))
        + tag
        + data
        + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def encode_png_rgb(width: int, height: int, pixels: bytearray) -> bytes:
    """Encode raw RGB pixel bytes as a PNG using only the standard library."""
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # filter type 0 (none) for each scanline
        raw.extend(pixels[y * stride : (y + 1) * stride])
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (
        signature
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )


def _render_app_icon(size: int) -> bytes:
    """Draw a dark camera-lens home-screen icon matching the monitor palette."""
    cx = size / 2.0
    cy = size / 2.0
    aa = size / 340.0  # ~1.5px soft edge at 512
    grad_cx = size * 0.5
    grad_cy = size * 0.40
    grad_max = size * 0.72
    bg_in = (0x21, 0x2A, 0x33)
    bg_out = (0x05, 0x07, 0x0A)
    lens_fill = (0x0B, 0x10, 0x15)
    ring_col = (0xD2, 0xDC, 0xE6)
    accent = (0x42, 0xD3, 0x92)
    glint = (0x8A, 0x97, 0xA4)

    ring_mid = size * 0.315
    ring_half = size * 0.030
    inner_r = ring_mid - ring_half
    glint_cx = cx - inner_r * 0.34
    glint_cy = cy - inner_r * 0.40
    glint_r = inner_r * 0.16
    arc_start = math.radians(150)  # accent segment across the left of the ring
    arc_end = math.radians(250)
    two_pi = 2 * math.pi

    pixels = bytearray(size * size * 3)
    i = 0
    for y in range(size):
        fy = y + 0.5
        for x in range(size):
            fx = x + 0.5
            gdx = fx - grad_cx
            gdy = fy - grad_cy
            t = min(1.0, ((gdx * gdx + gdy * gdy) ** 0.5) / grad_max)
            r = bg_in[0] + (bg_out[0] - bg_in[0]) * t
            g = bg_in[1] + (bg_out[1] - bg_in[1]) * t
            b = bg_in[2] + (bg_out[2] - bg_in[2]) * t

            dx = fx - cx
            dy = fy - cy
            d = (dx * dx + dy * dy) ** 0.5

            fill_cov = min(1.0, max(0.0, 0.5 - (d - inner_r) / aa))
            if fill_cov > 0:
                r += (lens_fill[0] - r) * fill_cov
                g += (lens_fill[1] - g) * fill_cov
                b += (lens_fill[2] - b) * fill_cov
                gdist = ((fx - glint_cx) ** 2 + (fy - glint_cy) ** 2) ** 0.5
                gl = min(1.0, max(0.0, 0.5 - (gdist - glint_r) / aa)) * 0.5 * fill_cov
                if gl > 0:
                    r += (glint[0] - r) * gl
                    g += (glint[1] - g) * gl
                    b += (glint[2] - b) * gl

            ring_cov = min(1.0, max(0.0, 0.5 - (abs(d - ring_mid) - ring_half) / aa))
            if ring_cov > 0:
                ang = math.atan2(dy, dx)
                if ang < 0:
                    ang += two_pi
                col = accent if arc_start <= ang <= arc_end else ring_col
                r += (col[0] - r) * ring_cov
                g += (col[1] - g) * ring_cov
                b += (col[2] - b) * ring_cov

            pixels[i] = min(255, max(0, int(r + 0.5)))
            pixels[i + 1] = min(255, max(0, int(g + 0.5)))
            pixels[i + 2] = min(255, max(0, int(b + 0.5)))
            i += 3
    return encode_png_rgb(size, size, pixels)


def app_icon_png(size: int) -> bytes:
    with _ICON_CACHE_LOCK:
        cached = _ICON_CACHE.get(size)
    if cached is not None:
        return cached
    data = _render_app_icon(size)
    with _ICON_CACHE_LOCK:
        _ICON_CACHE[size] = data
    return data


def render_manifest() -> bytes:
    return json.dumps(
        {
            "name": "Brightwater Camera Monitor",
            "short_name": "Cameras",
            "display": "standalone",
            "orientation": "landscape",
            "start_url": "/",
            "scope": "/",
            "background_color": "#000000",
            "theme_color": "#000000",
            "icons": [
                {
                    "src": "/icons/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": "/icons/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
            ],
        }
    ).encode("utf-8")


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
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Brightwater Camera Monitor</title>
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="Cameras">
  <meta name="theme-color" content="#000000">
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="apple-touch-icon" href="/apple-touch-icon.png">
  <link rel="icon" type="image/png" sizes="192x192" href="/icons/icon-192.png">
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
    .tile canvas[data-role="focus-frame"],
    .tile video,
    .tile video-stream {{
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
      background: #050607;
      pointer-events: none;
      -webkit-user-drag: none;
    }}
    .tile img[data-role="image"] {{
      position: absolute;
      inset: 0;
      z-index: 1;
      opacity: 0;
    }}
    .tile img[data-role="image"].snapshot-active {{
      opacity: 1;
    }}
    .tile img[data-role="image"].snapshot-entering {{
      z-index: 2;
    }}
    .tile canvas[data-role="focus-frame"] {{
      position: absolute;
      inset: 0;
      z-index: 2;
    }}
    .tile video,
    .tile canvas[data-role="focus-frame"],
    .tile video-stream {{
      display: none;
    }}
    .tile.direct-live img {{
      display: none;
    }}
    .tile.direct-mse-live img {{
      display: block;
    }}
    .tile.direct-mse-live > video[data-role="video"] {{
      display: block;
      position: absolute;
      inset: 0;
      z-index: 0;
      opacity: 0;
    }}
    .tile.direct-focus-pending img {{
      display: block;
    }}
    .tile.direct-focus-pending > video[data-role="video"] {{
      display: none;
    }}
    .tile.direct-focus-frame-live img {{
      display: none;
    }}
    .tile.direct-focus-frame-live > canvas[data-role="focus-frame"] {{
      display: block;
    }}
    .tile.direct-focus-frame-live > video[data-role="video"] {{
      display: none;
    }}
    .tile.direct-webrtc-live > video-stream {{
      display: block;
    }}
    .tile video-stream .info {{
      display: none;
    }}
    .tile.expanded {{
      position: fixed;
      inset: 0;
      z-index: 20;
      background: #000;
    }}
    .tile.expanded img,
    .tile.expanded video,
    .tile.expanded video-stream {{
      object-fit: contain;
    }}
    .hud {{
      position: absolute;
      right: 16px;
      bottom: 14px;
      z-index: 3;
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
  <script type="module" src="/go2rtc/video-stream.js"></script>
  <script>
    const allCameras = {cameras_json};
    const pageParams = new URLSearchParams(window.location.search);
    const sentinelMode = pageParams.get("sentinel") === "1";
    const sentinelCameraSlug = sentinelMode ? pageParams.get("camera") : "";
    const cameras = sentinelCameraSlug
      ? allCameras.filter((camera) => camera.slug === sentinelCameraSlug)
      : sentinelMode
        ? allCameras.filter(
            (camera) => camera.source === "nest" && camera.keep_warm,
          )
        : allCameras;
    const grid = document.getElementById("grid");
    let cameraOrder = cameras.map((camera) => camera.slug);
    let paused = false;
    let expandedTile = null;
    let draggedSlug = null;
    let didDrag = false;
    let suppressNextClick = false;
    let orderSaveTimer = null;
    let focusedSlug = "";
    let pageVisible = !document.hidden;
    const viewerId = window.crypto?.randomUUID?.()
      || `viewer-${{Date.now()}}-${{Math.random().toString(16).slice(2)}}`;
    const imageTimers = new Map();
    const imageEtags = new Map();
    const imageObjectUrls = new Map();
    const imageSwapVersions = new Map();
    const imageSwapAnimations = new Map();
    const directStates = new Map();
    const consoleThrottle = new Map();
    const snapshotProbeCanvas = document.createElement("canvas");
    snapshotProbeCanvas.width = 24;
    snapshotProbeCanvas.height = 14;
    const snapshotProbeContext = snapshotProbeCanvas.getContext(
      "2d",
      {{alpha: false}},
    );
    const errorLogIntervalMs = 60000;
    const captureFrameWidth = 960;
    const captureFrameQuality = 0.72;
    const eufyCaptureIntervalMs = 1000;
    const directCaptureIntervalMs = 30000;
    const directQueueLimitBytes = 16 * 1024 * 1024;
    const initialImageStaggerMs = 20;
    const imageRevealMs = 70;
    const directMseCodecs = [
      "avc1.640029",
      "avc1.64002A",
      "avc1.640033",
      "hvc1.1.6.L153.B0",
    ];

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
        const total = cameraOrder.length;
        const cols = mobile ? 1 : Math.min(4, Math.max(2, Math.ceil((total - 1) / 2)));
        // The bottom row holds the large feature tiles. Prefer two large tiles
        // sharing the bottom (e.g. 10 cameras -> 8 in a 2x4 grid above, 2 large
        // below) whenever the remaining cameras fill the grid with no orphan
        // row; otherwise fall back to a single full-width feature tile.
        let featuredCount = 1;
        let topRows;
        if (mobile) {{
          topRows = total;
        }} else {{
          const fit = [2, 1].find(f => f < total && cols % f === 0 && (total - f) % cols === 0);
          featuredCount = fit || 1;
          topRows = fit ? (total - fit) / cols : Math.ceil((total - 1) / cols);
        }}
        grid.style.gridTemplateColumns = `repeat(${{cols}}, minmax(0, 1fr))`;
        grid.style.gridTemplateRows = mobile
          ? `repeat(${{total}}, minmax(190px, 56vw))`
          : `repeat(${{topRows}}, minmax(0, .58fr)) minmax(0, 1.28fr)`;
        for (const tile of grid.querySelectorAll(".tile")) {{
          tile.style.gridColumn = "";
          tile.style.gridRow = "";
        }}
        if (!mobile) {{
          const span = cols / featuredCount;
          for (let i = 0; i < featuredCount; i++) {{
            const featured = getTile(cameraOrder[total - featuredCount + i]);
            if (featured) {{
              featured.style.gridColumn = `${{i * span + 1}} / ${{i * span + span + 1}}`;
              featured.style.gridRow = `${{topRows + 1}}`;
            }}
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
        <img alt="" class="snapshot-active" data-role="image" draggable="false">
        <img alt="" data-role="image" draggable="false">
        <canvas data-role="focus-frame"></canvas>
        <video data-role="video" autoplay muted playsinline></video>
        <video-stream data-role="webrtc"></video-stream>
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

    function hasVisiblePixels(context, width, height) {{
      if (!context) return false;
      try {{
        const pixels = context.getImageData(0, 0, width, height).data;
        const pixelCount = pixels.length / 4;
        let visiblePixels = 0;
        let totalBrightness = 0;
        for (let index = 0; index < pixels.length; index += 4) {{
          const brightness = Math.max(
            pixels[index],
            pixels[index + 1],
            pixels[index + 2],
          );
          totalBrightness += brightness;
          if (brightness >= 18) visiblePixels += 1;
        }}
        return (
          visiblePixels >= Math.max(4, Math.ceil(pixelCount * 0.04))
          && totalBrightness / pixelCount >= 6
        );
      }} catch (_) {{
        return false;
      }}
    }}

    async function visibleImageObjectUrl(blob) {{
      const imageUrl = URL.createObjectURL(blob);
      const candidate = new Image();
      candidate.decoding = "async";
      candidate.src = imageUrl;
      try {{
        if (candidate.decode) {{
          await candidate.decode();
        }} else {{
          await new Promise((resolve, reject) => {{
            candidate.addEventListener("load", resolve, {{once: true}});
            candidate.addEventListener("error", reject, {{once: true}});
          }});
        }}
        if (
          !candidate.naturalWidth
          || !candidate.naturalHeight
          || !snapshotProbeContext
        ) throw new Error("snapshot did not decode");
        snapshotProbeContext.drawImage(
          candidate,
          0,
          0,
          snapshotProbeCanvas.width,
          snapshotProbeCanvas.height,
        );
        if (
          !hasVisiblePixels(
            snapshotProbeContext,
            snapshotProbeCanvas.width,
            snapshotProbeCanvas.height,
          )
        ) throw new Error("snapshot is black");
        return imageUrl;
      }} catch (_) {{
        URL.revokeObjectURL(imageUrl);
        return "";
      }}
    }}

    async function presentImageObjectUrl(camera, imageUrl) {{
      const tile = getTile(camera.slug);
      const images = [...(tile?.querySelectorAll('[data-role="image"]') || [])];
      if (images.length !== 2) {{
        URL.revokeObjectURL(imageUrl);
        return false;
      }}
      const version = (imageSwapVersions.get(camera.slug) || 0) + 1;
      imageSwapVersions.set(camera.slug, version);
      const current = images.find((image) =>
        image.classList.contains("snapshot-active")
      ) || images[0];
      const next = images.find((image) => image !== current);
      imageSwapAnimations.get(camera.slug)?.cancel();
      imageSwapAnimations.delete(camera.slug);
      next.src = imageUrl;
      try {{
        if (next.decode) await next.decode();
      }} catch (_) {{
        if (imageSwapVersions.get(camera.slug) === version) {{
          next.classList.remove("snapshot-entering");
        }}
        URL.revokeObjectURL(imageUrl);
        return false;
      }}
      if (imageSwapVersions.get(camera.slug) !== version) {{
        URL.revokeObjectURL(imageUrl);
        return false;
      }}
      const oldImageUrl = imageObjectUrls.get(camera.slug);
      next.classList.add("snapshot-entering");
      const reveal = next.animate(
        [
          {{opacity: 0}},
          {{opacity: 1}},
        ],
        {{
          duration: imageRevealMs,
          easing: "linear",
          fill: "both",
        }},
      );
      imageSwapAnimations.set(camera.slug, reveal);
      try {{
        await reveal.finished;
      }} catch (_) {{}}
      if (
        imageSwapVersions.get(camera.slug) !== version
        || imageSwapAnimations.get(camera.slug) !== reveal
      ) {{
        URL.revokeObjectURL(imageUrl);
        return false;
      }}
      next.classList.add("snapshot-active");
      current.classList.remove("snapshot-active");
      next.classList.remove("snapshot-entering");
      reveal.cancel();
      imageSwapAnimations.delete(camera.slug);
      imageObjectUrls.set(camera.slug, imageUrl);
      if (oldImageUrl) {{
        if (current.getAttribute("src") === oldImageUrl) {{
          current.removeAttribute("src");
        }}
        URL.revokeObjectURL(oldImageUrl);
      }}
      return true;
    }}

    async function presentVisibleImageBlob(camera, blob) {{
      const imageUrl = await visibleImageObjectUrl(blob);
      if (!imageUrl) return false;
      return presentImageObjectUrl(camera, imageUrl);
    }}

    async function refreshImage(camera) {{
      if (paused) return;
      if (!pageVisible && !sentinelMode) {{
        scheduleImage(camera, 30000);
        return;
      }}
      const directState = directStates.get(camera.slug);
      if (camera.direct_ws_url && directState?.lastFrameAt) {{
        scheduleImage(camera, 30000);
        return;
      }}
      const headers = {{}};
      const etag = imageEtags.get(camera.slug);
      if (etag) headers["If-None-Match"] = etag;
      let nextDelay = camera.direct_ws_url ? 30000 : camera.refresh_ms;
      try {{
        const role = sentinelMode ? "sentinel" : "viewer";
        const response = await fetch(
          `/snapshot/${{camera.slug}}.jpg?role=${{role}}`,
          {{ cache: "no-store", headers }},
        );
        if (response.status === 304) {{
          nextDelay = Math.max(nextDelay, camera.refresh_ms, 3000);
          return;
        }}
        if (!response.ok) {{
          throw new Error(`${{response.status}} ${{response.statusText}}`);
        }}
        const responseEtag = response.headers.get("ETag");
        if (responseEtag) imageEtags.set(camera.slug, responseEtag);
        if (!await presentVisibleImageBlob(camera, await response.blob())) {{
          nextDelay = Math.min(nextDelay, 3000);
          return;
        }}
      }} catch (_) {{
        nextDelay = Math.max(nextDelay, camera.refresh_ms, 3000);
      }} finally {{
        scheduleImage(camera, nextDelay);
      }}
    }}

    function sleep(ms) {{
      return new Promise((resolve) => setTimeout(resolve, ms));
    }}

    function pumpDirectMedia(camera, state) {{
      const sourceBuffer = state.sourceBuffer;
      if (!sourceBuffer || sourceBuffer.updating || state.mediaReleased) return;
      const video = state.video;
      if (sourceBuffer.buffered.length && video.currentTime > 30) {{
        const oldest = sourceBuffer.buffered.start(0);
        const cutoff = video.currentTime - 20;
        if (cutoff > oldest + 5) {{
          try {{
            sourceBuffer.remove(oldest, cutoff);
            return;
          }} catch (_) {{}}
        }}
      }}
      const segment = state.segmentQueue.shift();
      if (!segment) return;
      state.segmentQueueBytes -= segment.byteLength;
      try {{
        sourceBuffer.appendBuffer(segment);
      }} catch (error) {{
        logCameraError({{
          slug: camera.slug,
          last_error: `go2rtc MSE append failed: ${{error}}`,
        }});
        state.ws?.close();
      }}
    }}

    function keepDirectMediaAtLiveEdge(state) {{
      const sourceBuffer = state.sourceBuffer;
      const video = state.video;
      if (!sourceBuffer?.buffered.length || !video) return;
      try {{
        const range = sourceBuffer.buffered.length - 1;
        const start = sourceBuffer.buffered.start(range);
        const end = sourceBuffer.buffered.end(range);
        const lag = end - video.currentTime;
        if (
          !state.playbackStarted
          || video.currentTime < start
          || lag > 3
        ) {{
          video.currentTime = Math.max(start, end - 0.25);
        }}
        state.playbackStarted = true;
      }} catch (_) {{}}
    }}

    function captureDirectFrame(camera, state) {{
      const video = state.video;
      if (
        !state.active
        || state.uploadInFlight
        || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA
        || !video.videoWidth
        || !video.videoHeight
      ) return;
      if (!isDirectFrameVisible(state)) return;
      renderDirectFocusFrame(camera, state, true);
      const scale = Math.min(1, captureFrameWidth / video.videoWidth);
      const canvas = state.captureCanvas;
      const width = Math.max(2, Math.round(video.videoWidth * scale));
      const height = Math.max(2, Math.round(video.videoHeight * scale));
      if (canvas.width !== width) canvas.width = width;
      if (canvas.height !== height) canvas.height = height;
      try {{
        state.captureContext.drawImage(video, 0, 0, canvas.width, canvas.height);
      }} catch (_) {{
        return;
      }}
      state.uploadInFlight = true;
      canvas.toBlob(async (blob) => {{
        if (!blob) {{
          state.uploadInFlight = false;
          return;
        }}
        try {{
          await Promise.allSettled([
            presentVisibleImageBlob(camera, blob),
            fetch(`/api/frame/${{camera.slug}}`, {{
              method: "POST",
              headers: {{"Content-Type": "image/jpeg"}},
              body: blob,
            }}),
          ]);
        }} catch (_) {{
        }} finally {{
          state.uploadInFlight = false;
        }}
      }}, "image/jpeg", captureFrameQuality);
    }}

    function isDirectFrameVisible(state) {{
      if (!state.probeContext) return true;
      try {{
        state.probeContext.drawImage(state.video, 0, 0, 16, 9);
        return hasVisiblePixels(state.probeContext, 16, 9);
      }} catch (_) {{
        return false;
      }}
    }}

    function renderDirectFocusFrame(camera, state, frameIsVisible = false) {{
      const video = state.video;
      if (
        !video
        || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA
        || !video.videoWidth
        || !video.videoHeight
      ) return false;
      if (focusedSlug !== camera.slug) return true;
      try {{
        if (!frameIsVisible && !isDirectFrameVisible(state)) return false;
        if (
          directStates.get(camera.slug) === state
          && state.focusContext
        ) {{
          const canvas = state.focusCanvas;
          if (canvas.width !== video.videoWidth) canvas.width = video.videoWidth;
          if (canvas.height !== video.videoHeight) canvas.height = video.videoHeight;
          state.focusContext.drawImage(video, 0, 0, canvas.width, canvas.height);
          const tile = getTile(camera.slug);
          tile?.classList.remove("direct-focus-pending");
          tile?.classList.add("direct-focus-frame-live");
        }}
        return true;
      }} catch (_) {{
        return false;
      }}
    }}

    function setDirectCaptureInterval(camera, state, intervalMs) {{
      if (state.captureTimer) clearInterval(state.captureTimer);
      state.captureTimer = setInterval(
        () => captureDirectFrame(camera, state),
        intervalMs,
      );
      captureDirectFrame(camera, state);
    }}

    function releaseDirectMedia(camera, state) {{
      if (state.mediaReleased) return;
      state.mediaReleased = true;
      const ownsMedia = directStates.get(camera.slug) === state;
      if (state.captureTimer) clearInterval(state.captureTimer);
      if (state.segmentQueue) state.segmentQueue.length = 0;
      state.segmentQueueBytes = 0;
      const tile = getTile(camera.slug);
      if (tile && ownsMedia) {{
        tile.classList.remove(
          "direct-live",
          "direct-mse-live",
          "direct-webrtc-live",
        );
        if (focusedSlug !== camera.slug) {{
          tile.classList.remove(
            "direct-focus-pending",
            "direct-focus-frame-live",
          );
        }} else if (!tile.classList.contains("direct-focus-frame-live")) {{
          tile.classList.add("direct-focus-pending");
        }}
      }}
      const video = state.video;
      if (video) {{
        if (state.frameCallbackId && video.cancelVideoFrameCallback) {{
          video.cancelVideoFrameCallback(state.frameCallbackId);
        }}
        if (state.loadedDataHandler) {{
          video.removeEventListener("loadeddata", state.loadedDataHandler);
        }}
        if (state.timeUpdateHandler) {{
          video.removeEventListener("timeupdate", state.timeUpdateHandler);
        }}
        if (ownsMedia) {{
          video.pause();
          if (!state.player) {{
            video.removeAttribute("src");
            video.load();
          }}
        }}
      }}
      if (state.player && ownsMedia) {{
        state.player.wsURL = "";
        state.player.ondisconnect?.();
      }}
      if (state.mediaUrl) URL.revokeObjectURL(state.mediaUrl);
    }}

    function bindDirectVideo(camera, state, tile, liveClass) {{
      const video = state.video;
      state.loadedDataHandler = () => {{
        if (directStates.get(camera.slug) !== state) return;
        tile.classList.remove("direct-mse-live", "direct-webrtc-live");
        tile.classList.add("direct-live", liveClass);
        if (renderDirectFocusFrame(camera, state)) state.lastFrameAt = Date.now();
        captureDirectFrame(camera, state);
        video.play().catch(() => {{}});
        if (video.requestVideoFrameCallback && !state.frameCallbackId) {{
          const frameArrived = () => {{
            if (directStates.get(camera.slug) !== state || state.mediaReleased) return;
            if (renderDirectFocusFrame(camera, state)) state.lastFrameAt = Date.now();
            state.frameCallbackId = video.requestVideoFrameCallback(frameArrived);
          }};
          state.frameCallbackId = video.requestVideoFrameCallback(frameArrived);
        }}
      }};
      video.addEventListener("loadeddata", state.loadedDataHandler);
      if (!video.requestVideoFrameCallback) {{
        state.timeUpdateHandler = () => {{
          if (renderDirectFocusFrame(camera, state)) state.lastFrameAt = Date.now();
        }};
        video.addEventListener("timeupdate", state.timeUpdateHandler);
      }}
      setDirectCaptureInterval(
        camera,
        state,
        camera.source === "eufy" ? eufyCaptureIntervalMs : directCaptureIntervalMs,
      );
    }}

    function startNestWebRTC(camera) {{
      if (!camera.direct_ws_url || paused || (!pageVisible && !sentinelMode)) return;
      const existing = directStates.get(camera.slug);
      if (existing?.active || existing?.connecting) return;

      const tile = getTile(camera.slug);
      const player = tile?.querySelector("[data-role=webrtc]");
      if (!tile || !player || !("RTCPeerConnection" in window)) {{
        logCameraError({{
          slug: camera.slug,
          last_error: "WebRTC is unavailable in this browser",
        }});
        return;
      }}

      const state = {{
        active: false,
        connecting: true,
        ws: null,
        reconnectTimer: null,
        lastFrameAt: 0,
        mediaSource: null,
        mediaUrl: null,
        mediaReleased: false,
        sourceBuffer: null,
        segmentQueue: [],
        segmentQueueBytes: 0,
        video: null,
        player,
        captureCanvas: document.createElement("canvas"),
        captureContext: null,
        focusCanvas: null,
        focusContext: null,
        captureTimer: null,
        uploadInFlight: false,
        frameCallbackId: 0,
        loadedDataHandler: null,
        timeUpdateHandler: null,
        playbackStarted: false,
      }};
      state.captureContext = state.captureCanvas.getContext("2d", {{alpha: false}});
      state.focusCanvas = tile.querySelector('[data-role="focus-frame"]');
      state.focusContext = state.focusCanvas?.getContext("2d", {{alpha: false}});
      if (!state.captureContext) return;
      directStates.set(camera.slug, state);

      customElements.whenDefined("video-stream").then(() => {{
        if (directStates.get(camera.slug) !== state || state.mediaReleased) return;
        player.mode = "webrtc";
        player.media = "video";
        player.background = false;
        player.visibilityCheck = true;
        const video = player.video;
        if (!video) throw new Error("go2rtc WebRTC player did not initialize");
        state.video = video;
        video.autoplay = true;
        video.controls = false;
        video.muted = true;
        video.playsInline = true;
        bindDirectVideo(camera, state, tile, "direct-webrtc-live");
        state.active = true;
        state.connecting = false;
        player.src = camera.direct_ws_url;
      }}).catch((error) => {{
        if (directStates.get(camera.slug) !== state) return;
        logCameraError({{
          slug: camera.slug,
          last_error: `go2rtc WebRTC setup failed: ${{error}}`,
        }});
        cleanupDirect(camera);
      }});
    }}

    function startMseDirect(camera) {{
      if (!camera.direct_ws_url || paused) return;
      const existing = directStates.get(camera.slug);
      if (existing?.active || existing?.connecting) return;

      const tile = getTile(camera.slug);
      const video = tile?.querySelector("[data-role=video]");
      const MediaSourceClass = window.MediaSource || window.ManagedMediaSource;
      if (!tile || !video || !MediaSourceClass) {{
        logCameraError({{
          slug: camera.slug,
          last_error: "MediaSource is unavailable in this browser",
        }});
        return;
      }}
      const codecs = directMseCodecs.filter((codec) =>
        MediaSourceClass.isTypeSupported(`video/mp4; codecs="${{codec}}"`)
      );
      if (!codecs.length) {{
        logCameraError({{
          slug: camera.slug,
          last_error: "No supported H.264/H.265 MediaSource codec",
        }});
        return;
      }}

      const mediaSource = new MediaSourceClass();
      const mediaUrl = URL.createObjectURL(mediaSource);
      const state = {{
        active: false,
        connecting: true,
        ws: null,
        reconnectTimer: null,
        lastFrameAt: 0,
        mediaSource,
        mediaUrl,
        mediaReleased: false,
        sourceBuffer: null,
        segmentQueue: [],
        segmentQueueBytes: 0,
        video,
        captureCanvas: document.createElement("canvas"),
        captureContext: null,
        focusCanvas: null,
        focusContext: null,
        captureTimer: null,
        uploadInFlight: false,
        probeCanvas: document.createElement("canvas"),
        probeContext: null,
        mseRequested: false,
        frameCallbackId: 0,
        loadedDataHandler: null,
        timeUpdateHandler: null,
        player: null,
        playbackStarted: false,
      }};
      state.captureContext = state.captureCanvas.getContext("2d", {{alpha: false}});
      state.focusCanvas = tile.querySelector('[data-role="focus-frame"]');
      state.focusContext = state.focusCanvas?.getContext("2d", {{alpha: false}});
      state.probeCanvas.width = 16;
      state.probeCanvas.height = 9;
      state.probeContext = state.probeCanvas.getContext("2d", {{alpha: false}});
      if (!state.captureContext || !state.probeContext) {{
        URL.revokeObjectURL(mediaUrl);
        return;
      }}
      directStates.set(camera.slug, state);
      video.src = mediaUrl;
      bindDirectVideo(camera, state, tile, "direct-mse-live");

      const directUrl = new URL(camera.direct_ws_url, window.location.href);
      directUrl.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(directUrl);
      ws.binaryType = "arraybuffer";
      state.ws = ws;
      const requestMse = () => {{
        if (
          state.mseRequested
          || ws.readyState !== WebSocket.OPEN
          || mediaSource.readyState !== "open"
        ) return;
        state.mseRequested = true;
        ws.send(JSON.stringify({{type: "mse", value: codecs.join(",")}}));
      }};
      mediaSource.addEventListener("sourceopen", requestMse, {{once: true}});
      ws.addEventListener("open", () => {{
        state.active = true;
        state.connecting = false;
        requestMse();
      }});
      ws.addEventListener("message", (event) => {{
        if (typeof event.data === "string") {{
          try {{
            const message = JSON.parse(event.data);
            if (message.type === "mse" && message.value) {{
              if (!MediaSourceClass.isTypeSupported(message.value)) {{
                throw new Error(`unsupported codec: ${{message.value}}`);
              }}
              state.sourceBuffer = mediaSource.addSourceBuffer(message.value);
              state.sourceBuffer.addEventListener(
                "updateend",
                () => {{
                  keepDirectMediaAtLiveEdge(state);
                  pumpDirectMedia(camera, state);
                }},
              );
              state.sourceBuffer.addEventListener("error", () => ws.close());
              pumpDirectMedia(camera, state);
            }}
            if (message.type === "error") {{
              logCameraError({{
                slug: camera.slug,
                last_error: `go2rtc: ${{message.value}}`,
              }});
              ws.close();
            }}
          }} catch (error) {{
            logCameraError({{
              slug: camera.slug,
              last_error: `go2rtc MSE setup failed: ${{error}}`,
            }});
            ws.close();
          }}
          return;
        }}
        const segment = event.data;
        state.segmentQueue.push(segment);
        state.segmentQueueBytes += segment.byteLength;
        if (state.segmentQueueBytes > directQueueLimitBytes) {{
          logCameraError({{
            slug: camera.slug,
            last_error: "go2rtc MSE queue exceeded 16 MiB",
          }});
          ws.close();
          return;
        }}
        pumpDirectMedia(camera, state);
      }});
      ws.addEventListener("close", () => {{
        state.active = false;
        state.connecting = false;
        state.ws = null;
        releaseDirectMedia(camera, state);
        if (directStates.get(camera.slug) !== state || paused) return;
        state.reconnectTimer = setTimeout(() => {{
          directStates.delete(camera.slug);
          startDirect(camera);
        }}, 60000);
      }});
      ws.addEventListener("error", () => ws.close());
    }}

    function startDirect(camera) {{
      if (!pageVisible && !sentinelMode) return;
      if (camera.source === "nest") startNestWebRTC(camera);
      else startMseDirect(camera);
    }}

    function cleanupDirect(camera) {{
      const state = directStates.get(camera.slug);
      if (!state) return;
      if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
      if (state.ws && state.ws.readyState < WebSocket.CLOSING) state.ws.close();
      releaseDirectMedia(camera, state);
      if (directStates.get(camera.slug) === state) {{
        directStates.delete(camera.slug);
      }}
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

    async function setCameraFocus(slug) {{
      if (focusedSlug === slug) return;
      focusedSlug = slug;
      for (const camera of cameras) {{
        if (camera.source !== "eufy") continue;
        const state = directStates.get(camera.slug);
        const tile = getTile(camera.slug);
        if (slug && camera.slug !== slug) {{
          cleanupDirect(camera);
        }} else {{
          if (!slug) {{
            tile?.classList.remove(
              "direct-focus-pending",
              "direct-focus-frame-live",
            );
          }} else {{
            tile?.classList.add("direct-focus-pending");
            if (state) renderDirectFocusFrame(camera, state);
          }}
          if (state) {{
            setDirectCaptureInterval(
              camera,
              state,
              eufyCaptureIntervalMs,
            );
          }}
        }}
      }}
      const path = slug ? `/api/focus/${{encodeURIComponent(slug)}}` : "/api/focus";
      try {{
        await fetchJson(`${{path}}?owner=${{encodeURIComponent(viewerId)}}`, {{
          method: "POST",
        }});
      }} catch (error) {{
        logCameraError({{
          slug: slug || "focus",
          last_error: `focus request failed: ${{error}}`,
        }});
      }}
    }}

    function toggleExpanded(tile) {{
      const first = tile.getBoundingClientRect();
      if (expandedTile && expandedTile !== tile) {{
        expandedTile.classList.remove("expanded");
      }}
      const willExpand = !tile.classList.contains("expanded");
      const camera = cameras.find((item) => item.slug === tile.dataset.slug);
      const nextFocus = willExpand && camera?.source === "eufy" ? camera.slug : "";
      if (nextFocus) tile.classList.add("direct-focus-pending");
      tile.classList.toggle("expanded", willExpand);
      expandedTile = willExpand ? tile : null;
      setCameraFocus(nextFocus);
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
        const query = new URLSearchParams();
        if (sentinelMode) query.set("touch", "warm");
        else if (pageVisible) query.set("touch", "1");
        if (focusedSlug && pageVisible) {{
          query.set("focus", focusedSlug);
          query.set("owner", viewerId);
        }}
        const response = await fetch(`/api/status?${{query}}`, {{cache: "no-store"}});
        const statuses = await response.json();
        const activeFocus = statuses.focused_slug || "";
        const wasPaused = paused;
        paused = Boolean(statuses.paused);
        if (paused || (!pageVisible && !sentinelMode)) {{
          for (const camera of cameras) {{
            cleanupDirect(camera);
          }}
        }} else if (wasPaused) {{
          for (const camera of cameras) {{
            startDirect(camera);
          }}
        }}
        for (const status of statuses.cameras) {{
          const tile = document.querySelector(`[data-slug="${{status.slug}}"]`);
          if (!tile) continue;
          logCameraError(status);
          const age = status.source === "eufy"
            ? status.received_age_seconds
            : status.age_seconds;
          const ageText = formatRelativeAge(age);

          if (status.go2rtc_mode) {{
            const camera = cameras.find((item) => item.slug === status.slug);
            if (camera) {{
              if (!pageVisible && !sentinelMode) cleanupDirect(camera);
              else if (activeFocus && status.slug !== activeFocus) cleanupDirect(camera);
              else if (
                status.live
                || (
                  status.source === "eufy"
                  && status.wanted
                )
              ) startDirect(camera);
              else cleanupDirect(camera);
            }}
          }}

          const liveWindow = status.go2rtc_mode
            ? 45
            : 45;
          const directState = directStates.get(status.slug);
          const directFresh = directState?.lastFrameAt
            && Date.now() - directState.lastFrameAt < 5000;
          const directVisualWindow = focusedSlug === status.slug ? 5 : 45;
          if (
            status.go2rtc_mode
            && directFresh
            && age !== null
            && age < directVisualWindow
          ) {{
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
    document.addEventListener("visibilitychange", () => {{
      pageVisible = !document.hidden;
      if (!pageVisible && !sentinelMode) {{
        if (focusedSlug) {{
          navigator.sendBeacon(`/api/focus?owner=${{encodeURIComponent(viewerId)}}`);
          focusedSlug = "";
        }}
        for (const camera of cameras) cleanupDirect(camera);
        return;
      }}
      cameras.forEach((camera, index) => {{
        scheduleImage(camera, index * initialImageStaggerMs);
      }});
    }});
    window.addEventListener("beforeunload", () => {{
      if (focusedSlug) {{
        navigator.sendBeacon(`/api/focus?owner=${{encodeURIComponent(viewerId)}}`);
      }}
      for (const camera of cameras) {{
        cleanupDirect(camera);
      }}
      for (const imageUrl of imageObjectUrls.values()) {{
        URL.revokeObjectURL(imageUrl);
      }}
    }});
    cameras.forEach((camera, index) => {{
      if (
        sentinelMode
        && (!camera.keep_warm || (sentinelCameraSlug && camera.slug !== sentinelCameraSlug))
      ) return;
      scheduleImage(camera, index * initialImageStaggerMs);
      const spacing = sentinelMode ? 5000 : 1200;
      if (camera.source === "nest") {{
        setTimeout(() => startDirect(camera), index * spacing + 600);
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
    # Browsers commonly open six or more HTTP/1.1 connections at once. The
    # stdlib default backlog is only five, which can force the last initial
    # snapshot connections through a one-second TCP retry even though every
    # frame is already cached in memory.
    request_queue_size = 64
    daemon_threads = True
    block_on_close = False

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        runners: dict[str, CameraRunner],
        camera_payload: list[dict[str, Any]],
        camera_order: list[str],
    ) -> None:
        super().__init__(server_address, handler_class)
        self.runners = runners
        self.camera_payload_by_slug = {
            camera["slug"]: camera for camera in camera_payload
        }
        self.camera_order = normalize_camera_order(camera_order)
        self.state_lock = threading.Lock()
        self.paused = False
        self.focused_slug = ""
        self.focus_owner = ""
        self.focused_until = 0.0
        self.last_warm_touch_at = 0.0
        try:
            self.eufy_viewer_slots = max(
                1,
                int(
                    os.environ.get(
                        "CAMERA_MONITOR_EUFY_VIEWER_SLOTS",
                        DEFAULT_EUFY_VIEWER_SLOTS,
                    )
                ),
            )
        except ValueError:
            self.eufy_viewer_slots = DEFAULT_EUFY_VIEWER_SLOTS
        try:
            self.eufy_thumbnail_refresh_seconds = max(
                10.0,
                float(
                    os.environ.get(
                        "CAMERA_MONITOR_EUFY_THUMBNAIL_REFRESH_SECONDS",
                        DEFAULT_EUFY_THUMBNAIL_REFRESH_SECONDS,
                    )
                ),
            )
        except ValueError:
            self.eufy_thumbnail_refresh_seconds = (
                DEFAULT_EUFY_THUMBNAIL_REFRESH_SECONDS
            )
        self.eufy_thumbnail_targets: dict[str, tuple[float, float]] = {}
        self.eufy_thumbnail_retry_after: dict[str, float] = {}
        self.eufy_thumbnail_failures: dict[str, int] = {}
        try:
            warm_idle_hours = float(
                os.environ.get("CAMERA_MONITOR_WARM_IDLE_HOURS", DEFAULT_WARM_IDLE_HOURS)
            )
        except ValueError:
            warm_idle_hours = DEFAULT_WARM_IDLE_HOURS
        self.warm_idle_timeout_seconds = max(0.0, warm_idle_hours * 60 * 60)
        self.last_viewer_activity_at = load_viewer_activity()
        self.last_viewer_activity_written_at = 0.0
        self.warm_agent_expected = os.environ.get(
            "CAMERA_MONITOR_WARM_AGENT_ENABLED", ""
        ).strip().lower() in {"1", "true", "yes", "on"}

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

    def warm_agent_active(self) -> bool:
        return time.time() - self.last_warm_touch_at < WARM_AGENT_HEARTBEAT_SECONDS

    def active_focus_slug(self) -> str:
        now = time.time()
        with self.state_lock:
            if self.focused_slug and now >= self.focused_until:
                self.focused_slug = ""
                self.focus_owner = ""
                self.focused_until = 0.0
            return self.focused_slug

    def set_focus(self, slug: str, owner: str) -> str:
        if not owner or len(owner) > 128:
            raise ValueError("focus owner is required")
        if slug:
            runner = self.runners.get(slug)
            if runner is None:
                raise KeyError("unknown camera")
            if runner.config.source != "eufy":
                raise ValueError("focus mode is only needed for Eufy cameras")
            with self.state_lock:
                self.focused_slug = slug
                self.focus_owner = owner
                self.focused_until = time.time() + VIEWER_TTL_SECONDS
                self.eufy_thumbnail_targets.clear()
            for other in self.runners.values():
                if (
                    other.config.source == "eufy"
                    and other.config.slug != slug
                ):
                    other.stop_when_idle()
            self.touch_runner_for_viewer(runner)
            return slug

        with self.state_lock:
            if self.focus_owner == owner:
                self.focused_slug = ""
                self.focus_owner = ""
                self.focused_until = 0.0
        return self.active_focus_slug()

    def record_viewer_activity(self) -> None:
        now = time.time()
        with self.state_lock:
            self.last_viewer_activity_at = now
            should_persist = (
                now - self.last_viewer_activity_written_at
                >= VIEWER_ACTIVITY_WRITE_INTERVAL_SECONDS
            )
            if should_persist:
                self.last_viewer_activity_written_at = now
        if should_persist:
            save_viewer_activity(now)

    def viewer_activity_status(self) -> dict[str, float | bool]:
        now = time.time()
        with self.state_lock:
            last_viewer_activity_at = self.last_viewer_activity_at
            timeout = self.warm_idle_timeout_seconds
        idle_seconds = max(0.0, now - last_viewer_activity_at)
        return {
            "last_viewer_activity_at": last_viewer_activity_at,
            "viewer_idle_seconds": round(idle_seconds, 1),
            "viewer_active": idle_seconds < VIEWER_TTL_SECONDS,
            "warm_idle_timeout_seconds": timeout,
            "warm_allowed": timeout > 0 and idle_seconds < timeout,
        }

    def touch_runner_for_viewer(
        self,
        runner: CameraRunner,
        *,
        record_activity: bool = True,
    ) -> None:
        if record_activity:
            self.record_viewer_activity()
        if self.paused or not runner.config.auto_start:
            return
        focused_slug = self.active_focus_slug()
        if (
            focused_slug
            and runner.config.source == "eufy"
            and runner.config.slug != focused_slug
        ):
            runner.stop_when_idle()
            return
        if (
            runner.config.source == "eufy"
            and runner.config.slug not in self._eufy_viewer_targets()
        ):
            runner.stop_when_idle()
            return
        runner.touch(role="viewer")

    def _eufy_viewer_targets(self) -> set[str]:
        focused_slug = self.active_focus_slug()
        if focused_slug:
            return {focused_slug}
        with self.state_lock:
            return set(self.eufy_thumbnail_targets)

    def _refresh_eufy_thumbnail_targets(self) -> set[str]:
        focused_slug = self.active_focus_slug()
        if focused_slug:
            return {focused_slug}

        now = time.time()
        monotonic_now = time.monotonic()
        with self.state_lock:
            slugs = [
                slug
                for slug in self.camera_order
                if slug in self.runners
                and self.runners[slug].config.source == "eufy"
                and self.runners[slug].config.auto_start
            ]
            targets = dict(self.eufy_thumbnail_targets)
            retry_after = dict(self.eufy_thumbnail_retry_after)
            failures = dict(self.eufy_thumbnail_failures)

        snapshots = {slug: self.runners[slug].snapshot() for slug in slugs}
        completed: set[str] = set()
        for slug, (baseline_received_at, started_at) in list(targets.items()):
            latest_received_at = float(
                snapshots.get(slug, {}).get("latest_received_at") or 0.0
            )
            if latest_received_at > baseline_received_at:
                completed.add(slug)
                targets.pop(slug, None)
                retry_after.pop(slug, None)
                failures.pop(slug, None)
            elif monotonic_now - started_at >= EUFY_THUMBNAIL_ATTEMPT_TIMEOUT_SECONDS:
                completed.add(slug)
                targets.pop(slug, None)
                failure_count = failures.get(slug, 0) + 1
                failures[slug] = failure_count
                retry_delay = min(
                    EUFY_THUMBNAIL_RETRY_BASE_SECONDS
                    * (2 ** min(failure_count - 1, 4)),
                    EUFY_THUMBNAIL_RETRY_MAX_SECONDS,
                )
                retry_after[slug] = monotonic_now + retry_delay

        # Let completed P2P sessions stop before filling their slots. Starting a
        # replacement in the same heartbeat can briefly exceed Eufy's session
        # limit and strand both the old and new camera.
        slots_available = 0 if completed else max(
            0,
            min(self.eufy_viewer_slots, len(slugs)) - len(targets),
        )
        candidates = sorted(
            (
                slug
                for slug in slugs
                if slug not in targets
                and retry_after.get(slug, 0.0) <= monotonic_now
                and (
                    not snapshots[slug].get("latest_received_at")
                    or now - float(snapshots[slug]["latest_received_at"])
                    >= self.eufy_thumbnail_refresh_seconds
                )
            ),
            key=lambda slug: float(snapshots[slug].get("latest_received_at") or 0.0),
        )
        for slug in candidates[:slots_available]:
            baseline = float(snapshots[slug].get("latest_received_at") or 0.0)
            targets[slug] = (baseline, monotonic_now)

        with self.state_lock:
            self.eufy_thumbnail_targets = targets
            self.eufy_thumbnail_retry_after = retry_after
            self.eufy_thumbnail_failures = failures
        for slug in completed:
            runner = self.runners.get(slug)
            if runner is not None:
                runner.stop_when_idle()
        return set(targets)

    def touch_visible_runners(self, *, keep_warm_only: bool = False) -> None:
        if keep_warm_only:
            self.last_warm_touch_at = time.time()
            if not self.viewer_activity_status()["warm_allowed"]:
                return
        else:
            self.record_viewer_activity()
        if self.paused:
            return
        focused_slug = self.active_focus_slug()
        eufy_targets = (
            self._refresh_eufy_thumbnail_targets() if not keep_warm_only else set()
        )
        with self.state_lock:
            order = list(self.camera_order)
        for slug in order:
            runner = self.runners.get(slug)
            if runner is None or not runner.config.auto_start:
                continue
            if (
                focused_slug
                and runner.config.source == "eufy"
                and slug != focused_slug
            ):
                continue
            if keep_warm_only:
                if not runner.config.keep_warm:
                    continue
                if runner.config.source == "eufy":
                    # Eufy thumbnails require browser decoding and are only
                    # refreshed while a viewer has the wall open.
                    continue
                runner.touch(role="warm")
            else:
                if runner.config.source == "eufy" and slug not in eufy_targets:
                    runner.stop_when_idle()
                else:
                    self.touch_runner_for_viewer(runner, record_activity=False)

    def set_camera_order(self, order: list[str]) -> list[str]:
        normalized = save_camera_order(order)
        with self.state_lock:
            self.camera_order = normalized
        return normalized

    def pause_camera_work(self) -> None:
        self.paused = True
        for runner in self.runners.values():
            runner.stop_when_idle()


class Handler(BaseHTTPRequestHandler):
    server: MonitorServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/go2rtc/api/ws":
            self._proxy_go2rtc_websocket(parsed)
            return
        if parsed.path in GO2RTC_BROWSER_MODULES:
            self._proxy_go2rtc_browser_module(parsed.path)
            return

        if parsed.path == "/":
            query = urllib.parse.parse_qs(parsed.query)
            if query.get("sentinel") != ["1"]:
                self.server.record_viewer_activity()
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

        if parsed.path == "/manifest.webmanifest":
            self._send_bytes(
                HTTPStatus.OK,
                "application/manifest+json; charset=utf-8",
                render_manifest(),
                cache=True,
            )
            return

        if parsed.path in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
            self._send_bytes(
                HTTPStatus.OK, "image/png", app_icon_png(APP_ICON_TOUCH_SIZE), cache=True
            )
            return

        if parsed.path == "/icons/icon-192.png":
            self._send_bytes(HTTPStatus.OK, "image/png", app_icon_png(192), cache=True)
            return

        if parsed.path == "/icons/icon-512.png":
            self._send_bytes(HTTPStatus.OK, "image/png", app_icon_png(512), cache=True)
            return

        if parsed.path == "/.well-known/appspecific/com.chrome.devtools.json":
            self._send_json(HTTPStatus.OK, {})
            return

        if parsed.path == "/api/status":
            query = urllib.parse.parse_qs(parsed.query)
            focus_slugs = query.get("focus", [])
            focus_owners = query.get("owner", [])
            if len(focus_slugs) == 1 and len(focus_owners) == 1:
                try:
                    self.server.set_focus(focus_slugs[0], focus_owners[0])
                except (KeyError, ValueError):
                    pass
            if query.get("touch") == ["1"]:
                self.server.touch_visible_runners()
            elif query.get("touch") == ["warm"]:
                self.server.touch_visible_runners(keep_warm_only=True)
            payload = {
                "paused": self.server.paused,
                "warm_agent_active": self.server.warm_agent_active(),
                "warm_agent_expected": self.server.warm_agent_expected,
                "order": self.server.get_camera_order(),
                "focused_slug": self.server.active_focus_slug(),
                "cameras": self.server.get_runner_snapshots(),
            }
            payload.update(self.server.viewer_activity_status())
            self._send_json(HTTPStatus.OK, payload)
            return

        if parsed.path.startswith("/snapshot/") and parsed.path.endswith(".jpg"):
            slug = parsed.path.removeprefix("/snapshot/").removesuffix(".jpg")
            runner = self.server.runners.get(slug)
            if runner is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown camera"})
                return

            query = urllib.parse.parse_qs(parsed.query)
            if query.get("role") != ["sentinel"]:
                self.server.touch_runner_for_viewer(runner)

            frame, latest_at, content_type = runner.get_frame()
            if frame is None:
                svg = make_placeholder_svg()
                self._send_bytes(HTTPStatus.OK, "image/svg+xml", svg, cache=False)
                return

            age = time.time() - latest_at
            etag = f'"{slug}-{int(latest_at * 1000)}"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("ETag", etag)
                self.send_header("X-Frame-Age-Seconds", f"{age:.1f}")
                self.end_headers()
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(frame)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("ETag", etag)
            self.send_header("X-Frame-Age-Seconds", f"{age:.1f}")
            self.end_headers()
            self.wfile.write(frame)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _proxy_go2rtc_browser_module(self, path: str) -> None:
        upstream_path = path.removeprefix("/go2rtc")
        try:
            with urllib.request.urlopen(
                f"{GO2RTC_URL}{upstream_path}",
                timeout=SOCKET_TIMEOUT_SECONDS,
            ) as response:
                body = response.read(MAX_GO2RTC_BROWSER_MODULE_BYTES + 1)
            if len(body) > MAX_GO2RTC_BROWSER_MODULE_BYTES:
                raise ValueError("go2rtc browser module is too large")
        except Exception as exc:  # noqa: BLE001 - bounded local proxy error.
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                {"error": f"go2rtc browser module failed: {type(exc).__name__}"},
            )
            return
        self._send_bytes(
            HTTPStatus.OK,
            "text/javascript; charset=utf-8",
            body,
            cache=True,
        )

    def _proxy_go2rtc_websocket(self, parsed: urllib.parse.ParseResult) -> None:
        if self.headers.get("Upgrade", "").lower() != "websocket":
            self._send_json(
                HTTPStatus.UPGRADE_REQUIRED,
                {"error": "WebSocket upgrade required"},
            )
            return

        query = urllib.parse.parse_qs(parsed.query)
        requested_sources = query.get("src", [])
        allowed_sources = {
            browser_stream_name(runner.config): upstream_stream_name(runner.config)
            for runner in self.server.runners.values()
        }
        if len(requested_sources) != 1 or requested_sources[0] not in allowed_sources:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "unknown camera stream"})
            return
        upstream_source = allowed_sources[requested_sources[0]]

        target = urllib.parse.urlsplit(GO2RTC_URL)
        if target.scheme not in {"http", "https"} or not target.hostname:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": "go2rtc is unavailable"})
            return

        upstream: socket.socket | ssl.SSLSocket | None = None
        handshake_forwarded = False
        try:
            port = target.port or (443 if target.scheme == "https" else 80)
            upstream = socket.create_connection(
                (target.hostname, port),
                timeout=SOCKET_TIMEOUT_SECONDS,
            )
            if target.scheme == "https":
                upstream = ssl.create_default_context().wrap_socket(
                    upstream,
                    server_hostname=target.hostname,
                )

            upstream_path = "/api/ws?" + urllib.parse.urlencode(
                {"src": upstream_source}
            )
            request_headers = [
                f"GET {upstream_path} HTTP/1.1",
                f"Host: {target.netloc}",
                "Upgrade: websocket",
                "Connection: Upgrade",
                f"Origin: {target.scheme}://{target.netloc}",
            ]
            skipped_headers = {
                "connection",
                "host",
                "origin",
                "proxy-connection",
                "upgrade",
            }
            request_headers.extend(
                f"{name}: {value}"
                for name, value in self.headers.items()
                if name.lower() not in skipped_headers
            )
            upstream.sendall(("\r\n".join(request_headers) + "\r\n\r\n").encode("latin-1"))

            response = bytearray()
            while b"\r\n\r\n" not in response:
                chunk = upstream.recv(4096)
                if not chunk:
                    raise ConnectionError("go2rtc closed during WebSocket handshake")
                response.extend(chunk)
                if len(response) > 65536:
                    raise ValueError("go2rtc WebSocket handshake is too large")
            status_line = bytes(response).split(b"\r\n", 1)[0]
            if b" 101 " not in status_line:
                raise ConnectionError(
                    "go2rtc rejected the WebSocket handshake: "
                    + status_line.decode("latin-1", errors="replace")
                )

            self.connection.sendall(response)
            handshake_forwarded = True
            self.close_connection = True
            upstream.settimeout(None)
            self.connection.settimeout(None)
            peers = {
                self.connection: upstream,
                upstream: self.connection,
            }
            while True:
                readable, _, _ = select.select(
                    list(peers),
                    [],
                    [],
                    WEBSOCKET_PROXY_IDLE_SECONDS,
                )
                if not readable:
                    return
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    peers[source].sendall(data)
                    if is_websocket_close_frame(
                        data,
                        masked=source is self.connection,
                    ):
                        return
        except Exception as exc:  # noqa: BLE001 - local proxy returns a bounded error.
            if not handshake_forwarded:
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"error": f"go2rtc WebSocket proxy failed: {type(exc).__name__}"},
                )
        finally:
            if upstream is not None:
                upstream.close()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/focus" or parsed.path.startswith("/api/focus/"):
            query = urllib.parse.parse_qs(parsed.query)
            owner = query.get("owner", [""])[0]
            slug = parsed.path.removeprefix("/api/focus/") if parsed.path != "/api/focus" else ""
            try:
                focused_slug = self.server.set_focus(slug, owner)
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"focused_slug": focused_slug})
            return

        if parsed.path == "/api/pause":
            self.server.pause_camera_work()
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
        if parsed.path.startswith("/api/frame/"):
            slug = parsed.path.removeprefix("/api/frame/")
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
            if not frame.startswith((b"\xff\xd8", b"\x89PNG\r\n\x1a\n")):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "browser frame is not a valid JPEG or PNG"},
                )
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
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if not cache:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # A status poll can time out while camera snapshots are collected.
            # The next poll will reconnect; avoid turning that into a traceback.
            return


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
    global CACHE_DIR, ORDER_PATH, VIEWER_ACTIVITY_PATH, GO2RTC_URL
    global DEFAULT_CAMERA_ORDER

    parser = argparse.ArgumentParser(description="Run a local camera monitor wall.")
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
    VIEWER_ACTIVITY_PATH = CACHE_DIR / "viewer_activity.json"
    cameras = load_monitor_config(Path(args.config))
    DEFAULT_CAMERA_ORDER = tuple(camera.slug for camera in cameras)
    GO2RTC_URL = resolve_go2rtc_url()

    prepare_cache_dir()
    if any(camera.source == "nest" for camera in cameras):
        configure_nest_streams(
            GO2RTC_URL,
            cameras,
            NestCredentials.from_environment(),
        )
    eufy = None
    if any(camera.source == "eufy" for camera in cameras):
        eufy = DirectEufyClient(
            os.environ.get("CAMERA_EUFY_WS_URL", "ws://eufy-security:3000"),
            GO2RTC_URL,
        )
        eufy.start()
    runners = {camera.slug: CameraRunner(camera, eufy) for camera in cameras}
    camera_order = load_camera_order()
    camera_payload = [
        {
            "slug": camera.slug,
            "name": camera.name,
            "lan_ip": camera.lan_ip,
            "refresh_ms": camera.refresh_ms,
            "source": camera.source,
            "go2rtc_mode": "webrtc" if camera.source == "nest" else "mse",
            "direct_ws_url": direct_websocket_url(camera),
            "snapshot_interval": camera.snapshot_interval,
            "stale_ok": camera.stale_ok,
            "stale_ok_seconds": camera.stale_ok_seconds,
            "stale_kick_seconds": camera.stale_kick_seconds,
            "keep_warm": camera.keep_warm,
            "auto_start": camera.auto_start,
            "note": camera.note,
        }
        for camera in cameras
    ]

    port = find_port(args.host, args.port)
    server = MonitorServer(
        (args.host, port),
        Handler,
        runners,
        camera_payload,
        camera_order,
    )
    print(f"Serving camera monitor at http://{args.host}:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        if eufy is not None:
            eufy.close()


if __name__ == "__main__":
    main()
