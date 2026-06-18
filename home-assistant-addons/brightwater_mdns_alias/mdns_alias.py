#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import socket
import struct
import time


MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
MDNS_TTL = 120


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


def build_response(alias: str, address: str, transaction_id: int = 0) -> bytes:
    name = encode_name(alias)
    header = struct.pack("!HHHHHH", transaction_id, 0x8400, 0, 1, 0, 0)
    answer = (
        name
        + struct.pack("!HHIH", 1, 0x8001, MDNS_TTL, 4)
        + ipaddress.IPv4Address(address).packed
    )
    return header + answer


def make_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except OSError:
        pass
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    sock.bind(("", MDNS_PORT))
    membership = socket.inet_aton(MDNS_GROUP) + socket.inet_aton("0.0.0.0")
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    sock.settimeout(1.0)
    return sock


def should_answer(questions: list[tuple[str, int, int]], alias: str) -> bool:
    normalized_alias = alias.rstrip(".").lower()
    for name, qtype, qclass in questions:
        if name == normalized_alias and qclass == 1 and qtype in {1, 255}:
            return True
    return False


def publish(sock: socket.socket, response: bytes) -> None:
    sock.sendto(response, (MDNS_GROUP, MDNS_PORT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish a single IPv4 mDNS alias.")
    parser.add_argument("--alias", required=True)
    parser.add_argument("--address", required=True)
    args = parser.parse_args()

    alias = args.alias.rstrip(".")
    address = str(ipaddress.IPv4Address(args.address))
    response = build_response(alias, address)
    sock = make_socket()
    next_announcement = 0.0
    print(f"Publishing {alias}. -> {address} via mDNS", flush=True)

    while True:
        now = time.time()
        if now >= next_announcement:
            publish(sock, response)
            next_announcement = now + 30

        try:
            packet, _source = sock.recvfrom(9000)
        except socket.timeout:
            continue
        except OSError:
            time.sleep(1)
            continue

        try:
            transaction_id = struct.unpack_from("!H", packet, 0)[0] if len(packet) >= 2 else 0
            if should_answer(parse_questions(packet), alias):
                publish(sock, build_response(alias, address, transaction_id))
        except Exception as exc:  # noqa: BLE001 - keep the responder alive.
            print(f"Ignoring malformed mDNS query: {exc}", flush=True)


if __name__ == "__main__":
    main()
