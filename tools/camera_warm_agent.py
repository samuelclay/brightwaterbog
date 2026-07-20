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
STATUS_INTERVAL_SECONDS = 5
STATUS_REQUEST_TIMEOUT_SECONDS = 6
CHROMIUM_DEVTOOLS_PORT = 9222
CHROMIUM_DEVTOOLS_TIMEOUT_SECONDS = 15
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
EUFY_INTEGRATION_SETTLE_SECONDS = 20
ADDON_RESTART_TIMEOUT_SECONDS = 120
EUFY_REACHABILITY_INTERVAL_SECONDS = 10
EUFY_OFFLINE_CONFIRMATIONS = 3
EUFY_ONLINE_CONFIRMATIONS = 2
EUFY_POWER_RECOVERY_COOLDOWN_SECONDS = 5 * 60
EUFY_POWER_RECOVERY_STALE_SECONDS = 5 * 60


@dataclass
class WarmBrowser:
    slugs: list[str]
    process: subprocess.Popen[bytes] | None = None
    started_at: float = 0.0
    next_start_at: float = 0.0


@dataclass(frozen=True)
class EufyTarget:
    slug: str
    lan_ip: str
    power_entity_id: str = ""


@dataclass
class ReachabilityState:
    online: bool | None = None
    success_count: int = 0
    failure_count: int = 0


class EufyReachabilityTracker:
    def __init__(self, targets: list[EufyTarget]) -> None:
        self.states = {target.slug: ReachabilityState() for target in targets}

    def observe(self, slug: str, reachable: bool) -> str | None:
        state = self.states[slug]
        if reachable:
            state.failure_count = 0
            state.success_count += 1
            if state.online is None:
                state.online = True
                state.success_count = 0
                return None
            if (
                state.online is False
                and state.success_count >= EUFY_ONLINE_CONFIRMATIONS
            ):
                state.online = True
                state.success_count = 0
                return "restored"
            return None

        state.success_count = 0
        state.failure_count += 1
        if (
            state.online is not False
            and state.failure_count >= EUFY_OFFLINE_CONFIRMATIONS
        ):
            state.online = False
            state.failure_count = 0
            return "offline"
        return None


