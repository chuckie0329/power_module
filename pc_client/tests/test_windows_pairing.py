import asyncio
from enum import IntEnum
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pc_client.connection import BleLink, Client, DeviceError, PairingPinRequired, pair_windows_device
from pc_client.protocol import RESPONSE, STATE


class Address(IntEnum):
    PUBLIC = 0
    RANDOM = 1


class Protection(IntEnum):
    ENCRYPTION = 1
    ENCRYPTION_AND_AUTHENTICATION = 2


class PairKind(IntEnum):
    PROVIDE_PIN = 4
    CONFIRM_ONLY = 1


class Result(IntEnum):
    PAIRED = 0
    ALREADY_PAIRED = 3
    PAIRING_CANCELED = 9
    FAILED = 19
    AUTHENTICATION_FAILURE = 18
    REQUIRED_HANDLER_NOT_REGISTERED = 20
    REMOTE_DEVICE_HAS_ASSOCIATION = 21


class PairOperation:
    def __init__(self, result=None, delay=0):
        self.result, self.delay, self.cancelled = result, delay, False

    def __await__(self):
        async def finish():
            await asyncio.sleep(self.delay)
            return self.result
        return finish().__await__()

    def cancel(self):
        self.cancelled = True


class PairingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.selected = SimpleNamespace(details=SimpleNamespace(
            adv=SimpleNamespace(bluetooth_address=0x010203040506, bluetooth_address_type=Address.RANDOM)))
        self.device = SimpleNamespace(device_information=SimpleNamespace(id="selected-device"), close=Mock())
        self.pairing = SimpleNamespace(is_paired=False, pair_with_protection_level_async=Mock())
        self.enum = SimpleNamespace(create_from_id_async=AsyncMock(return_value=SimpleNamespace(pairing=self.pairing)))
        self.factory = SimpleNamespace(from_bluetooth_address_with_bluetooth_address_type_async=AsyncMock(
            return_value=self.device), from_bluetooth_address_async=AsyncMock(return_value=self.device))
        modules = {
            "winrt.windows.devices.bluetooth": SimpleNamespace(BluetoothAddressType=Address, BluetoothLEDevice=self.factory),
            "winrt.windows.devices.enumeration": SimpleNamespace(DeviceInformation=self.enum,
                DevicePairingProtectionLevel=Protection, DevicePairingResultStatus=Result, DevicePairingKinds=PairKind),
        }
        mocked_winrt = patch.dict(sys.modules, modules)
        mocked_winrt.start()
        self.addCleanup(mocked_winrt.stop)

    async def test_known_pairing_never_requests_pairing(self):
        self.pairing.is_paired = True
        await pair_windows_device(self.selected)
        self.factory.from_bluetooth_address_with_bluetooth_address_type_async.assert_awaited_once_with(
            0x010203040506, Address.RANDOM)
        self.pairing.pair_with_protection_level_async.assert_not_called()
        self.device.close.assert_called_once()

    async def test_unpaired_waits_for_explicit_authenticated_result(self):
        operation = PairOperation(SimpleNamespace(status=Result.PAIRED,
            protection_level_used=Protection.ENCRYPTION_AND_AUTHENTICATION), delay=.02)
        self.pairing.pair_with_protection_level_async.return_value = operation
        task = asyncio.create_task(pair_windows_device(self.selected))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        await task
        self.pairing.pair_with_protection_level_async.assert_called_once_with(Protection.ENCRYPTION_AND_AUTHENTICATION)
        self.device.close.assert_called_once()

    async def test_cancellation_refusal_and_downgraded_result(self):
        for status, error in ((Result.PAIRING_CANCELED, "BLE_PAIR_CANCELLED"),
                              (Result.FAILED, "BLE_PAIR_FAILED"), (Result.PAIRED, "BLE_PAIR_FAILED")):
            with self.subTest(status=status):
                self.pairing.pair_with_protection_level_async.return_value = PairOperation(
                    SimpleNamespace(status=status, protection_level_used=Protection.ENCRYPTION))
                with self.assertRaisesRegex(DeviceError, error):
                    await pair_windows_device(self.selected)

    async def test_winrt_specific_status_and_protection_are_preserved(self):
        for status in (Result.AUTHENTICATION_FAILURE, Result.REQUIRED_HANDLER_NOT_REGISTERED,
                       Result.REMOTE_DEVICE_HAS_ASSOCIATION):
            self.pairing.pair_with_protection_level_async.return_value = PairOperation(
                SimpleNamespace(status=status, protection_level_used=Protection.ENCRYPTION))
            with self.assertRaises(DeviceError) as exc:
                await pair_windows_device(self.selected)
            prefix = "BLE_PAIR_PIN_REQUIRED" if status is Result.REQUIRED_HANDLER_NOT_REGISTERED else "BLE_PAIR_FAILED"
            self.assertEqual(str(exc.exception), f"{prefix}:{status.name}/ENCRYPTION")

    async def test_hresult_stage_only_does_not_expose_windows_exception_text(self):
        failure = OSError(-2147024891, "private Windows text or PIN")
        self.enum.create_from_id_async.side_effect = failure
        with self.assertRaises(DeviceError) as exc:
            await pair_windows_device(self.selected)
        self.assertEqual(str(exc.exception), "BLE_PAIR_FAILED:INFORMATION/0x80070005")
        self.assertNotIn("PIN", str(exc.exception))
        self.device.close.assert_called_once()

    async def test_custom_accepts_only_provide_pin_and_keeps_strict_protection(self):
        custom = self.pairing.custom = Mock()
        token = object()
        custom.add_pairing_requested.return_value = token
        confirm, provide = Mock(pairing_kind=PairKind.CONFIRM_ONLY), Mock(pairing_kind=PairKind.PROVIDE_PIN)
        def pair(kind, level):
            self.assertEqual((kind, level), (PairKind.PROVIDE_PIN, Protection.ENCRYPTION_AND_AUTHENTICATION))
            handler = custom.add_pairing_requested.call_args.args[0]
            handler(None, confirm)
            handler(None, provide)
            return PairOperation(SimpleNamespace(status=Result.PAIRED, protection_level_used=level))
        custom.pair_with_protection_level_async.side_effect = pair
        await pair_windows_device(self.selected, pin="654321")
        confirm.accept_with_pin.assert_not_called()
        provide.accept_with_pin.assert_called_once_with("654321")
        custom.remove_pairing_requested.assert_called_once_with(token)
        self.pairing.pair_with_protection_level_async.assert_not_called()

    async def test_timeout_cancels_the_windows_operation(self):
        operation = PairOperation(delay=60)
        real_timeout = asyncio.timeout
        self.pairing.pair_with_protection_level_async.return_value = operation
        with patch("pc_client.connection.asyncio.timeout", side_effect=lambda _seconds: real_timeout(.03)) as timeout:
            with self.assertRaisesRegex(DeviceError, "BLE_PAIR_TIMEOUT"):
                await pair_windows_device(self.selected)
        timeout.assert_called_once_with(50)
        self.assertTrue(operation.cancelled)
        self.device.close.assert_called_once()


class LinkPairingTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_before_gatt_and_background_does_not_pair(self):
        for manual in (True, False):
            with self.subTest(manual=manual):
                events = []
                link = BleLink("selected")
                link.allow_pairing = manual
                ble = SimpleNamespace(connect=AsyncMock(side_effect=lambda: events.append("connect")),
                    start_notify=AsyncMock(side_effect=lambda uuid, callback: events.append(uuid)))
                def pair(target):
                    self.assertEqual(target, "selected")
                    events.append("pair")
                with patch("pc_client.connection.sys.platform", "win32"), patch(
                    "pc_client.connection.pair_windows_device", new=AsyncMock(side_effect=pair)
                ), patch("pc_client.connection.BleakClient", return_value=ble) as factory:
                    await link.open(Mock(), Mock())
                self.assertEqual(events, (["pair"] if manual else []) + ["connect", RESPONSE, STATE])
                self.assertNotIn("pair", factory.call_args.kwargs)  # Do not enter Bleak ConfirmOnly.

    async def test_failed_pairing_never_starts_gatt(self):
        link = BleLink("selected")
        link.allow_pairing = True
        with patch("pc_client.connection.sys.platform", "win32"), patch(
            "pc_client.connection.pair_windows_device", new=AsyncMock(side_effect=DeviceError("BLE_PAIR_CANCELLED"))
        ), patch("pc_client.connection.BleakClient") as factory:
            with self.assertRaisesRegex(DeviceError, "BLE_PAIR_CANCELLED"):
                await link.open(Mock(), Mock())
        factory.assert_not_called()

    async def test_custom_pin_requires_local_challenge_for_exact_same_selected_object(self):
        client = Client(Mock())
        original, other = object(), object()
        link = SimpleNamespace(allow_pairing=False)
        detail = "BLE_PAIR_PIN_REQUIRED:REQUIRED_HANDLER_NOT_REGISTERED/ENCRYPTION"
        with patch("pc_client.connection.BleLink", return_value=link):
            with patch.object(client, "identify", AsyncMock(side_effect=DeviceError(detail))):
                with self.assertRaisesRegex(DeviceError, "LINK_ERROR"):
                    await client.connect("ble", original, allow_pairing=True)
            with self.assertRaisesRegex(DeviceError, "BLE_PAIR_FAILED"):
                await client.connect("ble", original, allow_pairing=True, pairing_pin="654321")
            challenge = PairingPinRequired("BLE_PAIR_PIN_REQUIRED", "REQUIRED_HANDLER_NOT_REGISTERED/ENCRYPTION")
            with patch.object(client, "identify", AsyncMock(side_effect=challenge)):
                with self.assertRaises(PairingPinRequired):
                    await client.connect("ble", original, allow_pairing=True)
            with self.assertRaisesRegex(DeviceError, "BLE_PAIR_FAILED"):
                await client.connect("ble", other, allow_pairing=True, pairing_pin="654321")
            with self.assertRaisesRegex(DeviceError, "BLE_PAIR_FAILED"):
                await client.connect("ble", original, allow_pairing=True, pairing_pin="654321")

    async def test_expected_identity_restore_and_usb_cannot_opt_in(self):
        for mode, expected, want_pair in (("ble", "same-device", False), ("ble", None, True),
                                           ("usb", None, False)):
            with self.subTest(mode=mode, expected=expected):
                c = Client(Mock())
                link = SimpleNamespace(allow_pairing=False)
                with patch.object(c, "identify", AsyncMock(side_effect=DeviceError("LINK_ERROR"))), patch(
                    "pc_client.connection.BleLink" if mode == "ble" else "pc_client.connection.SerialLink",
                    return_value=link
                ):
                    with self.assertRaisesRegex(DeviceError, "LINK_ERROR"):
                        await c.connect(mode, "selected", expected_device=expected, allow_pairing=True)
                self.assertEqual(link.allow_pairing, want_pair)


