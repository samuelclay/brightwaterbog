#!/usr/bin/env python3

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest import mock

import camera_monitor


class FakeRunner:
    def __init__(self, *, source: str = "webrtc", keep_warm: bool = True) -> None:
        self.config = SimpleNamespace(
            slug="camera-one",
            source=source,
            keep_warm=keep_warm,
            auto_start=True,
        )
        self.latest_at = 0.0
        self.touches: list[str] = []

    def webrtc_cooldown_seconds(self) -> int:
        return 0

    def touch(self, role: str = "viewer") -> None:
        self.touches.append(role)


class FakeSession:
    def __init__(
        self,
        local_id: str,
        runner: FakeRunner,
        _ha: object,
        _offer: str,
        role: str,
    ) -> None:
        self.local_id = local_id
        self.runner = runner
        self.role = role
        self.created_at = time.time()
        self.last_seen_at = self.created_at
        self.closed = False

    def start(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def expired(self) -> bool:
        return False


def make_server(runner: FakeRunner) -> camera_monitor.MonitorServer:
    server = object.__new__(camera_monitor.MonitorServer)
    server.ha = object()
    server.runners = {runner.config.slug: runner}
    server.camera_order = [runner.config.slug]
    server.state_lock = camera_monitor.threading.Lock()
    server.webrtc_sessions = {}
    server.paused = False
    server.last_warm_touch_at = 0.0
    server.started_at = time.time()
    server.warm_agent_expected = False
    return server


class CameraOwnershipTest(unittest.TestCase):
    @mock.patch.object(camera_monitor, "WebRTCSessionProxy", FakeSession)
    def test_second_viewer_cannot_replace_camera_owner(self) -> None:
        runner = FakeRunner()
        server = make_server(runner)

        first_id = server.start_webrtc_session("camera-one", "offer-one", "viewer")
        with self.assertRaises(camera_monitor.ViewerSessionActiveError):
            server.start_webrtc_session("camera-one", "offer-two", "viewer")

        self.assertIn(first_id, server.webrtc_sessions)
        self.assertFalse(server.webrtc_sessions[first_id].closed)

    @mock.patch.object(camera_monitor, "WebRTCSessionProxy", FakeSession)
    def test_sentinel_replaces_abandoned_viewer(self) -> None:
        runner = FakeRunner()
        server = make_server(runner)
        viewer_id = server.start_webrtc_session("camera-one", "offer-one", "viewer")
        viewer = server.webrtc_sessions[viewer_id]
        viewer.last_seen_at = time.time() - camera_monitor.WEBRTC_OWNER_STALE_SECONDS - 1

        sentinel_id = server.start_webrtc_session(
            "camera-one", "offer-two", "sentinel"
        )

        self.assertTrue(viewer.closed)
        self.assertNotIn(viewer_id, server.webrtc_sessions)
        self.assertIn(sentinel_id, server.webrtc_sessions)

    def test_wall_does_not_start_warm_eufy_camera(self) -> None:
        runner = FakeRunner(source="eufy_p2p")
        server = make_server(runner)
        server.last_warm_touch_at = time.time()

        server.touch_runner_for_viewer(runner)

        self.assertEqual(runner.touches, [])

    def test_wall_yields_during_warm_agent_startup(self) -> None:
        runner = FakeRunner(source="eufy_p2p")
        server = make_server(runner)
        server.warm_agent_expected = True

        server.touch_runner_for_viewer(runner)

        self.assertEqual(runner.touches, [])


if __name__ == "__main__":
    unittest.main()
