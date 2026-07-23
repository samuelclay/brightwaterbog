from __future__ import annotations

import struct
import unittest

import camera_mdns_alias as mdns_alias


def response_types(packet: bytes) -> tuple[tuple[int, int], list[int]]:
    _transaction_id, _flags, questions, answers, authorities, additional = struct.unpack_from(
        "!HHHHHH", packet, 0
    )
    assert questions == 0
    assert authorities == 0

    offset = 12
    record_types: list[int] = []
    for _ in range(answers + additional):
        _name, offset = mdns_alias.parse_name(packet, offset)
        record_type, _record_class, _ttl, rdata_length = struct.unpack_from(
            "!HHIH", packet, offset
        )
        offset += 10 + rdata_length
        record_types.append(record_type)
    return (answers, additional), record_types


class MdnsAliasTest(unittest.TestCase):
    def test_parse_aliases(self) -> None:
        self.assertEqual(
            mdns_alias.parse_aliases("cameras.local, cameras.local."),
            ["cameras.local"],
        )

    def test_positive_response_includes_a_and_nsec(self) -> None:
        packet = mdns_alias.build_positive_response("cameras.local", "192.0.2.10")
        self.assertEqual(
            response_types(packet),
            ((1, 1), [mdns_alias.DNS_TYPE_A, mdns_alias.DNS_TYPE_NSEC]),
        )

    def test_negative_response_includes_nsec_and_a(self) -> None:
        packet = mdns_alias.build_negative_response("cameras.local", "192.0.2.10")
        self.assertEqual(
            response_types(packet),
            ((1, 1), [mdns_alias.DNS_TYPE_NSEC, mdns_alias.DNS_TYPE_A]),
        )

    def test_aaaa_query_gets_negative_response(self) -> None:
        questions = [("cameras.local", mdns_alias.DNS_TYPE_AAAA, mdns_alias.DNS_CLASS_IN)]
        self.assertEqual(mdns_alias.response_kind(questions, "cameras.local"), "negative")

    def test_parse_mappings_supports_different_addresses(self) -> None:
        self.assertEqual(
            mdns_alias.parse_mappings(
                "cameras.local=192.0.2.20, cameras-backup.local.=192.0.2.10"
            ),
            {
                "cameras.local": "192.0.2.20",
                "cameras-backup.local": "192.0.2.10",
            },
        )

    def test_parse_mappings_rejects_duplicate_alias(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate mDNS alias"):
            mdns_alias.parse_mappings(
                "cameras.local=192.0.2.20,cameras.local=192.0.2.21"
            )


if __name__ == "__main__":
    unittest.main()
