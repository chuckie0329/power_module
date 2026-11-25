import asyncio
from base64 import b64encode
import re
import secrets
import sys
import struct
import threading
import time

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError
from PySide6.QtCore import QObject, Signal
import serial
from serial.tools import list_ports

from pc_client.protocol import (
    COMMAND, HEARTBEAT, RESPONSE, SERVICE, STATE, VERSION,
    Fragments, Lines, decode, encode, fragments,
)


def usb_ports():
    ports = sorted(list_ports.comports(), key=lambda port: (port.vid != 0x1A86, port.device))
    return [
        (f"{port.device}  ·  {port.description.removesuffix(f' ({port.device})')}", port.device)
        for port in ports
    ]


def firmware_version(info):
    try:
        return tuple(map(int, info.get("firmware", "").split(".")))
    except (AttributeError, ValueError):
        return ()


def retains_outputs(info):
    return firmware_version(info) >= (1, 2, 1)


class DeviceError(Exception):
    def __init__(self, code, detail=None):
        super().__init__(f"{code}:{detail}" if detail else code)
        self.code = code


class PairingPinRequired(DeviceError):
    pass


def _winrt_enum(value):
    name = getattr(value, "name", None)
    if isinstance(name, str) and re.fullmatch(r"[A-Z][A-Z_0-9]{0,47}", name):
        return name
    return "UNKNOWN"


class SerialLink:
    name = "usb"
    timeout = 3.0

    def __init__(self, port, baudrate=921600):
        self.port = serial.Serial(baudrate=baudrate, timeout=0, write_timeout=0.5)
        self.port.port = port
        self.port.dtr = False
        self.port.rts = False
        self.reader = None

    async def open(self, received, disconnected):
        self.port.open()
        # End any incomplete line left on the device before the handshake.
        await self.send(b"\n")
        self.reader = asyncio.create_task(self.read(received, disconnected))
        await asyncio.sleep(0.15)

    async def read(self, received, disconnected):
        lines = Lines()
        try:
            while self.port.is_open:
                chunk = self.port.read(2048)
                for line in lines.feed(chunk):
                    received(line)
                await asyncio.sleep(0.02)
        except (OSError, serial.SerialException):
            disconnected("串口已断开")

    async def send(self, line):
        if self.port.write(line) != len(line):
            raise DeviceError("LINK_ERROR")

    async def close(self):
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        self.port.close()


