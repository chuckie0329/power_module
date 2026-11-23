"""Send protocol v1 requests through the assembled unit's CH340E."""

import argparse
import json
import secrets
import time
import zlib

import serial


def canonical(message):
    return json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def crc32(message):
    return f"{zlib.crc32(canonical(message)):08x}"


class Client:
    def __init__(self, port):
        self.port = port
        self.request_id = 0
        self.client_id = secrets.randbits(32) or 1
        self.keep_alive = False
        self.next_beat = 0.0
        self.incoming = bytearray()

    def send(self, op, args=None, **fields):
        self.request_id += 1
        message = {
            "v": 1, "id": self.request_id, "client_id": self.client_id, "op": op, "args": args or {}, **fields,
        }
        message["crc32"] = crc32(message)
        self.port.write(canonical(message) + b"\n")
        return self.request_id

    def read(self):
        self.incoming.extend(self.port.read(min(self.port.in_waiting or 1, 2048)))
        while b"\n" in self.incoming:
            raw, _, self.incoming = self.incoming.partition(b"\n")
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    continue
                received = message.pop("crc32", None)
                if received == crc32(message):
                    yield message
            except (ValueError, UnicodeError):
                continue
        if len(self.incoming) > 1024:
            self.incoming.clear()

    def call(self, op, args=None, **fields):
        request_id = self.send(op, args, **fields)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.keep_alive and time.monotonic() >= self.next_beat:
                self.send("heartbeat")
                self.next_beat = time.monotonic() + 1
            for message in self.read():
                if message.get("type") == "reply" and message.get("id") == request_id:
                    return message
        raise TimeoutError(f"{op}: no matching response")

def show(reply):
    print(json.dumps(reply, ensure_ascii=False, indent=2))
    if not reply["ok"]:
        raise RuntimeError(reply["fault"]["code"])
    return reply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="COM port of the CH340E")
    parser.add_argument("--baud", type=int, choices=(921600, 115200), default=921600,
                        help="select 115200 for firmware through v1.1.6")
    parser.add_argument("--set-off", nargs=2, type=int, metavar=("MV", "MA"),
                        help="write and read back setpoints with SX output off")
    parser.add_argument("--relay-test", type=int, choices=(1, 2),
                        help="energize the selected relay for 0.4 seconds")
    options = parser.parse_args()
    with serial.Serial(options.port, options.baud, timeout=0.1, write_timeout=1) as port:
        port.reset_input_buffer()
        client = Client(port)
        info = show(client.call("get_info"))["data"]
        show(client.call("get_state"))
        if options.set_off is None and options.relay_test is None:
            return
        if not info.get("direct_control"):
            raise RuntimeError("Update ESP32 firmware to v1.1.0 for direct control")
        client.keep_alive = True
        try:
            if options.set_off is not None:
                voltage, current = options.set_off
                args = {"voltage_mV": voltage, "current_mA": current, "enabled": False}
                state = show(client.call("get_state"))
                reply = client.call("set_output", args, expected_rev=state["state_rev"])
                if reply["fault"]["code"] == "REV_CONFLICT":
                    state = show(client.call("get_state"))
                    reply = client.call("set_output", args, expected_rev=state["state_rev"])
                show(reply)
            if options.relay_test is not None:
                args = {"channel": options.relay_test, "enabled": True}
                try:
                    show(client.call("set_relay", args))
                    time.sleep(0.4)
                finally:
                    args["enabled"] = False
                    show(client.call("set_relay", args))
        finally:
            show(client.call("disconnect"))


if __name__ == "__main__":
    try:
        main()
    except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error