class ManualWindowTests(unittest.TestCase):
    def test_pairing_message_distinguishes_status_and_masks_invalid_detail(self):
        from pc_client.app import error_text
        text = error_text("BLE_PAIR_FAILED:REQUIRED_HANDLER_NOT_REGISTERED/ENCRYPTION")
        self.assertIn("REQUIRED_HANDLER_NOT_REGISTERED", text)
        self.assertIn("未能处理需要的配对操作", text)
        self.assertNotIn("私人值", error_text("BLE_PAIR_FAILED:私人值"))

    def test_pin_dialog_requires_opt_in_and_rechecks_selected_device(self):
        from pc_client.app import Window
        target = object()
        connect = Mock()
        dropdown = Mock()
        dropdown.currentData.return_value = target
        window = SimpleNamespace(kind="ble", connect_target=target, target=dropdown,
            info={}, jobs=set(), pc_handoff=False, exit_requested=False, launch=Mock(),
            announce=Mock(), worker=SimpleNamespace(client=SimpleNamespace(connect=connect)))
        for returned in (("", False), ("badpin", True)):
            with patch("pc_client.app.QInputDialog.getText", return_value=returned):
                Window.offer_explicit_ble_pin(window)
        connect.assert_not_called()
        dropdown.currentData.return_value = object()
        with patch("pc_client.app.QInputDialog.getText", return_value=("654321", True)):
            Window.offer_explicit_ble_pin(window)
        connect.assert_not_called()
        dropdown.currentData.return_value = target
        with patch("pc_client.app.QInputDialog.getText", return_value=("654321", True)):
            Window.offer_explicit_ble_pin(window)
        connect.assert_called_once_with("ble", target, allow_pairing=True, pairing_pin="654321")

    def test_button_requests_pairing_only_for_ble(self):
        from pc_client.app import Window
        for mode in ("usb", "ble"):
            connect = Mock()
            w = SimpleNamespace(pc_handoff=False, info={}, target=Mock(), kind=mode,
                                announce=Mock(), launch=Mock(),
                                worker=SimpleNamespace(client=SimpleNamespace(connect=connect)))
            w.target.currentData.return_value = "selected"
            Window.toggle_connection(w)
            if mode == "ble":
                connect.assert_called_once_with(mode, "selected", allow_pairing=True)
            else:
                connect.assert_called_once_with(mode, "selected")
