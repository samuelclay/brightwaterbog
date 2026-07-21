#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import camera_warm_agent


class FakeProcess:
    pid = 1234
    returncode = None

    def poll(self) -> None:
        return None


class JsonResponse:
    def __init__(self, payload: dict[str, str]) -> None:
        self.payload = payload

    def __enter__(self) -> "JsonResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class SharedBrowserTest(unittest.TestCase):
    @mock.patch.object(camera_warm_agent.time, "sleep")
    @mock.patch.object(camera_warm_agent.urllib.request, "urlopen")
    @mock.patch.object(camera_warm_agent.subprocess, "Popen")
    def test_opens_one_process_with_one_tab_per_camera(
        self,
        popen: mock.Mock,
        urlopen: mock.Mock,
        _sleep: mock.Mock,
    ) -> None:
        popen.return_value = FakeProcess()
        urlopen.side_effect = [
            JsonResponse({"Browser": "Chromium"}),
            JsonResponse({"id": "tab-2"}),
            JsonResponse({"id": "tab-3"}),
        ]
        browser = camera_warm_agent.WarmBrowser(
            slugs=["nest-one", "nest-two", "nest-three"]
        )

        with tempfile.TemporaryDirectory() as profile_root:
            camera_warm_agent.start_browser(
                browser,
                "/usr/bin/chromium",
                "http://127.0.0.1:8765",
                Path(profile_root),
            )

        popen.assert_called_once()
        command = popen.call_args.args[0]
        self.assertIn("--renderer-process-limit=4", command)
        self.assertIn("--remote-debugging-port=9222", command)
        startup_urls = [argument for argument in command if argument.startswith("http://")]
        self.assertEqual(
            startup_urls,
            ["http://127.0.0.1:8765/?sentinel=1&camera=nest-one"],
        )

        tab_requests = [
            call.args[0]
            for call in urlopen.call_args_list
            if isinstance(call.args[0], urllib.request.Request)
        ]
        self.assertEqual(len(tab_requests), 2)
        self.assertTrue(all(request.method == "PUT" for request in tab_requests))
        self.assertIn("camera%3Dnest-two", tab_requests[0].full_url)
        self.assertIn("camera%3Dnest-three", tab_requests[1].full_url)

    def test_reports_shared_browser_failure_from_stale_frames(self) -> None:
        browser = camera_warm_agent.WarmBrowser(
            slugs=["nest-one", "nest-two", "nest-three", "nest-four"]
        )
        statuses = {
            "nest-one": {"received_age_seconds": 10},
            "nest-two": {"received_age_seconds": 121},
            "nest-three": {"received_age_seconds": 600},
            "nest-four": {"received_age_seconds": 20},
        }

        issue = camera_warm_agent.webrtc_browser_health_issue(browser, statuses)

        self.assertIn("2/4 WebRTC cameras", issue or "")
        statuses["nest-two"]["received_age_seconds"] = 10
        self.assertIsNone(
            camera_warm_agent.webrtc_browser_health_issue(browser, statuses)
        )


class EufyRefreshTest(unittest.TestCase):
    @mock.patch.object(camera_warm_agent, "post_json")
    @mock.patch.object(camera_warm_agent, "fetch_status")
    def test_known_start_failure_does_not_consume_full_refresh_timeout(
        self,
        fetch_status: mock.Mock,
        post_json: mock.Mock,
    ) -> None:
        fetch_status.side_effect = [
            {
                "driveway": {
                    "latest_received_at": 10,
                    "received_age_seconds": 600,
                    "consecutive_failure_count": 0,
                }
            },
            {
                "driveway": {
                    "latest_received_at": 10,
                    "received_age_seconds": 600,
                    "consecutive_failure_count": 1,
                    "last_start_status": 500,
                    "last_error": "start failed",
                }
            },
        ]

        refreshed = camera_warm_agent.refresh_eufy_camera(
            "http://127.0.0.1:8765",
            "driveway",
            threading.Event(),
        )

        self.assertFalse(refreshed)
        self.assertEqual(fetch_status.call_count, 2)
        self.assertEqual(post_json.call_count, 2)


