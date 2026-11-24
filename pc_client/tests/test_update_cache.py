import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pc_client import online_updates as u
from pc_client.firmware_update import IDENTITY_OFFSET, PROJECT_FIELD
from pc_client.tests import test_online_updates


def update(kind, version, payload):
    return u.Update(kind, version, u.release_url(kind, version),
                    hashlib.sha256(payload).hexdigest(), len(payload))


def firmware(version):
    image = bytearray(IDENTITY_OFFSET + len(PROJECT_FIELD) + 16)
    image[0] = 0xE9
    image[23] = 1
    image[IDENTITY_OFFSET:IDENTITY_OFFSET + len(PROJECT_FIELD)] = PROJECT_FIELD
    start = IDENTITY_OFFSET + len(PROJECT_FIELD)
    image[start:start + len(version)] = version.encode()
    return bytes(image) + hashlib.sha256(image).digest()


class CacheTests(unittest.TestCase):
    def test_valid_cache_needs_no_network_and_invalid_cache_is_atomic(self):
        data = firmware("1.2.2")
        latest = update("firmware", "1.2.2", data)
        with tempfile.TemporaryDirectory() as directory:
            cached = Path(directory) / u.FIRMWARE_ASSET
            old = firmware("1.2.1")
            cached.write_bytes(old)
            with patch.object(u, "_open", return_value=io.BytesIO(data[:-1])):
                with self.assertRaises(ValueError):
                    u.cache_firmware(latest, directory)
            self.assertEqual(cached.read_bytes(), old)
            with patch.object(u, "_open", return_value=io.BytesIO(data)), \
                    patch.object(Path, "replace", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    u.cache_firmware(latest, directory)
            self.assertEqual(cached.read_bytes(), old)
            with patch.object(u, "_open", return_value=io.BytesIO(data)):
                self.assertEqual(u.cache_firmware(latest, directory), cached)
            self.assertEqual(cached.read_bytes(), data)
            with patch.object(u, "_open") as get:
                self.assertEqual(u.cache_firmware(latest, directory), cached)
                get.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [cached])

    def test_same_size_and_hash_but_wrong_embedded_firmware_version(self):
        data = firmware("1.2.1")
        latest = update("firmware", "1.2.2", data)
        with tempfile.TemporaryDirectory() as directory:
            cached = Path(directory) / u.FIRMWARE_ASSET
            cached.write_bytes(data)
            self.assertIsNone(u.firmware_cached(latest, directory))
            with patch.object(u, "_open", return_value=io.BytesIO(data)):
                with self.assertRaisesRegex(ValueError, "内嵌版本"):
                    u.cache_firmware(latest, directory)
            self.assertEqual(cached.read_bytes(), data)

    def test_hardlinked_cache_is_never_overwritten_in_place(self):
        data = firmware("1.2.2")
        latest = update("firmware", "1.2.2", data)
        with tempfile.TemporaryDirectory() as directory:
            cached = Path(directory) / u.FIRMWARE_ASSET
            unrelated = Path(directory) / "unrelated"
            original = firmware("1.2.1")
            unrelated.write_bytes(original)
            try:
                cached.hardlink_to(unrelated)
            except OSError as error:
                self.skipTest(f"hard links unavailable: {error}")
            with patch.object(u, "_open", return_value=io.BytesIO(data)):
                u.cache_firmware(latest, directory)
            self.assertEqual(unrelated.read_bytes(), original)
            self.assertEqual(cached.read_bytes(), data)

    def test_exe_must_have_correct_embedded_version_before_publication_and_reuse(self):
        data = test_online_updates.DownloadTests.exe()
        latest = update("pc", "1.0.1", data)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / latest.filename
            with patch.object(u, "_open", return_value=io.BytesIO(data)), \
                    patch.object(u, "_windows_file_version", return_value=(1, 0, 0, 0)):
                with self.assertRaises(ValueError):
                    u.download_client(latest, directory)
            self.assertEqual(list(Path(directory).iterdir()), [])
            with patch.object(u, "_open", return_value=io.BytesIO(data)), \
                    patch.object(u, "_windows_file_version", return_value=(1, 0, 1, 0)):
                self.assertEqual(u.download_client(latest, directory), target)
                self.assertEqual(target.read_bytes(), data)
                u.verify_client(latest, target)
            with patch.object(u, "_open") as get, \
                    patch.object(u, "_windows_file_version", return_value=(1, 0, 0, 0)):
                with self.assertRaises(ValueError):
                    u.download_client(latest, directory)
                get.assert_not_called()
                self.assertEqual(target.read_bytes(), data)
