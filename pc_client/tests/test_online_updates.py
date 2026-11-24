import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pc_client import online_updates as u
from pc_client.version import PC_VERSION


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.item = dict(version="1.0.1", target="windows-x64",
                         url=u.release_url("pc", "1.0.1"), sha256="a" * 64, size=180)
        self.doc = dict(schema=1, kind="pc", stable=self.item)

    def test_independent_feeds(self):
        self.assertEqual(u.parse_catalog(self.doc, "pc").version, "1.0.1")
        self.assertIsNone(u.parse_catalog(dict(self.doc, stable=None), "pc"))
        with self.assertRaises(ValueError):
            u.parse_catalog(self.doc, "firmware")
        self.assertGreater(u.version_key("1.10.0"), u.version_key("1.2.9"))
        self.assertEqual(u.version_key(PC_VERSION), (1, 0, 0))

    def test_rejects_unpinned_wrong_kind_and_type(self):
        for changes in (
            {"url": "https://github.com/chuckie0329/power_module/releases/latest/download/PowerModule-PC.exe"},
            {"url": self.item["url"].replace("github.com", "user@github.com")},
            {"url": self.item["url"].replace("PowerModule-PC.exe", "PowerModule-ESP32.bin")},
            {"version": "1.0.1-beta"}, {"size": True}, {"size": u.LIMITS["pc"] + 1},
            {"target": "windows-x86"}, {"sha256": "f" * 63},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                u.parse_catalog(dict(self.doc, stable=dict(self.item, **changes)), "pc")

    def test_new_firmware_channel_and_versioned_filename(self):
        version = "1.2.2"
        fw = dict(version=version, target="PowerModule-ESP32", protocol=1,
                  url=u.release_url("firmware", version), sha256="a" * 64, size=500,
                  notes="Release note")
        doc = dict(schema=1, kind="firmware", stable=fw)
        parsed = u.parse_catalog(doc, "firmware")
        self.assertIn("/fw-v1.2.2/", parsed.url)
        self.assertEqual(parsed.notes, "Release note")
        with self.assertRaises(ValueError):
            u.parse_catalog(dict(doc, stable=dict(fw, url=fw["url"].replace("fw-v", "v"))), "firmware")
        self.assertEqual(u.parse_catalog(self.doc, "pc").filename, "PowerModule-PC-v1.0.1.exe")
        with self.assertRaises(ValueError):
            u.parse_catalog(dict(self.doc, stable=dict(self.item, notes="x" * 361)), "pc")

    def test_transport_restricts_hosts_and_caps_catalog(self):
        for url in (
            "http://release-assets.githubusercontent.com/file",
            "https://release-assets.githubusercontent.com.example.com/file",
            "https://release-assets.githubusercontent.com:8443/file",
            "https://any@release-assets.githubusercontent.com/file",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                u._Redirects(u.DOWNLOAD_HOSTS).redirect_request(None, None, 302, "", {}, url)
        self.assertTrue(u._allowed("https://release-assets.githubusercontent.com/file", u.DOWNLOAD_HOSTS))
        with patch.object(u, "_open", return_value=io.BytesIO(b" " * (u.MAX_CATALOG_BYTES + 1))):
            with self.assertRaisesRegex(ValueError, "过大"):
                u.check_catalog("pc")

    def test_released_firmware_catalog(self):
        folder = Path(__file__).resolve().parents[2]
        firmware = u.parse_catalog(json.loads((folder / "updates/firmware-stable.json").read_text(encoding="utf-8")), "firmware")
        self.assertEqual(firmware.kind, "firmware")
        self.assertGreaterEqual(u.version_key(firmware.version), (1, 2, 1))
        self.assertEqual(firmware.url, u.release_url("firmware", firmware.version))


class DownloadTests(unittest.TestCase):
    def check(self, kind, payload, *, expected=None, mismatch=False, firmware_version=None):
        expected = expected if expected is not None else payload
        checksum = hashlib.sha256(expected).hexdigest()
        update = u.Update(kind, "1.0.1", u.release_url(kind, "1.0.1"), checksum, len(expected))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "saved")
            with patch.object(u, "_open", return_value=io.BytesIO(payload)), patch.object(u.FirmwareImage, "load") as load:
                load.return_value.version = firmware_version
                if mismatch:
                    with self.assertRaises(ValueError):
                        u.download(update, target)
                    self.assertFalse(target.exists())
                else:
                    u.download(update, target)
                    self.assertEqual(target.read_bytes(), payload)
                self.assertEqual(list(Path(directory).iterdir()), [] if mismatch else [target])

    @staticmethod
    def exe():
        pe = bytearray(180)
        pe[:2] = b"MZ"
        pe[60:64] = (64).to_bytes(4, "little")
        pe[64:70] = b"PE\0\0\x64\x86"
        pe[88:90] = b"\x0b\x02"
        pe[156:158] = b"\x02\x00"
        return bytes(pe)

    def test_good_pc_and_preserved_destination_after_bad_bytes(self):
        payload = self.exe()
        self.check("pc", payload)
        self.check("pc", payload[:-1], expected=payload, mismatch=True)
        self.check("pc", payload + b"x", expected=payload, mismatch=True)
        self.check("pc", bytes(len(payload)), expected=payload, mismatch=True)
        self.check("pc", b"not a Windows PE", mismatch=True)

    def test_existing_file_and_creation_race_never_clobber(self):
        payload = self.exe()
        uinfo = u.Update("pc", "1.0.1", u.release_url("pc", "1.0.1"), hashlib.sha256(payload).hexdigest(), len(payload))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "PowerModule-PC-v1.0.1.exe")
            target.write_bytes(b"existing")
            with patch.object(u, "_open") as open_, self.assertRaises(FileExistsError):
                u.download(uinfo, target)
            open_.assert_not_called()
            self.assertEqual(target.read_bytes(), b"existing")
            target.unlink()

            def create_racing_destination(*_):
                target.write_bytes(b"raced")
                return io.BytesIO(payload)

            with patch.object(u, "_open", side_effect=create_racing_destination), self.assertRaises(FileExistsError):
                u.download(uinfo, target)
            self.assertEqual(target.read_bytes(), b"raced")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_requires_matching_embedded_firmware_version(self):
        self.check("firmware", b"firmware fixture", firmware_version="1.0.1")
        self.check("firmware", b"firmware fixture", firmware_version="2.0.0", mismatch=True)


class UpdateWindowTests(unittest.TestCase):
    def test_network_dialog_cannot_block_access_to_hardware_controls(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from pc_client.app import Window

        app = QApplication.instance() or QApplication([])
        window = Window()
        try:
            window.show()
            window.check_updates()
            dialog = window.update_dialog
            self.assertFalse(dialog.isModal())
            self.assertTrue(window.isEnabled())
            dialog.operation = "check"
            self.assertTrue(dialog.close())
            self.assertTrue(window.isVisible())
            self.assertFalse(dialog.isVisible())
        finally:
            window.close()
            window.worker.thread.join(timeout=2)
        self.assertIsNotNone(app)
