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
