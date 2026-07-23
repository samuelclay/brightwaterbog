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


class NativeStreamTest(unittest.TestCase):
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
        self.assertEqual(camera_monitor.upstream_stream_name(eufy), "T123")
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
            client._prepare_go2rtc_stream("T123")

        self.assertEqual(
            request.call_args_list,
            [
                mock.call("DELETE", "/api/streams", {"src": "T123"}),
                mock.call(
                    "PUT",
                    "/api/streams",
                    {"name": "T123", "src": "tcp://127.0.0.1:65535"},
                ),
            ],
        )

    def test_routes_video_event_to_bounded_stream_queue(self) -> None:
        client = camera_backends.DirectEufyClient(
            "ws://eufy:3000",
            "http://go2rtc:1984",
        )
        stream = camera_backends._EufyStream("T123")
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
