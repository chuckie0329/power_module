from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re


APP_SLOT_SIZE = 0x140000
IDENTITY_OFFSET = 24 + 8 + 256
PROJECT_FIELD = b"PowerModule-ESP32".ljust(20, b"\0")
VERSION_BYTES = 16
MIN_FIRMWARE_VERSION = (1, 2, 0)


@dataclass(frozen=True, slots=True)
class FirmwareImage:
    data: bytes
    version: str
    digest: str

    @classmethod
    def load(cls, filename):
        with Path(filename).open("rb") as source:
            data = source.read(APP_SLOT_SIZE + 1)
        version_at = IDENTITY_OFFSET + len(PROJECT_FIELD)
        if (
            not version_at + VERSION_BYTES + 32 <= len(data) <= APP_SLOT_SIZE
            or data[0] != 0xE9 or data[12:14] != b"\0\0"
            or data[IDENTITY_OFFSET:version_at] != PROJECT_FIELD
            or data[23] != 1 or sha256(data[:-32]).digest() != data[-32:]
        ):
            raise ValueError("OTA_BAD_IMAGE")
        version = data[version_at:version_at + VERSION_BYTES].split(b"\0", 1)[0]
        if not re.fullmatch(rb"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("OTA_BAD_IMAGE")
        if tuple(map(int, version.split(b"."))) < MIN_FIRMWARE_VERSION:
            raise ValueError("OTA_BAD_IMAGE")
        return cls(data, version.decode("ascii"), sha256(data).hexdigest())
