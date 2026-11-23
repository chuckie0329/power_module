import json
import struct
import time

from firmware.tools.usb_check import canonical, crc32


VERSION = 1
MAX_MESSAGE = 1024
SERVICE = "4d505301-7e64-4d4a-9f80-021a56c11000"
COMMAND, RESPONSE, STATE, INFO, HEARTBEAT = (SERVICE[:-1] + str(n) for n in range(1, 6))
HEADER = struct.Struct("<BBHBBH")


def encode(document):
    frame = dict(document)
    frame["crc32"] = crc32(frame)
    line = canonical(frame) + b"\n"
    if len(line) > MAX_MESSAGE:
        raise ValueError("Message exceeds 1024 bytes")
    return line


def decode(line):
    if len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
        return None
    try:
        frame = json.loads(line)
        if isinstance(frame, dict) and frame.get("v") == VERSION:
            checksum = frame.pop("crc32", None)
            if checksum == crc32(frame):
                return frame
    except (ValueError, UnicodeError, RecursionError):
        pass
    return None


class Lines:
    def __init__(self):
        self.buffer = bytearray()
        self.discarding = False

    def feed(self, data):
        parts = data.split(b"\n")
        for part in parts[:-1]:
            line = None
            if not self.discarding and len(self.buffer) + len(part) < MAX_MESSAGE:
                line = bytes(self.buffer) + part + b"\n"
            self.discarding = False
            self.buffer.clear()
            if line is not None:
                yield line
        if not self.discarding:
            if len(self.buffer) + len(parts[-1]) < MAX_MESSAGE:
                self.buffer.extend(parts[-1])
            else:
                self.discarding = True
                self.buffer.clear()


def fragments(line, message_number, mtu):
    payload = min(512, max(23, mtu) - 3) - HEADER.size
    count = (len(line) + payload - 1) // payload
    for index in range(count):
        start = index * payload
        yield HEADER.pack(VERSION, 0, message_number, index, count, len(line)) + line[start:start + payload]


class Fragments:
    def __init__(self):
        self.buffer = bytearray()
        self.next_index = 0
        self.message = None
        self.started = 0.0
        self.payload_size = 0

    def feed(self, packet):
        if not HEADER.size < len(packet) <= 512:
            self.message = None
            return None
        version, flags, number, index, count, total = HEADER.unpack_from(packet)
        if version != VERSION or flags or not 0 < count or not 0 < total <= MAX_MESSAGE or index >= count:
            self.message = None
            return None
        payload = packet[HEADER.size:]
        now = time.monotonic()
        identity = (number, count, total)
        if index == 0:
            self.buffer.clear()
            self.message = identity
            self.next_index = 0
            self.started = now
            self.payload_size = len(payload)
            if count != (total + self.payload_size - 1) // self.payload_size:
                self.message = None
                return None
        if (
            self.message != identity or index != self.next_index or now - self.started > 15
            or len(payload) != min(self.payload_size, total - len(self.buffer))
        ):
            self.message = None
            return None
        self.buffer.extend(payload)
        self.next_index += 1
        if self.next_index == count:
            self.message = None
            return bytes(self.buffer)
        return None
