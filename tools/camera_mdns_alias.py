#!/usr/bin/env python3
"""Publish the local cameras hostname without relying on another server."""
from __future__ import annotations

import argparse
import ipaddress
import socket
import struct
import time


MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
MDNS_TTL = 120
DNS_TYPE_A = 1
DNS_TYPE_AAAA = 28
DNS_TYPE_NSEC = 47
DNS_TYPE_ANY = 255
DNS_CLASS_IN = 1
DNS_CLASS_ANY = 255
DNS_CACHE_FLUSH = 0x8000


def encode_name(name: str) -> bytes:
    labels = name.rstrip(".").split(".")
    return b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels) + b"\0"


def parse_name(packet: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    jumped = False
    end_offset = offset
    seen = 0

    while True:
        if offset >= len(packet):
            raise ValueError("DNS name exceeds packet")
        length = packet[offset]
        if length == 0:
            if not jumped:
                end_offset = offset + 1
            return ".".join(labels).lower(), end_offset
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("DNS pointer exceeds packet")
            pointer = ((length & 0x3F) << 8) | packet[offset + 1]
            if not jumped:
                end_offset = offset + 2
            offset = pointer
            jumped = True
            seen += 1
            if seen > 16:
                raise ValueError("DNS pointer loop")
            continue
        offset += 1
        if offset + length > len(packet):
            raise ValueError("DNS label exceeds packet")
        labels.append(packet[offset : offset + length].decode("ascii", errors="ignore"))
        offset += length
        if not jumped:
            end_offset = offset


def parse_questions(packet: bytes) -> list[tuple[str, int, int]]:
    if len(packet) < 12:
        return []
    qdcount = struct.unpack_from("!H", packet, 4)[0]
    offset = 12
    questions: list[tuple[str, int, int]] = []
    for _ in range(qdcount):
        name, offset = parse_name(packet, offset)
        if offset + 4 > len(packet):
            return questions
        qtype, qclass = struct.unpack_from("!HH", packet, offset)
        offset += 4
        questions.append((name, qtype, qclass & 0x7FFF))
    return questions


def parse_aliases(value: str) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()
    for raw_alias in value.split(","):
        alias = raw_alias.strip().rstrip(".").lower()
        if not alias:
            continue
        if not alias.endswith(".local"):
            raise ValueError(f"mDNS alias must end in .local: {alias}")
        encode_name(alias)
        if alias not in seen:
            aliases.append(alias)
            seen.add(alias)
    if not aliases:
        raise ValueError("At least one mDNS alias is required")
    return aliases


def parse_mappings(value: str) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for raw_mapping in value.split(","):
        mapping = raw_mapping.strip()
        if not mapping:
            continue
        alias_value, separator, address_value = mapping.partition("=")
        if not separator:
            raise ValueError(f"mDNS mapping must use alias=address: {mapping}")
        aliases = parse_aliases(alias_value)
        if len(aliases) != 1:
            raise ValueError(f"mDNS mapping must contain one alias: {mapping}")
        alias = aliases[0]
        if alias in mappings:
            raise ValueError(f"Duplicate mDNS alias: {alias}")
        mappings[alias] = str(ipaddress.IPv4Address(address_value.strip()))
    if not mappings:
        raise ValueError("At least one mDNS mapping is required")
    return mappings


def build_record(alias: str, record_type: int, rdata: bytes) -> bytes:
    name = encode_name(alias)
    return (
        name
        + struct.pack(
            "!HHIH",
            record_type,
            DNS_CACHE_FLUSH | DNS_CLASS_IN,
            MDNS_TTL,
            len(rdata),
        )
        + rdata
    )


def build_address_record(alias: str, address: str) -> bytes:
    return build_record(alias, DNS_TYPE_A, ipaddress.IPv4Address(address).packed)


def build_nsec_record(alias: str) -> bytes:
    # RFC 6762 section 6.1: the next-domain name is the owner name, window 0
    # contains one bitmap byte, and bit 1 (0x40) says only an A record exists.
    # The NSEC bit itself must remain clear for a synthesized mDNS record.
    rdata = encode_name(alias) + bytes((0, 1, 0x40))
    return build_record(alias, DNS_TYPE_NSEC, rdata)


def build_positive_response(alias: str, address: str, transaction_id: int = 0) -> bytes:
    header = struct.pack("!HHHHHH", transaction_id, 0x8400, 0, 1, 0, 1)
    return header + build_address_record(alias, address) + build_nsec_record(alias)


def build_negative_response(alias: str, address: str, transaction_id: int = 0) -> bytes:
    header = struct.pack("!HHHHHH", transaction_id, 0x8400, 0, 1, 0, 1)
    return header + build_nsec_record(alias) + build_address_record(alias, address)


def make_socket(interface_address: str = "0.0.0.0") -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except OSError:
        pass
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    interface = socket.inet_aton(interface_address)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, interface)
    except OSError as exc:
        print(f"Could not set mDNS multicast interface to {interface_address}: {exc}", flush=True)
    sock.bind(("", MDNS_PORT))
    try:
        membership = socket.inet_aton(MDNS_GROUP) + interface
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    except OSError as exc:
        print(f"Could not join mDNS group on {interface_address}: {exc}; falling back to default interface", flush=True)
        membership = socket.inet_aton(MDNS_GROUP) + socket.inet_aton("0.0.0.0")
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    sock.settimeout(1.0)
    return sock


