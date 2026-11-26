"""Check the actual reconnect encoder and the released PC's OTA loader."""
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from pc_client import online_updates as updates
from pc_client.firmware_update import FirmwareImage
from pc_client.protocol import Lines, decode

encoder, binary, version = sys.argv[1:]
fixtures = json.loads(Path("tools/tests/reconnect_frames.json").read_text())
for label, fixture in fixtures.items():
    request = json.dumps(fixture).encode()
    old = subprocess.check_output([encoder, "legacy"], input=request)
    line = subprocess.check_output([encoder], input=request)
    (reply,) = map(decode, Lines().feed(line))
    assert reply["data"] == fixture["data"] and reply["id"] == fixture["id"]
    if label == "on":
        assert old == b"" and "applied" not in reply
        assert reply["data"]["device_id"] and reply["data"]["v"] == 1
    else:
        assert line == old and reply["applied"]["enabled"] is False

# USB in 1.2.4 adds a capability without changing old client framing. In
# particular, an oversized full response must still preserve the capability.
if tuple(map(int, version.split("."))) >= (1, 2, 4):
    for fixture in fixtures.values():
        usb_caps = {
            **fixture["data"],
            "firmware": version,
            "diag_log": True,
            "clear_ble_bonds": True,
        }
        request = json.dumps({**fixture, "data": usb_caps}).encode()
        bounded = subprocess.check_output([encoder], input=request)
        assert 0 < len(bounded) <= 1024
        (reply,) = map(decode, Lines().feed(bounded))
        assert reply["data"] == usb_caps and reply["id"] == fixture["id"]

image = FirmwareImage.load(binary)
assert image.version == version
stable = dict(version=version, target="PowerModule-ESP32", protocol=1,
              url=updates.release_url("firmware", version),
              sha256=image.digest, size=len(image.data))
update = updates.parse_catalog(dict(schema=1, kind="firmware", stable=stable), "firmware")
target = Path(binary).with_name(update.filename)
with patch.object(updates, "_open", return_value=io.BytesIO(image.data)):
    updates.download(update, target)
assert FirmwareImage.load(target) == image
print(f"Firmware {image.version}: {len(image.data)} bytes, SHA-256 {image.digest}")
