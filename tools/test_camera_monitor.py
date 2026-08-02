#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import camera_backends
import camera_monitor


class FakeRunner:
    def __init__(
        self,
        *,
        slug: str = "camera-one",
        source: str = "nest",
        keep_warm: bool = True,
    ) -> None:
        self.config = SimpleNamespace(
            slug=slug,
            source=source,
            keep_warm=keep_warm,
            auto_start=True,
        )
        self.touches: list[str] = []
        self.stop_count = 0
        self.latest_received_at = 0.0

    def touch(self, role: str = "viewer") -> None:
        self.touches.append(role)

    def stop_when_idle(self) -> None:
        self.stop_count += 1

    def snapshot(self) -> dict[str, str | float | None]:
        return {
            "slug": self.config.slug,
            "latest_received_at": self.latest_received_at or None,
        }


def make_server(runner: FakeRunner) -> camera_monitor.MonitorServer:
    server = object.__new__(camera_monitor.MonitorServer)
    server.runners = {runner.config.slug: runner}
    server.camera_order = [runner.config.slug]
    server.state_lock = threading.Lock()
    server.paused = False
    server.focused_slug = ""
    server.focus_owner = ""
    server.focused_until = 0.0
    server.last_warm_touch_at = 0.0
    server.eufy_viewer_slots = camera_monitor.DEFAULT_EUFY_VIEWER_SLOTS
    server.eufy_thumbnail_refresh_seconds = (
        camera_monitor.DEFAULT_EUFY_THUMBNAIL_REFRESH_SECONDS
    )
    server.eufy_thumbnail_targets = {}
    server.eufy_thumbnail_retry_after = {}
    server.eufy_thumbnail_failures = {}
    server.warm_idle_timeout_seconds = 48 * 60 * 60
    server.last_viewer_activity_at = time.time()
    server.last_viewer_activity_written_at = time.time()
    server.warm_agent_expected = False
    return server


