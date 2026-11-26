"""Read a bounded snapshot of the ESP32 USB diagnostic ring."""

from datetime import datetime, timezone
import json


ACTION = "导出诊断日志"
UINT32_MAX = (1 << 32) - 1
INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
CAPACITY = 64
PAGE_SIZE = 8
EVENTS = {
    1: ("boot", "reset_reason", None),
    2: ("advertising_initial", "started", None),
    3: ("connected", "handle", None),
    4: ("authenticated", "handle", "security_bits"),
    5: ("disconnected", "handle", "gap_reason"),
    6: ("terminate", "cause", "result"),
    7: ("stale_handle", "handle", "cause"),
    8: ("advertising_retry", "started", None),
    9: ("security_start_failed", "handle", "result"),
    10: ("disconnect_requested", "cause", "generation"),
}
TERMINATION_CAUSES = {
    1: "security_rejected",
    2: "receive_queue_full",
    3: "security_timeout",
    4: "reply_queue_full",
    5: "indication_failed",
    6: "indication_timeout",
    7: "indication_not_subscribed",
}


class InvalidDiagnosticReply(ValueError):
    pass


def integer(value, minimum=0, maximum=UINT32_MAX):
    if type(value) is not int or not minimum <= value <= maximum:
        raise InvalidDiagnosticReply
    return value


def data_of(reply):
    data = reply.get("data") if isinstance(reply, dict) else None
    if not isinstance(data, dict):
        raise InvalidDiagnosticReply
    return data


async def read_snapshot(call):
    header = data_of(await call("diag_info", {}))
    integer(header.get("schema"), 1, 1)
    boot_id = integer(header.get("boot_id"), 1)
    oldest = integer(header.get("oldest"), 1)
    until = integer(header.get("next"), oldest)
    capacity = integer(header.get("capacity"), 1, CAPACITY)
    if until - oldest > capacity:
        raise InvalidDiagnosticReply

    cursor = oldest
    skipped = 0
    rows = []
    while cursor < until:
        data = data_of(await call("diag_read", {"boot_id": boot_id, "from": cursor, "until": until}))
        next_cursor = integer(data.get("next"), cursor + 1, until)
        lost = integer(data.get("lost"), 0, next_cursor - cursor)
        events = data.get("events")
        if (not isinstance(events, list) or len(events) > PAGE_SIZE
                or len(events) != next_cursor - cursor - lost):
            raise InvalidDiagnosticReply
        for sequence, event in enumerate(events, cursor + lost):
            if not isinstance(event, list) or len(event) != 5:
                raise InvalidDiagnosticReply
            seq, ms, code, a, b = event
            integer(seq, sequence, sequence)
            integer(ms)
            integer(code, 1, UINT32_MAX)
            integer(a, INT32_MIN, INT32_MAX)
            integer(b, INT32_MIN, INT32_MAX)
            name, field_a, field_b = EVENTS.get(code, ("unknown", None, None))
            row = {"seq": seq, "uptime_ms": ms, "code": code, "event": name, "a": a, "b": b}
            if field_a:
                row[field_a] = a
            if field_b:
                row[field_b] = b
            if row.get("cause") in TERMINATION_CAUSES:
                row["cause_name"] = TERMINATION_CAUSES[row["cause"]]
            rows.append(row)
        skipped += lost
        cursor = next_cursor

    return {
        "schema": 1,
        "boot_id": boot_id,
        "capacity": capacity,
        "oldest": oldest,
        "next": until,
        "overwritten_before_read": oldest - 1,
        "overwritten_during_read": skipped,
        "events": rows,
    }


def serialize(snapshot, firmware, client_version):
    payload = {
        **snapshot,
        "firmware": firmware,
        "client": client_version,
        "exported_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