@dataclass(frozen=True)
class WarmInventory:
    webrtc_slugs: list[str]
    eufy_slugs: list[str]
    power_restore_targets: list[EufyTarget]
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
        power_restore_targets=[
            EufyTarget(
                slug=str(camera["slug"]),
                lan_ip=str(camera.get("lan_ip") or "").strip(),
                power_entity_id=str(camera.get("power_entity_id") or "").strip(),
            )
            for camera in cameras
            if (
                camera.get("source") == "eufy_p2p"
                and camera.get("keep_warm")
                and camera.get("auto_start", True)
                and camera.get("recover_on_power_restore", False)
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


def camera_power_is_on(
    target: EufyTarget,
    ha_url: str,
    token: str,
) -> bool | None:
    if not target.power_entity_id:
        return None
    entity_id = urllib.parse.quote(target.power_entity_id, safe=".")
    request = urllib.request.Request(
        f"{ha_url.rstrip('/')}/api/states/{entity_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=STATUS_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            state = str(json.load(response).get("state") or "").lower()
    except Exception:
        return None
    if state == "on":
        return True
    if state in {"off", "unavailable"}:
        return False
    return None


def poll_eufy_reachability(
    inventory: WarmInventory,
    tracker: EufyReachabilityTracker,
    ha_url: str,
    token: str,
) -> list[str]:
    restored: list[str] = []
    for target in inventory.power_restore_targets:
        reachable = camera_power_is_on(target, ha_url, token)
        if reachable is None:
            continue
        transition = tracker.observe(target.slug, reachable)
        if transition == "offline":
            print(
                f"Warm agent noticed {target.slug} power went offline; waiting for restore",
                flush=True,
            )
        elif transition == "restored":
            print(
                f"Warm agent noticed {target.slug} power restored",
                flush=True,
            )
            restored.append(target.slug)
    return restored


def stale_power_restore_targets(
    inventory: WarmInventory,
    camera_statuses: dict[str, dict[str, Any]],
) -> list[str]:
    stale_slugs: list[str] = []
    for target in inventory.power_restore_targets:
        received_age = camera_statuses.get(target.slug, {}).get(
            "received_age_seconds"
        )
        if (
            received_age is not None
            and float(received_age) > EUFY_POWER_RECOVERY_STALE_SECONDS
        ):
            stale_slugs.append(target.slug)
    return stale_slugs


def recover_eufy_stack(
    base_url: str,
    ha_url: str,
    token: str,
    eufy_addon: str,
    go2rtc_addon: str,
    affected_slugs: list[str],
    reason: str = "stale feeds",
) -> None:
    print(
        f"Warm agent recovering shared Eufy stack after {reason}: "
        + ", ".join(affected_slugs),
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
        reload_result = post_json(
            f"{base_url}/api/reload/config-entry/eufy_security"
        )
        print(
            "Warm agent reloaded "
            f"{reload_result.get('reloaded', 0)} Eufy integration entry",
            flush=True,
        )
        time.sleep(EUFY_INTEGRATION_SETTLE_SECONDS)
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
    baseline_failure_count = int(
        (status or {}).get("consecutive_failure_count") or 0
    )
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
            if (
                int(last_status.get("consecutive_failure_count") or 0)
                > baseline_failure_count
                and last_status.get("last_start_status") not in {None, 200}
            ):
                break
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
    consecutive_failed_slugs: list[str] = []
    failure_counts = {slug: 0 for slug in inventory.eufy_slugs}
    last_recovery_at = 0.0
    last_power_recovery_at = 0.0
    reachability_tracker = EufyReachabilityTracker(inventory.power_restore_targets)
    next_reachability_check_at = 0.0
    startup_recovery_checked = False
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
        now = time.time()
        if now >= next_reachability_check_at:
            camera_statuses = fetch_status(base_url, touch_warm=False) or {}
            restored_slugs: list[str] = []
            if not startup_recovery_checked:
                restored_slugs = stale_power_restore_targets(
                    inventory,
                    camera_statuses,
                )
                startup_recovery_checked = True
                if restored_slugs:
                    print(
                        "Warm agent found powered-monitor cameras stale at startup: "
                        + ", ".join(restored_slugs),
                        flush=True,
                    )
            power_restored_slugs = poll_eufy_reachability(
                inventory,
                reachability_tracker,
                ha_url,
                token,
            )
            restored_slugs.extend(
                slug for slug in power_restored_slugs if slug not in restored_slugs
            )
            next_reachability_check_at = now + EUFY_REACHABILITY_INTERVAL_SECONDS
            if restored_slugs:
                if (
                    recovery_enabled
                    and now - last_power_recovery_at
                    >= EUFY_POWER_RECOVERY_COOLDOWN_SECONDS
                ):
                    try:
                        recover_eufy_stack(
                            base_url,
                            ha_url,
                            token,
                            inventory.eufy_addon,
                            inventory.go2rtc_addon,
                            restored_slugs,
                            reason="camera power restoration",
                        )
                        last_power_recovery_at = time.time()
                        last_recovery_at = last_power_recovery_at
                        consecutive_failed_slugs.clear()
                        for index, warm_slug in enumerate(inventory.eufy_slugs):
                            failure_counts[warm_slug] = 0
                            next_refresh_at[warm_slug] = (
                                time.time() + index * EUFY_REFRESH_STAGGER_SECONDS
                            )
                        continue
                    except Exception as exc:  # noqa: BLE001 - retry refreshes.
                        print(
                            "Warm agent power-restore recovery failed: "
                            f"{type(exc).__name__}: {exc}",
                            flush=True,
                        )
                for restored_slug in restored_slugs:
                    failure_counts[restored_slug] = 0
                    next_refresh_at[restored_slug] = time.time()

        slug = min(next_refresh_at, key=next_refresh_at.get)
        wait_seconds = max(0.0, next_refresh_at[slug] - time.time())
        if stopping.wait(min(wait_seconds, STATUS_INTERVAL_SECONDS)):
            break
        if time.time() < next_refresh_at[slug]:
            continue

        if refresh_eufy_camera(base_url, slug, stopping):
            consecutive_failed_slugs.clear()
            failure_counts[slug] = 0
            next_refresh_at[slug] = time.time() + EUFY_REFRESH_INTERVAL_SECONDS
        else:
            consecutive_failed_slugs.append(slug)
            failure_counts[slug] += 1
            retry_delay = min(
                EUFY_REFRESH_INTERVAL_SECONDS
                * (2 ** min(failure_counts[slug] - 1, 8)),
                EUFY_REFRESH_BACKOFF_MAX_SECONDS,
            )
            next_refresh_at[slug] = time.time() + retry_delay

        if (
            recovery_enabled
            and len(consecutive_failed_slugs) >= EUFY_RECOVERY_FAILURE_QUORUM
            and time.time() - last_recovery_at >= EUFY_RECOVERY_COOLDOWN_SECONDS
        ):
            try:
                recover_eufy_stack(
                    base_url,
                    ha_url,
                    token,
                    inventory.eufy_addon,
                    inventory.go2rtc_addon,
                    consecutive_failed_slugs,
                )
                last_recovery_at = time.time()
                consecutive_failed_slugs.clear()
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
    if not browser.slugs:
        return

    profile_dir = profile_root / "shared"
    profile_dir.mkdir(parents=True, exist_ok=True)
    sentinel_urls = [
        f"{base_url}/?{urllib.parse.urlencode({'sentinel': '1', 'camera': slug})}"
        for slug in browser.slugs
    ]
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
        "--disable-component-update",
        "--disable-extensions",
        "--disable-gpu",
        "--disable-sync",
        "--autoplay-policy=no-user-gesture-required",
        "--log-level=3",
        "--metrics-recording-only",
        "--mute-audio",
        "--no-default-browser-check",
        "--no-first-run",
        "--renderer-process-limit=4",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={CHROMIUM_DEVTOOLS_PORT}",
        f"--user-data-dir={profile_dir}",
        sentinel_urls[0],
    ]
    browser.process = subprocess.Popen(command, start_new_session=True)
    browser.started_at = time.time()
    print(
        "Warm agent started shared Chromium for "
        f"{browser.slugs[0]} in pid {browser.process.pid}",
        flush=True,
    )

    devtools_url = f"http://127.0.0.1:{CHROMIUM_DEVTOOLS_PORT}"
    deadline = time.time() + CHROMIUM_DEVTOOLS_TIMEOUT_SECONDS
    while time.time() < deadline:
        if browser.process.poll() is not None:
            raise RuntimeError(
                f"Chromium exited with status {browser.process.returncode}"
            )
        try:
            with urllib.request.urlopen(
                f"{devtools_url}/json/version",
                timeout=1,
            ):
                break
        except Exception:
            time.sleep(0.25)
    else:
        raise TimeoutError("Chromium DevTools endpoint did not become ready")

    for slug, sentinel_url in zip(browser.slugs[1:], sentinel_urls[1:]):
        time.sleep(INITIAL_STAGGER_SECONDS)
        encoded_url = urllib.parse.quote(sentinel_url, safe="")
        request = urllib.request.Request(
            f"{devtools_url}/json/new?{encoded_url}",
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            json.load(response)
        print(f"Warm agent opened shared Chromium tab for {slug}", flush=True)


def stop_browser(browser: WarmBrowser, reason: str, restart_delay: int) -> None:
    process = browser.process
    if process is None:
        return
    print(f"Warm agent recycling shared Chromium: {reason}", flush=True)
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
    browser.next_start_at = time.time() + restart_delay


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
    browser = (
        WarmBrowser(slugs=inventory.webrtc_slugs, next_start_at=time.time())
        if inventory.webrtc_slugs
        else None
    )
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
    print(
        "Warm agent supervising "
        f"{len(inventory.webrtc_slugs)} WebRTC cameras in one shared Chromium",
        flush=True,
    )
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
            if browser is not None:
                process = browser.process

                if process is not None and process.poll() is not None:
                    print(
                        "Warm agent noticed shared Chromium exit "
                        f"with status {process.returncode}",
                        flush=True,
                    )
                    browser.process = None
                    browser.started_at = 0.0
                    browser.next_start_at = now + PROCESS_RESTART_DELAY_SECONDS
                    process = None

                if process is None and now >= browser.next_start_at:
                    try:
                        start_browser(
                            browser,
                            args.chromium,
                            args.base_url,
                            args.profile_root,
                        )
                    except Exception as exc:  # noqa: BLE001 - wrapper will retry.
                        stop_browser(
                            browser,
                            f"startup failed: {type(exc).__name__}: {exc}",
                            PROCESS_RESTART_DELAY_SECONDS,
                        )
            time.sleep(STATUS_INTERVAL_SECONDS)
    finally:
        stop_event.set()
        if eufy_thread is not None:
            eufy_thread.join(timeout=5)
        if browser is not None:
            stop_browser(browser, "warm agent shutting down", 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