async def pair_windows_device(target, *, pin=None):
    # Bleak 2.1.1's pair=True only implements the WinRT ConfirmOnly
    # ceremony. Basic pairing lets Windows handle the firmware's PIN.
    from winrt.windows.devices.bluetooth import BluetoothAddressType, BluetoothLEDevice
    from winrt.windows.devices.enumeration import (
        DeviceInformation, DevicePairingProtectionLevel, DevicePairingResultStatus,
    )
    if pin is not None and (not isinstance(pin, str) or not re.fullmatch(r"[0-9]{6}", pin)):
        raise DeviceError("BLE_PAIR_BAD_PIN")

    details = getattr(target, "details", None)
    advertisement = getattr(details, "adv", None) or getattr(details, "scan", None)
    address = getattr(advertisement, "bluetooth_address", None)
    if address is None:
        raise DeviceError("BLE_RESCAN")
    address_type = getattr(advertisement, "bluetooth_address_type", None)
    device = operation = custom = token = None
    stage = "DEVICE"
    try:
        async with asyncio.timeout(50):
            if address_type in (BluetoothAddressType.PUBLIC, BluetoothAddressType.RANDOM):
                device = await BluetoothLEDevice.from_bluetooth_address_with_bluetooth_address_type_async(
                    address, address_type)
            else:
                device = await BluetoothLEDevice.from_bluetooth_address_async(address)
            if device is None:
                raise DeviceError("BLE_RESCAN")
            stage = "INFORMATION"
            info = await DeviceInformation.create_from_id_async(device.device_information.id)
            pairing = info.pairing
            if pairing.is_paired:
                return
            level = DevicePairingProtectionLevel.ENCRYPTION_AND_AUTHENTICATION
            if pin is None:
                stage = "PAIR"
                operation = pairing.pair_with_protection_level_async(level)
            else:
                from winrt.windows.devices.enumeration import DevicePairingKinds
                stage = "CUSTOM"
                custom = pairing.custom

                def provide_pin(_sender, args):
                    if args.pairing_kind == DevicePairingKinds.PROVIDE_PIN:
                        args.accept_with_pin(pin)

                token = custom.add_pairing_requested(provide_pin)
                operation = custom.pair_with_protection_level_async(DevicePairingKinds.PROVIDE_PIN, level)
            result = await operation
            status = result.status
            if status == DevicePairingResultStatus.ALREADY_PAIRED:
                return
            if status == DevicePairingResultStatus.PAIRED and result.protection_level_used == level:
                return
            detail = f"{_winrt_enum(status)}/{_winrt_enum(result.protection_level_used)}"
            if status == DevicePairingResultStatus.PAIRING_CANCELED:
                raise DeviceError("BLE_PAIR_CANCELLED", detail)
            if pin is None and _winrt_enum(status) == "REQUIRED_HANDLER_NOT_REGISTERED":
                raise PairingPinRequired("BLE_PAIR_PIN_REQUIRED", detail)
            raise DeviceError("BLE_PAIR_FAILED", detail)
    except (TimeoutError, asyncio.CancelledError) as error:
        if operation is not None:
            try:
                operation.cancel()
            except OSError:
                pass  # The Windows operation may already have completed.
        if isinstance(error, TimeoutError):
            raise DeviceError("BLE_PAIR_TIMEOUT", stage) from error
        raise
    except OSError as error:
        hresult = getattr(error, "winerror", None)
        if type(hresult) is not int and error.args and type(error.args[0]) is int and error.args[0] < 0:
            hresult = error.args[0]
        detail = f"{stage}/0x{hresult & 0xffffffff:08X}" if type(hresult) is int else stage
        raise DeviceError("BLE_PAIR_FAILED", detail) from error
    finally:
        if token is not None:
            try:
                custom.remove_pairing_requested(token)
            except OSError:
                pass
        if device is not None:
            try:
                device.close()
            except OSError:
                pass  # Preserve the pairing result if WinRT already closed its handle.


class BleLink:
    name = "ble"
    timeout = 12.0

    def __init__(self, target):
        self.target = target
        self.client = None
        self.lock = asyncio.Lock()
        self.number = 0
        self.allow_pairing = False
        self.pairing_pin = None

    @staticmethod
    async def scan():
        found = await BleakScanner.discover(timeout=4, return_adv=True, service_uuids=[SERVICE])
        return [
            (f"{adv.local_name or device.name or 'SX150C'}  ·  {device.address}", device)
            for device, adv in found.values()
        ]

    async def open(self, received, disconnected):
        if self.allow_pairing and sys.platform == "win32":
            pin, self.pairing_pin = self.pairing_pin, None
            if pin is None:
                await pair_windows_device(self.target)
            else:
                await pair_windows_device(self.target, pin=pin)
        # WinRT's UUID-filtered discovery can fail with ERROR_BAD_COMMAND.
        self.client = BleakClient(
            self.target, timeout=15,
            disconnected_callback=lambda _client: disconnected("蓝牙已断开"),
            winrt={"use_cached_services": False},
        )
        await self.client.connect()
        for characteristic in (RESPONSE, STATE):
            assembler = Fragments()

            def on_notify(_sender, packet, reader=assembler):
                if line := reader.feed(packet):
                    received(line)

            await self.client.start_notify(characteristic, on_notify)
        await asyncio.sleep(0.25)

    async def send(self, line):
        async with self.lock:
            self.number = (self.number + 1) & 0xFFFF
            for fragment in fragments(line, self.number, self.client.mtu_size):
                await self.client.write_gatt_char(COMMAND, fragment, response=True)

    async def beat(self, client_id):
        await self.client.write_gatt_char(HEARTBEAT, struct.pack("<I", client_id), response=True)

    async def close(self):
        if self.client:
            await self.client.disconnect()


