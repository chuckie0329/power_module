"""Package the Arduino ESP32 4 MB default layout as a sparse Intel HEX."""
import argparse
from hashlib import sha256
from pathlib import Path
import shlex
from struct import unpack_from

from intelhex import IntelHex
from pc_client.firmware_update import APP_SLOT_SIZE, FirmwareImage

LAYOUT = {0x1000: "PowerModule.ino.bootloader.bin", 0x8000: "PowerModule.ino.partitions.bin",
          0xe000: "boot_app0.bin", 0x10000: "PowerModule.ino.bin"}
MAX_END = {0x1000: 0x8000, 0x8000: 0x9000, 0xe000: 0x10000, 0x10000: 0x10000 + APP_SLOT_SIZE}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def package(build, app, version, output, expected_sha=None):
    build = Path(build)
    args = shlex.split((build / "flash_args").read_text(encoding="ascii"))
    opts = dict(zip(args[:6:2], args[1:6:2], strict=True))
    require(opts == {"--flash-mode": "dio", "--flash-freq": "40m", "--flash-size": "4MB"},
            f"Unexpected board flash geometry: {opts}")
    files = {int(offset, 0): name for offset, name in zip(args[6::2], args[7::2], strict=True)}
    require(files == LAYOUT and len(args) == 14, f"Unexpected ESP32 flash layout: {files}")
    image = FirmwareImage.load(app)
    built = FirmwareImage.load(build / files[0x10000])
    require(built.version == image.version == version, "ESP32 build/application version disagrees with release tag")
    require(expected_sha is None or image.digest == expected_sha, "Published OTA application checksum mismatch")
    chunks = {offset: image.data if offset == 0x10000 else (build / name).read_bytes()
              for offset, name in LAYOUT.items()}
    for offset, data in chunks.items():
        require(bool(data) and offset + len(data) <= MAX_END[offset], f"Invalid segment bounds at {offset:#x}")
    require(chunks[0x1000][0] == 0xe9, "Invalid second-stage ESP32 bootloader")
    entries = {}
    table = chunks[0x8000]
    for i in range(0, len(table) - 31, 32):
        magic, kind, subtype, addr, size = unpack_from("<HBBII", table, i)
        if magic != 0x50aa:
            break
        entries[(kind, subtype)] = (addr, size)
    require(entries.get((0, 0x10)) == (0x10000, APP_SLOT_SIZE), "Unexpected OTA slot 0 partition")
    require(entries.get((1, 0)) == (0xe000, 0x2000), "Unexpected OTA metadata partition")
    require(entries.get((1, 2)) == (0x9000, 0x5000), "Unexpected NVS partition")
    merged = IntelHex()
    for offset, data in chunks.items():
        merged.frombytes(data, offset=offset)
    merged.write_hex_file(str(output))
    decoded = IntelHex(str(output))
    # Adjacent source segments can be merged by IntelHex; compare the entire sparse address map.
    require(decoded.segments() == merged.segments(), "HEX layout did not round trip")
    for offset, data in chunks.items():
        require(bytes(decoded.tobinarray(start=offset, size=len(data))) == data, f"HEX payload mismatch at {offset:#x}")
    require(not any(start < 0xe000 and end > 0x9000 for start, end in decoded.segments()),
            "HEX must not touch NVS")
    require(sha256(bytes(decoded.tobinarray(start=0x10000, size=len(image.data)))).hexdigest() == image.digest,
            "HEX does not contain exactly the release application")
    print(f"Verified ESP32 {version} sparse HEX, OTA SHA256 {image.digest}: {decoded.segments()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("app", type=Path)
    parser.add_argument("version")
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-sha")
    a = parser.parse_args()
    package(a.build, a.app, a.version, a.output, a.expected_sha)
