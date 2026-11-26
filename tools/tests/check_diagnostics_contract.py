"""Decode real ESP32 diagnostic frames with the Windows client's reader."""
import asyncio
import json
import subprocess
import sys

from pc_client.diagnostics import read_snapshot, serialize
from pc_client.protocol import Lines, decode


def main(encoder):
    wire = subprocess.check_output([encoder, "--wire-fixture"])
    reader = Lines()
    frames = []
    for offset in range(0, len(wire), 17):
        frames.extend(reader.feed(wire[offset:offset + 17]))
    assert len(frames) == 3 and not reader.buffer
    replies = iter(map(decode, frames))
    seen = []
    header = None

    async def call(op, args):
        nonlocal header
        reply = next(replies)
        assert reply and reply["type"] == "reply" and reply["ok"] is True
        if not seen:
            assert op == "diag_info" and args == {}
            header = reply["data"]
        else:
            assert op == "diag_read" and args == {
                "boot_id": header["boot_id"],
                "from": seen[-1]["next"] if len(seen) > 1 else header["oldest"],
                "until": header["next"],
            }
        seen.append(reply["data"])
        return reply

    snapshot = asyncio.run(read_snapshot(call))
    assert len(seen) == 3 and snapshot["events"]
    assert not snapshot["overwritten_during_read"]
    assert any(row["event"] == "disconnected" for row in snapshot["events"])
    by_name = {row["event"]: row for row in snapshot["events"]}
    rssi = by_name["ble_last_rssi"]
    params = by_name["ble_conn_parameters"]
    failures = by_name["ble_tx_failures"]
    drop = by_name["disconnected"]
    assert (rssi["rssi_dbm"], rssi["sample_age_ms"]) == (-66, 380)
    assert (params["interval_ms"], params["peripheral_latency"], params["supervision_timeout_ms"]) == (30, 2, 6000)
    assert (failures["notification_failures"], failures["indication_failures"]) == (2, 1)
    assert drop["gap_reason"] == 520 and {row["uptime_ms"] for row in (rssi, params, failures, drop)} == {2400}
    # The released 1.0.10 client intentionally treats the additive codes as raw.
    raw = [(row["code"], row["a"], row["b"], row["uptime_ms"]) for row in snapshot["events"]
           if row["event"] == "unknown"]
    assert raw == [(14, 6, 300, 2400), (15, 7, 30, 2400)]
    parsed = json.loads(serialize(snapshot, "1.2.6", "1.0.10"))
    assert parsed["events"] == snapshot["events"] and parsed["firmware"] == "1.2.6"
    print(f"Firmware → USB CRC frames → client JSON: {len(snapshot['events'])} events")


if __name__ == "__main__":
    main(sys.argv[1])
