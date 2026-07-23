#!/usr/bin/env python3
"""Bounded 48-hour Nest warm-session supervisor for the camera stack."""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CHECK_INTERVAL_SECONDS = 15.0


@dataclass(frozen=True)
class WarmInventory:
    nest_slugs: list[str]


def load_warm_inventory(config_path: Path) -> WarmInventory:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    cameras = payload.get("cameras", [])
    return WarmInventory(
        nest_slugs=[
            str(camera["slug"])
            for camera in cameras
            if camera.get("source") == "nest" and camera.get("keep_warm")
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


def run_agent(
    inventory: WarmInventory,
    base_url: str,
    stopping: threading.Event,
) -> None:
    del inventory  # The server owns the configured Nest warm targets.
    while not stopping.is_set():
        try:
            fetch_status(base_url, touch_warm=True)
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
    )


if __name__ == "__main__":
    main()