class Client:
    def __init__(self, worker):
        self.worker = worker
        self.link = None
        self.ready = False
        self.info = {}
        self.latest = {}
        self.client_id = 0
        self.sequence = secrets.randbelow(1 << 28)
        self.pending = {}
        self.heartbeat = None
        self.monitor = None
        self.time_received = 0.0
        self.closing = False
        self.updating = False
        self.pin_pairing_target = None

    async def connect(self, mode, target, expected_device=None, *, allow_pairing=False, pairing_pin=None):
        if self.link:
            raise DeviceError("BUSY")
        allowed_pin_target, self.pin_pairing_target = self.pin_pairing_target, None
        if pairing_pin is not None and (mode != "ble" or not allow_pairing or expected_device is not None
                                        or target is not allowed_pin_target):
            raise DeviceError("BLE_PAIR_FAILED")
        self.latest = {}
        self.info = {}
        self.client_id = secrets.randbits(32) or 1
        link = SerialLink(target) if mode == "usb" else BleLink(target)
        if mode == "ble" and allow_pairing and expected_device is None:
            link.allow_pairing = True
            link.pairing_pin = pairing_pin
        try:
            try:
                info = await self.identify(link)
            except (DeviceError, serial.SerialException) as error:
                if mode != "usb" or (isinstance(error, DeviceError) and error.code != "REPLY_TIMEOUT"):
                    raise
                await link.close()
                self.latest = {}
                link = SerialLink(target, baudrate=115200)
                info = await self.identify(link)
            if info.get("v") != VERSION or not info.get("device_id"):
                raise DeviceError("BAD_VERSION")
            if self.link is not link:
                raise DeviceError("LINK_ERROR")
            if expected_device is not None:
                if info["device_id"] != expected_device:
                    raise DeviceError("OTA_DEVICE")
                reply = await self.call("get_state")
                applied = reply.get("applied")
                if (self.link is not link or not isinstance(applied, dict)
                        or any(type(applied.get(k)) is not bool for k in ("enabled", "relay1", "relay2"))):
                    raise DeviceError("LINK_ERROR")
            self.info = info
            self.ready = True
            self.monitor = asyncio.create_task(self.monitor_link(link))
            if info.get("direct_control") and not retains_outputs(info):
                self.heartbeat = asyncio.create_task(self.keep_alive(link))
            self.worker.connected.emit(info, self.latest)
        except asyncio.CancelledError:
            await self.close(graceful=False)
            raise
        except Exception as error:
            await self.close(graceful=False)
            if isinstance(error, PairingPinRequired) and mode == "ble" and allow_pairing:
                self.pin_pairing_target = target
            elif isinstance(error, DeviceError) and str(error).startswith("BLE_PAIR_PIN_REQUIRED:"):
                raise DeviceError("LINK_ERROR") from error
            if mode == "ble" and isinstance(error, BleakError) and (
                "Protocol Error 0x05:" in str(error) or "Protocol Error 0x0F:" in str(error)
            ):
                raise DeviceError("BLE_AUTH_REQUIRED") from error
            raise

    async def identify(self, link):
        self.link = link
        await link.open(
            lambda line: self.receive(line) if self.link is link else None,
            lambda reason: self.lost(link, reason),
        )
        return (await self.call("get_info"))["data"]

    def receive(self, line):
        message = decode(line)
        if message is None or message.get("type") not in ("reply", "state"):
            return
        self.time_received = time.monotonic()
        uptime = message.get("uptime_ms")
        if isinstance(uptime, int) and isinstance(message.get("applied"), dict):
            previous = self.latest.get("uptime_ms")
            if (previous is not None and 1000 < previous - uptime < 0x80000000
                    and self.link.name == "usb" and message["type"] == "state"):
                self.info = {**self.info, "sx_model_raw": None}
                previous = None
                self.worker.notice.emit("设备已重新启动", "")
            # An older response can arrive after a newer snapshot over BLE.
            if previous is None or not ((uptime - previous) & 0x80000000):
                self.latest = {
                    key: value for key, value in message.items()
                    if key in ("state_rev", "uptime_ms", "mode", "applied", "measured", "protection", "fault")
                }
                if self.ready:
                    self.worker.state.emit(self.latest, self.time_received, message["type"] == "state")
        waiter = self.pending.get(message.get("id"))
        if message["type"] == "reply" and waiter and not waiter.done():
            waiter.set_result(message)

    def request(self, op, args=None, expected=None):
        self.sequence += 1
        request = {
            "v": VERSION, "id": self.sequence, "client_id": self.client_id, "op": op, "args": args or {},
        }
        if expected is not None:
            request["expected_rev"] = expected
        return request

    async def call(self, op, args=None, expected=None):
        if not self.link:
            raise DeviceError("LINK_ERROR")
        request = self.request(op, args, expected)
        link = self.link
        waiter = asyncio.get_running_loop().create_future()
        self.pending[request["id"]] = waiter
        try:
            async with asyncio.timeout(link.timeout):
                await link.send(encode(request))
                reply = await waiter
        except TimeoutError as error:
            raise DeviceError("REPLY_TIMEOUT") from error
        finally:
            self.pending.pop(request["id"], None)
            if not waiter.done():
                waiter.cancel()
            elif not waiter.cancelled():
                waiter.exception()
        if reply.get("ok") is not True:
            raise DeviceError(reply.get("fault", {}).get("code") or "COMMAND_FAILED")
        return reply

    async def keep_alive(self, link):
        try:
            while self.link is link:
                await asyncio.sleep(0.85)
                if self.updating or self.link is not link:
                    continue
                if link.name == "ble":
                    await asyncio.wait_for(link.beat(self.client_id), timeout=2)
                else:
                    await link.send(encode(self.request("heartbeat")))
        except Exception:
            self.lost(link, "设备心跳已中断")

    async def change(self, operation, args, revision):
        if not self.info.get("direct_control"):
            raise DeviceError("FIRMWARE_UPDATE")
        return await self.call(operation, args, revision)

    async def update_firmware(self, image, cancelled):
        if not self.ready or self.updating:
            raise DeviceError("BUSY")
        link = self.link
        mode = link.name
        target = link.port.port if mode == "usb" else link.target
        device_id = self.info["device_id"]
        size = len(image.data)
        self.updating = True
        started = committed = False
        try:
            status = (await self.call("ota_info"))["data"]
            slot = status.get("next_slot")
            if not slot or not 0 < size <= status.get("max_size", 0):
                raise DeviceError("OTA_NO_SLOT")
            if status.get("busy") or status.get("pending_verify"):
                raise DeviceError("OTA_BUSY")
            if not status.get("outputs_off"):
                raise DeviceError("OTA_OUTPUT")
            if cancelled():
                raise DeviceError("OTA_CANCELLED")
            chunk_size = status.get("max_chunk", 0)
            if not 324 <= chunk_size <= 600:
                raise DeviceError("OTA_PROTOCOL")
            started = True
            await self.call("ota_begin", {"size": size, "sha256": image.digest})
            self.worker.update_progress.emit(0, size, "transfer")
            for offset in range(0, size, chunk_size):
                if cancelled():
                    raise DeviceError("OTA_CANCELLED")
                chunk = image.data[offset:offset + chunk_size]
                args = {"offset": offset, "data": b64encode(chunk).decode("ascii")}
                try:
                    reply = await self.call("ota_write", args)
                except DeviceError as error:
                    if error.code != "REPLY_TIMEOUT" or self.link is not link or cancelled():
                        raise
                    reply = await self.call("ota_write", args)
                written = offset + len(chunk)
                if reply.get("data", {}).get("offset") != written:
                    raise DeviceError("OTA_PROTOCOL")
                if written * 100 // size != offset * 100 // size:
                    self.worker.update_progress.emit(written, size, "transfer")
            if cancelled():
                raise DeviceError("OTA_CANCELLED")
            self.worker.update_progress.emit(size, size, "restart")
            try:
                await self.call("ota_finish")
            except DeviceError as error:
                if error.code not in ("REPLY_TIMEOUT", "LINK_ERROR"):
                    raise
            except (OSError, BleakError):
                pass
            committed = True
            return await self.confirm_update(mode, target, device_id, image.version, slot)
        finally:
            if started and not committed and self.link is link and not self.closing:
                try:
                    await self.call("ota_abort")
                except (DeviceError, OSError, BleakError):
                    pass
            self.updating = False

    async def confirm_update(self, mode, target, device_id, version, slot):
        await self.close(graceful=False)
        await asyncio.sleep(2)
        deadline = time.monotonic() + 80
        while time.monotonic() < deadline:
            if self.closing:
                await asyncio.sleep(0.25)
                continue
            if not self.link:
                try:
                    await self.connect(mode, target)
                except (DeviceError, OSError, BleakError, TimeoutError):
                    await asyncio.sleep(1)
                    continue
            if self.info.get("device_id") != device_id:
                await self.close(graceful=False)
                raise DeviceError("OTA_DEVICE")
            try:
                status = (await self.call("ota_info"))["data"]
                if status.get("slot") == slot and self.info.get("firmware") == version:
                    await self.call("ota_confirm", {"slot": slot})
                    return {"firmware": version, "slot": slot}
            except DeviceError as error:
                if error.code in ("BUSY", "OTA_OUTPUT"):
                    await asyncio.sleep(0.5)
                    continue
            except (OSError, BleakError):
                pass
            await self.close(graceful=False)
            await asyncio.sleep(1)
        raise DeviceError("OTA_BOOT")

    async def monitor_link(self, link):
        while self.ready and self.link is link:
            await asyncio.sleep(1)
            if self.updating or self.link is not link:
                continue
            if time.monotonic() - self.time_received > 6:
                try:
                    await self.call("get_state")
                except Exception:
                    self.lost(link, "设备状态读取超时")
                    return
            if self.info.get("sx_model_raw") is None and self.latest.get("measured", {}).get("fresh"):
                try:
                    self.info = (await self.call("get_info"))["data"]
                    self.worker.info_updated.emit(self.info)
                except Exception:
                    pass

    def lost(self, link, reason):
        if self.link is link and not self.closing:
            asyncio.create_task(self.close(graceful=False, reason=reason))

    async def close(self, graceful=True, reason=""):
        if self.closing or not self.link:
            return
        self.closing = True
        self.ready = False
        link = self.link
        if self.monitor:
            self.monitor.cancel()
            self.monitor = None
        try:
            if graceful and self.info.get("direct_control"):
                try:
                    await self.call("disconnect")
                except Exception:
                    reason = ("连接已关闭，设备未应答断开确认" if retains_outputs(self.info)
                              else "连接已关闭，ESP32 将在心跳超时后关断本次开启的输出")
        finally:
            self.link = None
            if self.heartbeat:
                self.heartbeat.cancel()
                self.heartbeat = None
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(DeviceError("LINK_ERROR"))
            try:
                await asyncio.wait_for(link.close(), timeout=5)
            except Exception:
                if not reason:
                    reason = "设备连接已失效"
            self.closing = False
            self.worker.disconnected.emit(reason)
        return not reason


class LinkWorker(QObject):
    connected = Signal(object, object)
    info_updated = Signal(object)
    disconnected = Signal(str)
    state = Signal(object, float, bool)
    notice = Signal(str, str)
    update_progress = Signal(int, int, str)
    finished = Signal(str, object, str)

    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.client = Client(self)
        self.thread = threading.Thread(target=self.run, name="PowerModule link", daemon=True)
        self.thread.start()

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        tasks = asyncio.all_tasks(self.loop)
        for task in tasks:
            task.cancel()
        if tasks:
            self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        self.loop.close()

    def submit(self, action, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)

        def finished(result):
            try:
                self.finished.emit(action, result.result(), "")
            except Exception as error:
                self.finished.emit(action, None, str(error) or "LINK_ERROR")

        future.add_done_callback(finished)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
