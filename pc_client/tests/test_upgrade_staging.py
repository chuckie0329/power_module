from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from pc_client.update_dialog import discard_staged_client


class PcStagingTests(unittest.TestCase):
    def test_only_verified_candidate_is_removed_from_private_staging(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            staging = root / ".PowerModule-download-test"
            staging.mkdir()
            fixed = root / "PowerModule-PC.exe"
            fixed.write_bytes(b"running-old")
            other = staging / "user-data"
            other.write_bytes(b"untouched")
            staged = staging / "PowerModule-PC-v1.0.4.exe"
            asset = b"verified-candidate"
            staged.write_bytes(asset)
            update = SimpleNamespace(filename=staged.name, size=len(asset), sha256=sha256(asset).hexdigest())
            discard_staged_client(staged, update)
            self.assertFalse(staged.exists())
            self.assertEqual(fixed.read_bytes(), b"running-old")
            self.assertEqual(other.read_bytes(), b"untouched")
            staged.write_bytes(b"changed-candidate")
            discard_staged_client(staged, update)
            self.assertTrue(staged.exists())

    def test_non_staging_files_are_not_touched(self):
        with TemporaryDirectory() as tmp:
            fixed = Path(tmp).resolve() / "PowerModule-PC.exe"
            fixed.write_bytes(b"untouched")
            update = SimpleNamespace(filename=fixed.name, size=9, sha256=sha256(b"untouched").hexdigest())
            discard_staged_client(fixed, update)
            self.assertTrue(fixed.is_file())