def response_kind(questions: list[tuple[str, int, int]], alias: str) -> str | None:
    normalized_alias = alias.rstrip(".").lower()
    matching_types = {
        qtype
        for name, qtype, qclass in questions
        if name == normalized_alias and qclass in {DNS_CLASS_IN, DNS_CLASS_ANY}
    }
    if matching_types & {DNS_TYPE_A, DNS_TYPE_ANY}:
        return "positive"
    if DNS_TYPE_AAAA in matching_types:
        return "negative"
    return None


def publish(sock: socket.socket, response: bytes) -> bool:
    try:
        sock.sendto(response, (MDNS_GROUP, MDNS_PORT))
    except OSError as exc:
        print(f"Could not publish mDNS response: {exc}", flush=True)
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish one or more IPv4 mDNS aliases.")
    parser.add_argument("--alias", help="Comma-separated .local aliases")
    parser.add_argument("--address")
    parser.add_argument(
        "--mappings",
        help="Comma-separated alias=address mappings; overrides --alias/--address",
    )
    parser.add_argument(
        "--interface-address",
        default="0.0.0.0",
        help="Local IPv4 interface used for mDNS multicast",
    )
    args = parser.parse_args()

    if args.mappings:
        mappings = parse_mappings(args.mappings)
    elif args.alias and args.address:
        address = str(ipaddress.IPv4Address(args.address))
        mappings = {alias: address for alias in parse_aliases(args.alias)}
    else:
        parser.error("provide --mappings or both --alias and --address")

    positive_responses = {
        alias: build_positive_response(alias, address)
        for alias, address in mappings.items()
    }
    sock = make_socket(str(ipaddress.IPv4Address(args.interface_address)))
    next_announcement = 0.0
    published = ", ".join(
        f"{alias} -> {address}" for alias, address in mappings.items()
    )
    print(f"Publishing {published} via mDNS", flush=True)

    while True:
        now = time.time()
        if now >= next_announcement:
            ok = all(publish(sock, response) for response in positive_responses.values())
            next_announcement = now + (30 if ok else 5)

        try:
            packet, _source = sock.recvfrom(9000)
        except socket.timeout:
            continue
        except OSError:
            time.sleep(1)
            continue

        try:
            transaction_id = struct.unpack_from("!H", packet, 0)[0] if len(packet) >= 2 else 0
            questions = parse_questions(packet)
            for alias, address in mappings.items():
                kind = response_kind(questions, alias)
                if kind == "positive":
                    publish(
                        sock,
                        build_positive_response(alias, address, transaction_id),
                    )
                elif kind == "negative":
                    publish(
                        sock,
                        build_negative_response(alias, address, transaction_id),
                    )
        except Exception as exc:  # noqa: BLE001 - keep the responder alive.
            print(f"Ignoring malformed mDNS query: {exc}", flush=True)


if __name__ == "__main__":
    main()
