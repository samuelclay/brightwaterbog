#!/usr/bin/env python3

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import camera_warm_agent


class LightweightNestWarmTest(unittest.TestCase):
    @mock.patch.object(camera_warm_agent, "fetch_status")
    def test_nest_warming_uses_only_the_status_heartbeat(
        self,
        fetch_status: mock.Mock,
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
            camera_warm_agent.WarmInventory(["nest-one", "nest-two"]),
            "http://127.0.0.1:8765",
            stopping,
        )

        fetch_status.assert_called_once_with(
            "http://127.0.0.1:8765",
            touch_warm=True,
        )


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


if __name__ == "__main__":
    unittest.main()