class ConfigTest(unittest.TestCase):
    def test_loads_only_direct_camera_sources(self) -> None:
        payload = {
            "cameras": [
                {
                    "slug": "door",
                    "name": "Door",
                    "device_id": "T123",
                    "source": "eufy",
                },
                {
                    "slug": "yard",
                    "name": "Yard",
                    "device_id": "enterprises/p/devices/d",
                    "source": "nest",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cameras.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            cameras = camera_monitor.load_monitor_config(path)

        self.assertEqual([camera.source for camera in cameras], ["eufy", "nest"])

    def test_rejects_unknown_camera_fields(self) -> None:
        payload = {
            "cameras": [
                {
                    "slug": "door",
                    "name": "Door",
                    "device_id": "T123",
                    "source": "eufy",
                    "obsolete_field": "unused",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cameras.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(SystemExit):
                camera_monitor.load_monitor_config(path)


class CameraOwnershipTest(unittest.TestCase):
    def test_thumbnail_attempt_outlasts_eufy_start_command(self) -> None:
        self.assertGreater(
            camera_monitor.EUFY_THUMBNAIL_ATTEMPT_TIMEOUT_SECONDS,
            camera_backends.EUFY_COMMAND_TIMEOUT_SECONDS,
        )

    def test_warming_expires_after_48_hours(self) -> None:
        server = make_server(FakeRunner())
        server.last_viewer_activity_at = time.time() - 48 * 60 * 60 - 1

        status = server.viewer_activity_status()

        self.assertFalse(status["warm_allowed"])
        self.assertFalse(status["viewer_active"])

    @mock.patch.object(camera_monitor, "save_viewer_activity")
    def test_eufy_focus_stops_and_blocks_other_eufy_streams(
        self,
        _save_viewer_activity: mock.Mock,
    ) -> None:
        focused = FakeRunner(slug="focused", source="eufy")
        other = FakeRunner(slug="other", source="eufy")
        server = make_server(focused)
        server.runners["other"] = other
        server.camera_order.append("other")

        self.assertEqual(server.set_focus("focused", "viewer-one"), "focused")
        server.touch_runner_for_viewer(other)

        self.assertEqual(focused.touches, ["viewer"])
        self.assertEqual(other.touches, [])
        self.assertGreaterEqual(other.stop_count, 1)
        self.assertEqual(server.set_focus("", "viewer-one"), "")

    def test_focus_rejects_nest_camera(self) -> None:
        server = make_server(FakeRunner(source="nest"))
        with self.assertRaisesRegex(ValueError, "Eufy"):
            server.set_focus("camera-one", "viewer-one")

    @mock.patch.object(camera_monitor, "save_viewer_activity")
    def test_eufy_wall_refreshes_thumbnails_in_two_bounded_slots(
        self,
        _save_viewer_activity: mock.Mock,
    ) -> None:
        nest = FakeRunner(slug="nest", source="nest")
        server = make_server(nest)
        eufy = [FakeRunner(slug=f"eufy-{index}", source="eufy") for index in range(4)]
        for runner in eufy:
            server.runners[runner.config.slug] = runner
            server.camera_order.append(runner.config.slug)

        with (
            mock.patch.object(camera_monitor.time, "time", return_value=100.0),
            mock.patch.object(camera_monitor.time, "monotonic", return_value=100.0),
        ):
            server.touch_visible_runners()
        self.assertEqual(nest.touches, ["viewer"])
        self.assertEqual([runner.touches for runner in eufy], [["viewer"], ["viewer"], [], []])

        eufy[0].latest_received_at = 101.0
        eufy[1].latest_received_at = 101.0
        with (
            mock.patch.object(camera_monitor.time, "time", return_value=102.0),
            mock.patch.object(camera_monitor.time, "monotonic", return_value=102.0),
        ):
            server.touch_visible_runners()
        self.assertGreaterEqual(eufy[0].stop_count, 1)
        self.assertGreaterEqual(eufy[1].stop_count, 1)

        with (
            mock.patch.object(camera_monitor.time, "time", return_value=104.0),
            mock.patch.object(camera_monitor.time, "monotonic", return_value=104.0),
        ):
            server.touch_visible_runners()
        self.assertEqual([runner.touches for runner in eufy], [["viewer"], ["viewer"], ["viewer"], ["viewer"]])


class EufyRecoveryTest(unittest.TestCase):
    @staticmethod
    def snapshot(
        slug: str,
        *,
        received_age: float,
        failures: int,
        displayed_age: float | None = None,
        error: str = "",
    ) -> dict[str, object]:
        return {
            "slug": slug,
            "source": "eufy",
            "has_frame": True,
            "age_seconds": displayed_age if displayed_age is not None else received_age,
            "received_age_seconds": received_age,
            "latest_received_at": time.time() - received_age,
            "consecutive_failure_count": failures,
            "last_error": error,
        }

    def test_uses_received_age_instead_of_unchanged_frame_age(self) -> None:
        snapshots = [
            self.snapshot(
                "barn",
                received_age=5,
                failures=20,
                displayed_age=24 * 60 * 60,
            ),
            self.snapshot(
                "dam",
                received_age=6,
                failures=20,
                displayed_age=24 * 60 * 60,
            ),
        ]

        decision = camera_monitor.choose_eufy_recovery(snapshots, {})

        self.assertIsNone(decision)

    def test_restarts_for_two_stale_cameras_with_repeated_failures(self) -> None:
        snapshots = [
            self.snapshot("barn", received_age=901, failures=3),
            self.snapshot("dam", received_age=1200, failures=3),
            self.snapshot("driveway", received_age=100, failures=0),
        ]

        decision = camera_monitor.choose_eufy_recovery(snapshots, {})

        self.assertEqual(
            decision,
            camera_monitor.EufyRecoveryDecision(
                reason="multiple_stale_eufy_cameras",
                stale_slugs=("barn", "dam"),
            ),
        )

    def test_requires_longer_outage_for_one_stale_camera(self) -> None:
        before_threshold = [
            self.snapshot("barn", received_age=1799, failures=10),
        ]
        persistent = [
            self.snapshot("barn", received_age=1800, failures=4),
        ]

        self.assertIsNone(
            camera_monitor.choose_eufy_recovery(before_threshold, {})
        )
        self.assertEqual(
            camera_monitor.choose_eufy_recovery(persistent, {}),
            camera_monitor.EufyRecoveryDecision(
                reason="one_persistently_stale_eufy_camera",
                stale_slugs=("barn",),
            ),
        )

    def test_restarts_quickly_for_an_orphaned_livestream(self) -> None:
        snapshots = [
            self.snapshot(
                "dam",
                received_age=301,
                failures=2,
                error=(
                    "RuntimeError: Eufy command device.start_livestream "
                    "device_livestream_already_running"
                ),
            ),
        ]

        self.assertEqual(
            camera_monitor.choose_eufy_recovery(snapshots, {}),
            camera_monitor.EufyRecoveryDecision(
                reason="orphaned_eufy_livestream",
                stale_slugs=("dam",),
            ),
        )

    def test_orphaned_livestream_requires_repeated_failure(self) -> None:
        snapshots = [
            self.snapshot(
                "dam",
                received_age=3600,
                failures=1,
                error="device_livestream_already_running",
            ),
        ]

        self.assertIsNone(camera_monitor.choose_eufy_recovery(snapshots, {}))

    def test_thumbnail_failures_contribute_to_recovery_decision(self) -> None:
        snapshots = [
            self.snapshot("barn", received_age=901, failures=0),
            self.snapshot("dam", received_age=901, failures=0),
        ]

        decision = camera_monitor.choose_eufy_recovery(
            snapshots,
            {"barn": 3, "dam": 3},
        )

        self.assertIsNotNone(decision)

    def test_does_not_restart_when_camera_wall_has_no_viewer(self) -> None:
        snapshots = [
            self.snapshot("barn", received_age=901, failures=3),
            self.snapshot("dam", received_age=901, failures=3),
        ]
        server = SimpleNamespace(
            paused=False,
            state_lock=threading.Lock(),
            eufy_thumbnail_failures={},
            get_runner_snapshots=lambda: snapshots,
            viewer_activity_status=lambda: {"viewer_active": False},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = camera_monitor.EufyRecoveryController(
                server,
                SimpleNamespace(connected=threading.Event()),
                enabled=True,
                supervisor_url="http://supervisor",
                supervisor_token="test-token",
                addon_slug="eufy",
                state_path=Path(temp_dir) / "recovery.json",
            )
            with mock.patch.object(controller, "_restart_eufy") as restart:
                controller.check_once()

        restart.assert_not_called()
        self.assertEqual(controller.phase, "waiting_for_viewer")

    def test_enforces_restart_cooldown(self) -> None:
        snapshots = [
            self.snapshot("barn", received_age=901, failures=3),
            self.snapshot("dam", received_age=901, failures=3),
        ]
        server = SimpleNamespace(
            paused=False,
            state_lock=threading.Lock(),
            eufy_thumbnail_failures={},
            get_runner_snapshots=lambda: snapshots,
            viewer_activity_status=lambda: {"viewer_active": True},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = camera_monitor.EufyRecoveryController(
                server,
                SimpleNamespace(connected=threading.Event()),
                enabled=True,
                supervisor_url="http://supervisor",
                supervisor_token="test-token",
                addon_slug="eufy",
                state_path=Path(temp_dir) / "recovery.json",
            )
            controller.last_restart_at = time.time() - 60
            controller.restart_times = [controller.last_restart_at]
            with mock.patch.object(controller, "_restart_eufy") as restart:
                controller.check_once()

        restart.assert_not_called()
        self.assertEqual(controller.phase, "cooldown")

    def test_enforces_daily_restart_limit(self) -> None:
        now = time.time()
        snapshots = [
            self.snapshot("barn", received_age=901, failures=3),
            self.snapshot("dam", received_age=901, failures=3),
        ]
        server = SimpleNamespace(
            paused=False,
            state_lock=threading.Lock(),
            eufy_thumbnail_failures={},
            get_runner_snapshots=lambda: snapshots,
            viewer_activity_status=lambda: {"viewer_active": True},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = camera_monitor.EufyRecoveryController(
                server,
                SimpleNamespace(connected=threading.Event()),
                enabled=True,
                supervisor_url="http://supervisor",
                supervisor_token="test-token",
                addon_slug="eufy",
                state_path=Path(temp_dir) / "recovery.json",
            )
            controller.last_restart_at = now - 2 * 60 * 60
            controller.restart_times = [now - 3 * 60 * 60, now - 2 * 60 * 60]
            with mock.patch.object(controller, "_restart_eufy") as restart:
                controller.check_once()

        restart.assert_not_called()
        self.assertEqual(controller.phase, "restart_limit_reached")

    def test_restart_pauses_monitor_and_persists_verification_state(self) -> None:
        server = SimpleNamespace(paused=False)
        server.pause_camera_work = mock.Mock(
            side_effect=lambda: setattr(server, "paused", True)
        )
        server.prepare_eufy_after_recovery = mock.Mock()
        server.touch_visible_runners = mock.Mock()
        connected = threading.Event()
        connected.set()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "recovery.json"
            controller = camera_monitor.EufyRecoveryController(
                server,
                SimpleNamespace(connected=connected),
                enabled=True,
                supervisor_url="http://supervisor",
                supervisor_token="test-token",
                addon_slug="eufy",
                state_path=state_path,
            )
            with mock.patch.object(
                controller,
                "_request_supervisor_restart",
            ) as restart:
                controller._restart_eufy(
                    camera_monitor.EufyRecoveryDecision(
                        reason="multiple_stale_eufy_cameras",
                        stale_slugs=("barn", "dam"),
                    ),
                    time.time(),
                )

            persisted = json.loads(state_path.read_text(encoding="utf-8"))

        restart.assert_called_once_with()
        server.pause_camera_work.assert_called_once_with()
        server.prepare_eufy_after_recovery.assert_called_once_with()
        server.touch_visible_runners.assert_called_once_with()
        self.assertFalse(server.paused)
        self.assertEqual(controller.last_result, "restart_completed")
        self.assertEqual(persisted["last_result"], "restart_completed")


class NativeStreamTest(unittest.TestCase):
    def test_request_handler_ignores_peer_reset_before_request_line(self) -> None:
        handler = object.__new__(camera_monitor.Handler)
        with mock.patch(
            "http.server.BaseHTTPRequestHandler.handle",
            side_effect=ConnectionResetError("peer reset"),
        ):
            handler.handle()

    def test_websocket_proxy_recognizes_close_frames(self) -> None:
        self.assertTrue(
            camera_monitor.is_websocket_close_frame(
                b"\x88\x80\x00\x00\x00\x00",
                masked=True,
            )
        )
        self.assertTrue(
            camera_monitor.is_websocket_close_frame(b"\x88\x00", masked=False)
        )
        self.assertFalse(
            camera_monitor.is_websocket_close_frame(b"\x82\x80", masked=True)
        )

    def test_browser_alias_does_not_expose_provider_device_id(self) -> None:
        camera = SimpleNamespace(slug="camera-one")
        with mock.patch.object(camera_monitor, "GO2RTC_URL", "http://go2rtc:1984"):
            resolved = camera_monitor.direct_websocket_url(camera)
        self.assertEqual(resolved, "/go2rtc/api/ws?src=camera_camera-one_native")

    def test_upstream_names_are_provider_specific(self) -> None:
        eufy = SimpleNamespace(source="eufy", slug="door", device_id="T123")
        nest = SimpleNamespace(source="nest", slug="yard", device_id="ignored")
        self.assertEqual(
            camera_monitor.upstream_stream_name(eufy),
            "camera_eufy_door",
        )
        self.assertEqual(camera_monitor.upstream_stream_name(nest), "nest_yard")

    def test_generated_page_has_no_legacy_signaling_endpoints(self) -> None:
        page = camera_monitor.render_index(
            [
                {
                    "slug": "yard",
                    "source": "nest",
                    "keep_warm": True,
                    "direct_ws_url": "/go2rtc/api/ws?src=camera_yard_native",
                }
            ]
        ).decode()
        self.assertNotIn("/api/webrtc", page)
        self.assertNotIn("startWebRTC", page)
        self.assertIn("startDirect", page)
        self.assertIn("status.live", page)
        self.assertIn('status.source === "eufy"', page)
        self.assertIn('camera.source === "eufy" ? eufyCaptureIntervalMs', page)
        self.assertIn('status.source === "eufy"', page)
        self.assertIn('canvas data-role="focus-frame"', page)
        self.assertIn("renderDirectFocusFrame(camera, state)", page)
        self.assertIn('tile?.classList.add("direct-focus-pending")', page)
        self.assertIn("keepDirectMediaAtLiveEdge(state)", page)
        self.assertIn('focusedSlug !== camera.slug', page)
        self.assertIn('tile.classList.contains("direct-focus-frame-live")', page)
        self.assertIn("const ownsMedia = directStates.get(camera.slug) === state", page)
        self.assertIn("async function visibleImageObjectUrl(blob)", page)
        self.assertIn("hasVisiblePixels(state.probeContext, 16, 9)", page)
        self.assertIn('if (nextFocus) tile.classList.add("direct-focus-pending")', page)
        self.assertIn("const initialImageStaggerMs = 20", page)
        self.assertNotIn("index * 1200", page)
        self.assertIn("async function presentImageObjectUrl(camera, imageUrl)", page)
        self.assertIn('class="snapshot-active" data-role="image"', page)
        self.assertIn("await Promise.allSettled([", page)
        self.assertIn(".tile.direct-mse-live img", page)
        self.assertIn("const imageRevealMs = 70", page)
        self.assertIn("const reveal = next.animate(", page)
        self.assertIn('next.classList.add("snapshot-entering")', page)
        self.assertNotIn("transition: opacity", page)
        self.assertNotIn(".tile::after", page)
        self.assertIn("const dragThresholdPx = 20", page)
        self.assertIn("const dragHoldMs = 450", page)
        self.assertIn("const dragHoldMoveTolerancePx = 10", page)
        self.assertIn(
            'tile.draggable = !window.matchMedia("(pointer: coarse)").matches',
            page,
        )
        self.assertIn("-webkit-user-select: none", page)
        self.assertIn("-webkit-tap-highlight-color: transparent", page)
        self.assertIn(
            'tile.addEventListener("selectstart", preventTileSelection)',
            page,
        )
        self.assertIn("window.getSelection()?.removeAllRanges()", page)
        self.assertIn('tile.addEventListener("pointerdown", handlePointerDown)', page)
        self.assertIn("drag.holdTimer = setTimeout", page)
        self.assertIn("drag.armed = true", page)
        self.assertIn('"drag-armed",\n        "dragging",', page)
        self.assertIn("distance > dragHoldMoveTolerancePx", page)
        self.assertIn("distance <= dragThresholdPx", page)
        self.assertIn("pointerDropTarget(event.clientX, event.clientY)", page)
        self.assertIn("[nextOrder[fromIndex], nextOrder[targetIndex]]", page)
        self.assertNotIn("nextOrder.splice", page)
        self.assertIn("const expandedPointers = new Map()", page)
        self.assertIn("expandedPointers.size > 1", page)
        self.assertIn('document.addEventListener("gesturestart", handleSafariPinch', page)
        self.assertIn("Date.now() < suppressClickUntil", page)
        self.assertIn("const expandedClickSuppressMs = 180", page)
        self.assertNotIn("Date.now() + 1000", page)
        self.assertIn(".tile { touch-action: pan-y; }", page)
        self.assertIn(".tile.expanded { touch-action: pan-x pan-y pinch-zoom; }", page)

    def test_monitor_server_accepts_parallel_browser_connections(self) -> None:
        self.assertGreaterEqual(camera_monitor.MonitorServer.request_queue_size, 32)
        self.assertTrue(camera_monitor.MonitorServer.daemon_threads)


class NestBackendTest(unittest.TestCase):
    def test_builds_direct_go2rtc_source(self) -> None:
        credentials = camera_backends.NestCredentials(
            client_id="client",
            client_secret="secret",
            refresh_token="refresh",
            project_id="project",
        )
        source = camera_backends.build_nest_source(
            credentials,
            "enterprises/project/devices/device",
        )
        parsed = urllib.parse.urlsplit(source)
        values = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "nest")
        self.assertEqual(values["refresh_token"], ["refresh"])
        self.assertEqual(
            values["device_id"],
            ["device"],
        )

    def test_requires_all_direct_credentials(self) -> None:
        with mock.patch.dict(camera_backends.os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CAMERA_NEST_CLIENT_ID"):
                camera_backends.NestCredentials.from_environment()


class EufyBackendTest(unittest.TestCase):
    def test_surfaces_account_verification_without_storing_challenge_data(self) -> None:
        client = camera_backends.DirectEufyClient(
            "ws://eufy:3000",
            "http://go2rtc:1984",
        )

        client._handle_message(
            {
                "type": "event",
                "event": {"source": "driver", "event": "verify code"},
            }
        )

        self.assertEqual(client.last_error, "Eufy account verification is required")

    def test_replaces_existing_go2rtc_stream_before_upload(self) -> None:
        client = camera_backends.DirectEufyClient(
            "ws://eufy:3000",
            "http://go2rtc:1984",
        )
        with mock.patch.object(client, "_go2rtc_request") as request:
            client._prepare_go2rtc_stream("camera_eufy_backyard")

        self.assertEqual(
            request.call_args_list,
            [
                mock.call(
                    "DELETE",
                    "/api/streams",
                    {"src": "camera_eufy_backyard"},
                ),
                mock.call(
                    "PUT",
                    "/api/streams",
                    {
                        "name": "camera_eufy_backyard",
                        "src": "tcp://127.0.0.1:65535",
                    },
                ),
            ],
        )

    def test_routes_video_event_to_bounded_stream_queue(self) -> None:
        client = camera_backends.DirectEufyClient(
            "ws://eufy:3000",
            "http://go2rtc:1984",
        )
        stream = camera_backends._EufyStream("T123", "camera_eufy_backyard")
        stream.active.set()
        client.streams["T123"] = stream

        client._handle_message(
            {
                "type": "event",
                "event": {
                    "event": "livestream video data",
                    "serialNumber": "T123",
                    "buffer": {"data": [0, 1, 2, 255]},
                },
            }
        )

        self.assertEqual(stream.chunks.get_nowait(), b"\x00\x01\x02\xff")
        self.assertTrue(stream.first_video.is_set())

    def test_upload_uses_isolated_go2rtc_stream_name(self) -> None:
        client = camera_backends.DirectEufyClient(
            "ws://eufy:3000",
            "http://go2rtc:1984",
        )
        stream = camera_backends._EufyStream(
            "T123",
            "camera_eufy_backyard",
        )
        connection = mock.Mock()
        connection.getresponse.return_value.status = 200

        with mock.patch.object(
            camera_backends.http.client,
            "HTTPConnection",
            return_value=connection,
        ):
            client._upload_stream(stream)

        connection.putrequest.assert_called_once_with(
            "POST",
            "/api/stream?dst=camera_eufy_backyard",
        )

    def test_command_results_wake_waiter(self) -> None:
        client = camera_backends.DirectEufyClient(
            "ws://eufy:3000",
            "http://go2rtc:1984",
        )
        pending = camera_backends._PendingCommand()
        client.pending["one"] = pending

        client._handle_message(
            {"type": "result", "messageId": "one", "success": True}
        )

        self.assertTrue(pending.event.is_set())
        self.assertTrue(pending.response["success"])

    def test_cancelled_start_stops_remote_livestream(self) -> None:
        client = camera_backends.DirectEufyClient(
            "ws://eufy:3000",
            "http://go2rtc:1984",
        )
        client.connected.set()
        commands: list[str] = []

        def command(name: str, _serial: str, **_kwargs: object) -> dict[str, object]:
            commands.append(name)
            return {}

        with (
            mock.patch.object(client, "_prepare_go2rtc_stream"),
            mock.patch.object(client, "_upload_stream"),
            mock.patch.object(client, "_command", side_effect=command),
        ):
            with self.assertRaises(InterruptedError):
                client.start_stream("T123", wanted=lambda: False)

        self.assertEqual(
            commands,
            ["device.start_livestream", "device.stop_livestream"],
        )
        self.assertNotIn("T123", client.streams)


if __name__ == "__main__":
    unittest.main()
