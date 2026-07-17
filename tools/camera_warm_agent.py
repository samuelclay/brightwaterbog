#!/usr/bin/env python3
"""Supervise warm WebRTC sessions and serialized Eufy frame refreshes."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


INITIAL_STAGGER_SECONDS = 5
PROCESS_RESTART_DELAY_SECONDS = 10
STALE_RESTART_DELAY_SECONDS = 30
STALE_FRAME_SECONDS = 45
STARTUP_GRACE_SECONDS = 60
SESSION_RENEW_SECONDS = 225
STATUS_INTERVAL_SECONDS = 5
STATUS_REQUEST_TIMEOUT_SECONDS = 6
EUFY_FRESH_FRAME_SECONDS = 5
EUFY_REFRESH_INTERVAL_SECONDS = 60
EUFY_REFRESH_BACKOFF_MAX_SECONDS = 15 * 60
EUFY_REFRESH_TIMEOUT_SECONDS = 60
EUFY_REFRESH_POLL_SECONDS = 2
EUFY_REFRESH_STAGGER_SECONDS = 5
EUFY_RECOVERY_FAILURE_QUORUM = 2
EUFY_RECOVERY_COOLDOWN_SECONDS = 20 * 60
EUFY_MONITOR_DRAIN_SECONDS = 15
EUFY_ADDON_SETTLE_SECONDS = 20
GO2RTC_ADDON_SETTLE_SECONDS = 10
ADDON_RESTART_TIMEOUT_SECONDS = 120


@dataclass
class WarmBrowser:
    slug: str
    index: int
    process: subprocess.Popen[bytes] | None = None
    started_at: float = 0.0
    next_start_at: float = 0.0


@dataclass(frozen=True)
class WarmInventory:
    webrtc_slugs: list[str]
    eufy_slugs: list[str]
    eufy_addon: str
    go2rtc_addon: str


def load_warm_inventory(config_path: Path) -> WarmInventory:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    cameras = payload.get("cameras", [])
    return WarmInventory(
        webrtc_slugs=[
            str(camera["slug"])
            for camera in cameras
            if camera.get("source") == "webrtc" and camera.get("keep_warm")
        ],
        eufy_slugs=[
            str(camera["slug"])
            for camera in cameras
            if (
                camera.get("source") == "eufy_p2p"
                and camera.get("keep_warm")
                and camera.get("auto_start", True)
            )
        ],
        eufy_addon=str(payload.get("eufy_security_ws_addon") or "").strip(),
        go2rtc_addon=str(payload.get("go2rtc_addon") or "").strip(),
    )


def fetch_status(
    base_url: str,
    *,
    touch_warm: bool = True,
) -> dict[str, dict[str, Any]] | None:
    suffix = "?touch=warm" if touch_warm else ""
    try:
        with urllib.request.urlopen(
            f"{base_url}/api/status{suffix}",
            timeout=STATUS_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            payload = json.load(response)
    except Exception:
        return None
    return {
        str(camera.get("slug")): camera
        for camera in payload.get("cameras", [])
        if isinstance(camera, dict) and camera.get("slug")
    }


def has_fresh_frame(status: dict[str, Any] | None) -> bool:
    if not status:
        return False
    received_age = status.get("received_age_seconds")
    return bool(
        received_age is not None
        and float(received_age) <= EUFY_FRESH_FRAME_SECONDS
    )


def post_json(
    url: str,
    payload: dict[str, Any] | None = None,
    token: str = "",
    timeout: int = 20,
) -> dict[str, Any]:
    body = json.dumps(payload or {}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_body = response.read()
    return json.loads(response_body) if response_body else {}


def restart_addon(ha_url: str, token: str, addon: str) -> None:
    post_json(
        f"{ha_url.rstrip('/')}/api/services/hassio/addon_restart",
        {"addon": addon},
        token,
        timeout=ADDON_RESTART_TIMEOUT_SECONDS,
    )


def recover_eufy_stack(
    base_url: str,
    ha_url: str,
    token: str,
    eufy_addon: str,
    go2rtc_addon: str,
    unhealthy_slugs: list[str],
) -> None:
    print(
        "Warm agent recovering shared Eufy stack after stale feeds: "
        + ", ".join(unhealthy_slugs),
        flush=True,
    )
    paused = False
    try:
        post_json(f"{base_url}/api/pause")
        paused = True
        time.sleep(EUFY_MONITOR_DRAIN_SECONDS)
        restart_addon(ha_url, token, eufy_addon)
        time.sleep(EUFY_ADDON_SETTLE_SECONDS)
        restart_addon(ha_url, token, go2rtc_addon)
        time.sleep(GO2RTC_ADDON_SETTLE_SECONDS)
    finally:
        if paused:
            post_json(f"{base_url}/api/resume")
    print("Warm agent completed shared Eufy stack recovery", flush=True)


def refresh_eufy_camera(
    base_url: str,
    slug: str,
    stopping: threading.Event,
) -> bool:
    status = (fetch_status(base_url, touch_warm=False) or {}).get(slug)
    if has_fresh_frame(status):
        return True

    baseline_received_at = float((status or {}).get("latest_received_at") or 0)
    started = False
    last_status = status or {}
    try:
        post_json(f"{base_url}/api/warm/eufy/start/{slug}")
        started = True
        deadline = time.time() + EUFY_REFRESH_TIMEOUT_SECONDS
        while not stopping.is_set() and time.time() < deadline:
            statuses = fetch_status(base_url, touch_warm=False) or {}
            last_status = statuses.get(slug, {})
            latest_received_at = float(last_status.get("latest_received_at") or 0)
            if (
                latest_received_at > baseline_received_at
                and has_fresh_frame(last_status)
            ):
                print(f"Warm agent refreshed Eufy camera {slug}", flush=True)
                return True
            stopping.wait(EUFY_REFRESH_POLL_SECONDS)
    except Exception as exc:  # noqa: BLE001 - continue with the next camera.
        print(
            f"Warm agent could not start Eufy refresh for {slug}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
    finally:
        if started:
            try:
                post_json(f"{base_url}/api/warm/eufy/stop/{slug}")
            except Exception as exc:  # noqa: BLE001 - next cycle can clean up.
                print(
                    f"Warm agent could not release Eufy refresh for {slug}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )

    error = str(last_status.get("last_error") or "no fresh frame")
    print(f"Warm agent Eufy refresh failed for {slug}: {error}", flush=True)
    return False


def supervise_eufy_cameras(
    inventory: WarmInventory,
    base_url: str,
    ha_url: str,
    token: str,
    stopping: threading.Event,
) -> None:
    next_refresh_at = {
        slug: time.time() + index * EUFY_REFRESH_STAGGER_SECONDS
        for index, slug in enumerate(inventory.eufy_slugs)
    }
    failed_slugs: set[str] = set()
    failure_counts = {slug: 0 for slug in inventory.eufy_slugs}
    last_recovery_at = 0.0
    recovery_enabled = bool(
        inventory.eufy_addon and inventory.go2rtc_addon and token
    )
    if inventory.eufy_slugs and not recovery_enabled:
        print(
            "Warm agent Eufy stack recovery is disabled; configure both add-on "
            "slugs and an API token",
            flush=True,
        )

    while not stopping.is_set():
        slug = min(next_refresh_at, key=next_refresh_at.get)
        wait_seconds = max(0.0, next_refresh_at[slug] - time.time())
        if stopping.wait(min(wait_seconds, STATUS_INTERVAL_SECONDS)):
            break
        if time.time() < next_refresh_at[slug]:
            continue

        if refresh_eufy_camera(base_url, slug, stopping):
            failed_slugs.discard(slug)
            failure_counts[slug] = 0
            next_refresh_at[slug] = time.time() + EUFY_REFRESH_INTERVAL_SECONDS
        else:
            failed_slugs.add(slug)
            failure_counts[slug] += 1
            retry_delay = min(
                EUFY_REFRESH_INTERVAL_SECONDS
                * (2 ** min(failure_counts[slug] - 1, 8)),
                EUFY_REFRESH_BACKOFF_MAX_SECONDS,
            )
            next_refresh_at[slug] = time.time() + retry_delay

        if (
            recovery_enabled
            and len(failed_slugs) >= EUFY_RECOVERY_FAILURE_QUORUM
            and time.time() - last_recovery_at >= EUFY_RECOVERY_COOLDOWN_SECONDS
        ):
            try:
                recover_eufy_stack(
                    base_url,
                    ha_url,
                    token,
                    inventory.eufy_addon,
                    inventory.go2rtc_addon,
                    sorted(failed_slugs),
                )
                last_recovery_at = time.time()
                failed_slugs.clear()
                for index, warm_slug in enumerate(inventory.eufy_slugs):
                    failure_counts[warm_slug] = 0
                    next_refresh_at[warm_slug] = (
                        time.time() + index * EUFY_REFRESH_STAGGER_SECONDS
                    )
            except Exception as exc:  # noqa: BLE001 - keep polling cameras.
                print(
                    "Warm agent Eufy stack recovery failed: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )


def start_browser(
    browser: WarmBrowser,
    chromium: str,
    base_url: str,
    profile_root: Path,
) -> None:
    profile_dir = profile_root / browser.slug
    profile_dir.mkdir(parents=True, exist_ok=True)
    query = urllib.parse.urlencode({"sentinel": "1", "camera": browser.slug})
    command = [
        chromium,
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-breakpad",
        "--autoplay-policy=no-user-gesture-required",
        "--log-level=3",
        "--mute-audio",
        f"--user-data-dir={profile_dir}",
        f"{base_url}/?{query}",
    ]
    browser.process = subprocess.Popen(command, start_new_session=True)
    browser.started_at = time.time()
    print(
        f"Warm agent started {browser.slug} in Chromium pid {browser.process.pid}",
        flush=True,
    )


def stop_browser(browser: WarmBrowser, reason: str, restart_delay: int) -> None:
    process = browser.process
    if process is None:
        return
    print(f"Warm agent recycling {browser.slug}: {reason}", flush=True)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=8)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    browser.process = None
    browser.started_at = 0.0
    browser.next_start_at = (
        time.time()
        + restart_delay
        + browser.index * INITIAL_STAGGER_SECONDS
    )


def frame_status_is_stale(status: dict[str, Any] | None) -> bool:
    """Treat missing monitor status as unknown, not as a failed camera."""
    if status is None:
        return False
    received_age = status.get("received_age_seconds")
    return bool(
        received_age is None or float(received_age) > STALE_FRAME_SECONDS
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chromium", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--ha-url",
        default=os.environ.get("CAMERA_MONITOR_HA_URL", "http://supervisor/core"),
    )
    parser.add_argument(
        "--profile-root",
        type=Path,
        default=Path("/data/chromium"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inventory = load_warm_inventory(args.config)
    browsers = [
        WarmBrowser(
            slug=slug,
            index=index,
            next_start_at=time.time() + index * INITIAL_STAGGER_SECONDS,
        )
        for index, slug in enumerate(inventory.webrtc_slugs)
    ]
    ha_token = os.environ.get("CABIN_HOME_ASSISTANT_TOKEN") or os.environ.get(
        "SUPERVISOR_TOKEN", ""
    )
    stopping = False
    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    print(f"Warm agent supervising {len(browsers)} WebRTC cameras", flush=True)
    print(
        f"Warm agent monitoring {len(inventory.eufy_slugs)} warm Eufy cameras",
        flush=True,
    )
    eufy_thread: threading.Thread | None = None
    status_available = True
    if inventory.eufy_slugs:
        eufy_thread = threading.Thread(
            target=supervise_eufy_cameras,
            args=(
                inventory,
                args.base_url,
                args.ha_url,
                ha_token,
                stop_event,
            ),
            name="eufy-warm-supervisor",
            daemon=True,
        )
        eufy_thread.start()

    try:
        while not stopping:
            now = time.time()
            statuses = fetch_status(args.base_url)
            if statuses is None and status_available:
                print(
                    "Warm agent status unavailable; preserving existing "
                    "browser sessions",
                    flush=True,
                )
                status_available = False
            elif statuses is not None and not status_available:
                print("Warm agent status recovered", flush=True)
                status_available = True
            for browser in browsers:
                process = browser.process
                camera_status = (
                    statuses.get(browser.slug) if statuses is not None else None
                )
                cooldown = float(
                    (camera_status or {}).get("webrtc_cooldown_seconds") or 0
                )

                if process is not None and process.poll() is not None:
                    print(
                        f"Warm agent noticed {browser.slug} Chromium exit "
                        f"with status {process.returncode}",
                        flush=True,
                    )
                    browser.process = None
                    browser.started_at = 0.0
                    browser.next_start_at = (
                        now
                        + PROCESS_RESTART_DELAY_SECONDS
                        + browser.index * INITIAL_STAGGER_SECONDS
                    )
                    process = None

                if process is None:
                    if cooldown > 0:
                        browser.next_start_at = max(
                            browser.next_start_at,
                            now + min(cooldown, 60),
                        )
                    elif now >= browser.next_start_at:
                        start_browser(browser, args.chromium, args.base_url, args.profile_root)
                    continue

                process_age = now - browser.started_at
                received_age = (camera_status or {}).get("received_age_seconds")
                renew_after = (
                    SESSION_RENEW_SECONDS
                    + browser.index * INITIAL_STAGGER_SECONDS
                )
                if process_age >= renew_after:
                    stop_browser(
                        browser,
                        "scheduled pre-expiry renewal",
                        PROCESS_RESTART_DELAY_SECONDS,
                    )
                elif (
                    process_age >= STARTUP_GRACE_SECONDS
                    and frame_status_is_stale(camera_status)
                    and cooldown <= 0
                ):
                    stop_browser(
                        browser,
                        f"no successful frame for {received_age}s",
                        STALE_RESTART_DELAY_SECONDS,
                    )

            time.sleep(STATUS_INTERVAL_SECONDS)
    finally:
        stop_event.set()
        if eufy_thread is not None:
            eufy_thread.join(timeout=5)
        for browser in browsers:
            stop_browser(browser, "warm agent shutting down", 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
