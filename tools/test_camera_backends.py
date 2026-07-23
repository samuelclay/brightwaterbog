#!/usr/bin/env python3

import unittest
import urllib.parse

from camera_backends import NestCredentials, build_nest_source, nest_device_key


class NestSourceTests(unittest.TestCase):
    def test_full_sdm_resource_is_reduced_to_bare_device_key(self) -> None:
        resource = "enterprises/project/devices/device-key"

        self.assertEqual(nest_device_key(resource), "device-key")

        source = build_nest_source(
            NestCredentials("client", "secret", "refresh", "project"),
            resource,
        )
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(source).query)
        self.assertEqual(query["device_id"], ["device-key"])

    def test_bare_device_key_is_preserved(self) -> None:
        self.assertEqual(nest_device_key("device-key"), "device-key")

    def test_invalid_device_resource_is_rejected(self) -> None:
        for value in ("", "enterprises/project/devices/", "not/a/device"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    nest_device_key(value)


if __name__ == "__main__":
    unittest.main()