class PowerRestoreRecoveryTest(unittest.TestCase):
    def test_power_restore_opt_in_does_not_limit_normal_warming(self) -> None:
        config = {
            "cameras": [
                {
                    "slug": "backyard",
                    "source": "eufy_p2p",
                    "keep_warm": True,
                },
                {
                    "slug": "dam",
                    "source": "eufy_p2p",
                    "keep_warm": True,
                    "recover_on_power_restore": True,
                    "power_entity_id": "switch.dam_camera_power",
                    "ensure_power_on": True,
                },
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "cameras.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            inventory = camera_warm_agent.load_warm_inventory(config_path)

        self.assertEqual(inventory.eufy_slugs, ["backyard", "dam"])
        self.assertEqual(
            [target.slug for target in inventory.power_restore_targets],
            ["dam"],
        )
        self.assertTrue(inventory.power_restore_targets[0].ensure_power_on)

    def test_requires_confirmed_offline_and_online_transitions(self) -> None:
        target = camera_warm_agent.EufyTarget("dam", "192.0.2.10")
        tracker = camera_warm_agent.EufyReachabilityTracker([target])

        self.assertIsNone(tracker.observe("dam", True))
        self.assertIsNone(tracker.observe("dam", False))
        self.assertIsNone(tracker.observe("dam", False))
        self.assertEqual(tracker.observe("dam", False), "offline")
        self.assertIsNone(tracker.observe("dam", True))
        self.assertEqual(tracker.observe("dam", True), "restored")

    def test_stale_camera_recovers_after_agent_restart(self) -> None:
        target = camera_warm_agent.EufyTarget(
            "driveway",
            "192.0.2.11",
            "switch.driveway_camera_power",
        )
        inventory = camera_warm_agent.WarmInventory(
            webrtc_slugs=[],
            eufy_slugs=["driveway"],
            power_restore_targets=[target],
            eufy_addon="eufy-addon",
            go2rtc_addon="go2rtc-addon",
        )
        restored = camera_warm_agent.stale_power_restore_targets(
            inventory,
            {"driveway": {"received_age_seconds": 301}},
        )

        self.assertEqual(restored, ["driveway"])

    @mock.patch.object(camera_warm_agent, "turn_on_camera_power")
    @mock.patch.object(camera_warm_agent, "camera_power_state")
    def test_turns_dedicated_outlet_on_and_confirms_restore(
        self,
        camera_power_state: mock.Mock,
        turn_on_camera_power: mock.Mock,
    ) -> None:
        target = camera_warm_agent.EufyTarget(
            "dam",
            "192.0.2.10",
            "switch.dam_camera_power",
            ensure_power_on=True,
        )
        inventory = camera_warm_agent.WarmInventory(
            webrtc_slugs=[],
            eufy_slugs=["dam"],
            power_restore_targets=[target],
            eufy_addon="eufy-addon",
            go2rtc_addon="go2rtc-addon",
        )
        tracker = camera_warm_agent.EufyReachabilityTracker([target])
        attempted_at: dict[str, float] = {}
        camera_power_state.side_effect = ["off", "on", "on"]

        first, first_states = camera_warm_agent.poll_eufy_reachability(
            inventory,
            tracker,
            "http://supervisor/core",
            "token",
            attempted_at,
        )
        second, _ = camera_warm_agent.poll_eufy_reachability(
            inventory,
            tracker,
            "http://supervisor/core",
            "token",
            attempted_at,
        )
        third, _ = camera_warm_agent.poll_eufy_reachability(
            inventory,
            tracker,
            "http://supervisor/core",
            "token",
            attempted_at,
        )

        self.assertEqual(first, [])
        self.assertEqual(first_states, {"dam": False})
        self.assertEqual(second, [])
        self.assertEqual(third, ["dam"])
        turn_on_camera_power.assert_called_once_with(
            target,
            "http://supervisor/core",
            "token",
        )

    def test_waits_for_auto_power_before_startup_recovery(self) -> None:
        target = camera_warm_agent.EufyTarget(
            "dam",
            "192.0.2.10",
            "switch.dam_camera_power",
            ensure_power_on=True,
        )
        inventory = camera_warm_agent.WarmInventory(
            webrtc_slugs=[],
            eufy_slugs=["dam"],
            power_restore_targets=[target],
            eufy_addon="eufy-addon",
            go2rtc_addon="go2rtc-addon",
        )

        restored = camera_warm_agent.stale_power_restore_targets(
            inventory,
            {"dam": {"received_age_seconds": 3600}},
            {"dam": False},
        )

        self.assertEqual(restored, [])

    @mock.patch.object(camera_warm_agent, "post_json")
    def test_turn_on_camera_power_calls_home_assistant(
        self,
        post_json: mock.Mock,
    ) -> None:
        target = camera_warm_agent.EufyTarget(
            "driveway",
            "192.0.2.11",
            "switch.driveway_camera_power",
            ensure_power_on=True,
        )

        camera_warm_agent.turn_on_camera_power(
            target,
            "http://supervisor/core",
            "token",
        )

        post_json.assert_called_once_with(
            "http://supervisor/core/api/services/switch/turn_on",
            {"entity_id": "switch.driveway_camera_power"},
            "token",
        )

    @mock.patch.object(camera_warm_agent.urllib.request, "urlopen")
    def test_reads_home_assistant_power_switch_state(
        self,
        urlopen: mock.Mock,
    ) -> None:
        target = camera_warm_agent.EufyTarget(
            "dam",
            "192.0.2.10",
            "switch.dam_camera_power",
        )
        urlopen.side_effect = [
            JsonResponse({"state": "on"}),
            JsonResponse({"state": "unavailable"}),
        ]

        self.assertTrue(
            camera_warm_agent.camera_power_is_on(
                target,
                "http://supervisor/core",
                "token",
            )
        )
        self.assertFalse(
            camera_warm_agent.camera_power_is_on(
                target,
                "http://supervisor/core",
                "token",
            )
        )
        request = urlopen.call_args_list[0].args[0]
        self.assertEqual(
            request.full_url,
            "http://supervisor/core/api/states/switch.dam_camera_power",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer token")

    @mock.patch.object(camera_warm_agent.time, "sleep")
    @mock.patch.object(camera_warm_agent, "restart_addon")
    @mock.patch.object(camera_warm_agent, "post_json")
    def test_shared_recovery_reloads_eufy_integration(
        self,
        post_json: mock.Mock,
        restart_addon: mock.Mock,
        _sleep: mock.Mock,
    ) -> None:
        post_json.side_effect = [
            {"paused": True},
            {"domain": "eufy_security", "reloaded": 1},
            {"paused": False},
        ]

        camera_warm_agent.recover_eufy_stack(
            "http://127.0.0.1:8765",
            "http://supervisor/core",
            "token",
            "eufy-addon",
            "go2rtc-addon",
            ["dam", "driveway"],
            reason="camera power restoration",
        )

        self.assertEqual(
            [call.args[0] for call in post_json.call_args_list],
            [
                "http://127.0.0.1:8765/api/pause",
                "http://127.0.0.1:8765/api/reload/config-entry/eufy_security",
                "http://127.0.0.1:8765/api/resume",
            ],
        )
        self.assertEqual(
            [call.args[-1] for call in restart_addon.call_args_list],
            ["eufy-addon", "go2rtc-addon"],
        )


if __name__ == "__main__":
    unittest.main()
