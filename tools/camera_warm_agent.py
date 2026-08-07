#!/usr/bin/env python3
"""Bounded 48-hour warm-session and thumbnail agent for the camera stack."""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CHECK_INTERVAL_SECONDS = 15.0
MAX_EUFY_FRAME_BYTES = 2_500_000
GO2RTC_FRAME_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class WarmInventory:
    nest_slugs: list[str]
    eufy_slugs: list[str]


def load_warm_inventory(config_path: Path) -> WarmInventory:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    cameras = payload.get("cameras", [])
    return WarmInventory(
        nest_slugs=[
            str(camera["slug"])
            for camera in cameras
            if camera.get("source") == "nest" and camera.get("keep_warm")
        ],
        eufy_slugs=[
            str(camera["slug"])
            for camera in cameras
            if camera.get("source") == "eufy"
            and camera.get("auto_start", True)
        ],
    )


def fetch_status(base_url: str, *, touch_warm: bool = False) -> dict[str, Any]:
    suffix = "?touch=warm" if touch_warm else ""
    with urllib.request.urlopen(
        f"{base_url.rstrip('/')}/api/status{suffix}",
        timeout=10,
    ) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("camera monitor status is not an object")
    return payload


def capture_eufy_thumbnail(
    slug: str,
    base_url: str,
    go2rtc_url: str,
) -> bool:
    query = urllib.parse.urlencode({"src": f"camera_eufy_{slug}"})
    frame_url = f"{go2rtc_url.rstrip('/')}/api/frame.jpeg?{query}"
    with urllib.request.urlopen(
        frame_url,
        timeout=GO2RTC_FRAME_TIMEOUT_SECONDS,
    ) as response:
        content_type = response.headers.get_content_type()
        frame = response.read(MAX_EUFY_FRAME_BYTES + 1)
    if (
        content_type != "image/jpeg"
        or not frame.startswith(b"\xff\xd8")
        or len(frame) > MAX_EUFY_FRAME_BYTES
    ):
        return False

    upload_url = (
        f"{base_url.rstrip('/')}/api/frame/"
        f"{urllib.parse.quote(slug, safe='')}"
    )
    request = urllib.request.Request(
        upload_url,
        data=frame,
        method="POST",
        headers={"Content-Type": "image/jpeg"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read(65536)
        return 200 <= response.status < 300


def capture_wanted_eufy_thumbnails(
    status: dict[str, Any],
    inventory: WarmInventory,
    base_url: str,
    go2rtc_url: str,
) -> None:
    eufy_slugs = set(inventory.eufy_slugs)
    cameras = status.get("cameras", [])
    if not isinstance(cameras, list):
        return
    for camera in cameras:
        if not isinstance(camera, dict):
            continue
        slug = str(camera.get("slug", ""))
        if slug not in eufy_slugs or not camera.get("warm_wanted"):
            continue
        try:
            capture_eufy_thumbnail(slug, base_url, go2rtc_url)
        except (OSError, ValueError):
            continue


def run_agent(
    inventory: WarmInventory,
    base_url: str,
    stopping: threading.Event,
    *,
    go2rtc_url: str = "",
) -> None:
    resolved_go2rtc_url = go2rtc_url or os.environ.get(
        "CAMERA_MONITOR_GO2RTC_URL",
        "http://go2rtc:1984",
    )
    while not stopping.is_set():
        try:
            status = fetch_status(base_url, touch_warm=True)
            capture_wanted_eufy_thumbnails(
                status,
                inventory,
                base_url,
                resolved_go2rtc_url,
            )
        except (OSError, ValueError):
            stopping.wait(CHECK_INTERVAL_SECONDS)
            continue
        stopping.wait(CHECK_INTERVAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Keep camera caches warm while recently used")
    parser.add_argument(
        "--config",
        default=os.environ.get("CAMERA_MONITOR_CONFIG", "/config/camera_monitor.json"),
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()

    inventory = load_warm_inventory(Path(args.config))
    stopping = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run_agent(
        inventory,
        args.base_url,
        stopping,
        go2rtc_url=os.environ.get("CAMERA_MONITOR_GO2RTC_URL", ""),
    )


if __name__ == "__main__":
    main()
