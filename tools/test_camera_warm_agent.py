#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import camera_warm_agent


class LightweightCameraWarmTest(unittest.TestCase):
    @mock.patch.object(camera_warm_agent, "capture_wanted_eufy_thumbnails")
    @mock.patch.object(camera_warm_agent, "fetch_status")
    def test_nest_warming_uses_only_the_status_heartbeat(
        self,
        fetch_status: mock.Mock,
        capture_eufy: mock.Mock,
    ) -> None:
        fetch_status.return_value = {
            "warm_allowed": True,
            "viewer_active": False,
            "cameras": [],
        }
        stopping = threading.Event()

        def stop_after_first_wait(_seconds: float) -> bool:
            stopping.set()
            return True

        stopping.wait = stop_after_first_wait  # type: ignore[method-assign]
        camera_warm_agent.run_agent(
            camera_warm_agent.WarmInventory(
                ["nest-one", "nest-two"],
                ["eufy-one"],
            ),
            "http://127.0.0.1:8765",
            stopping,
            go2rtc_url="http://go2rtc:1984",
        )

        fetch_status.assert_called_once_with(
            "http://127.0.0.1:8765",
            touch_warm=True,
        )
        capture_eufy.assert_called_once_with(
            fetch_status.return_value,
            mock.ANY,
            "http://127.0.0.1:8765",
            "http://go2rtc:1984",
        )

    @mock.patch.object(camera_warm_agent.urllib.request, "urlopen")
    def test_eufy_thumbnail_is_captured_from_go2rtc_and_uploaded(
        self,
        urlopen: mock.Mock,
    ) -> None:
        frame = b"\xff\xd8fresh-jpeg"
        frame_response = mock.MagicMock()
        frame_response.__enter__.return_value = frame_response
        frame_response.headers.get_content_type.return_value = "image/jpeg"
        frame_response.read.return_value = frame
        upload_response = mock.MagicMock()
        upload_response.__enter__.return_value = upload_response
        upload_response.status = 200
        upload_response.read.return_value = b'{"ok":true}'
        urlopen.side_effect = [frame_response, upload_response]

        captured = camera_warm_agent.capture_eufy_thumbnail(
            "front-door",
            "http://monitor:8765",
            "http://go2rtc:1984",
        )

        self.assertTrue(captured)
        self.assertEqual(
            urlopen.call_args_list[0].args[0],
            "http://go2rtc:1984/api/frame.jpeg?src=camera_eufy_front-door",
        )
        upload = urlopen.call_args_list[1].args[0]
        self.assertEqual(upload.full_url, "http://monitor:8765/api/frame/front-door")
        self.assertEqual(upload.data, frame)
        self.assertEqual(upload.get_method(), "POST")


class InventoryTest(unittest.TestCase):
    def test_loads_only_nest_warm_cameras(self) -> None:
        config = {
            "cameras": [
                {"slug": "door", "source": "eufy", "keep_warm": True},
                {"slug": "yard", "source": "nest", "keep_warm": True},
                {"slug": "idle", "source": "nest", "keep_warm": False},
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cameras.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            inventory = camera_warm_agent.load_warm_inventory(path)
        self.assertEqual(inventory.nest_slugs, ["yard"])
        self.assertEqual(inventory.eufy_slugs, ["door"])


if __name__ == "__main__":
    unittest.main()
