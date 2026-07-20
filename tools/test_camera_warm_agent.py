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


if __name__ == "__main__":
    unittest.main()
