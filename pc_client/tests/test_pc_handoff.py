"""Handoff ordering tests; the native workflow additionally tests real Win32 processes."""
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest import TestCase
from unittest.mock import patch

from pc_client import pc_upgrade as h


class HandoffTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.old = self.folder / "old.exe"
        self.handoff = h.LegacyHandoff(self.folder, self.old, (1, 2, 3), 42, monotonic() + 60, None)
        for name in ("_exited", "_close", "complete_legacy_install"):
            context = patch.object(h, name)
            setattr(self, name, context.start())
            self.addCleanup(context.stop)
        self._exited.return_value = False

    def test_commit_and_predecessor_exit_are_both_required(self):
        self.handoff.acknowledge()
        self.assertEqual(self.handoff.poll(), "pending")
        self.complete_legacy_install.assert_not_called()
        h._mark(self.folder, "commit")
        self.assertEqual(self.handoff.poll(), "pending")
        self.complete_legacy_install.assert_not_called()
        self._exited.return_value = True
        self.assertEqual(self.handoff.poll(), "complete")
        self.complete_legacy_install.assert_called_once_with(self.old, (1, 2, 3))

    def test_predecessor_exit_without_commit_or_abort_preserves_old(self):
        self.handoff.acknowledge()
        self._exited.return_value = True
        self.assertEqual(self.handoff.poll(), "abort")
        self.complete_legacy_install.assert_not_called()

    def test_replaced_file_remains(self):
        self.handoff.acknowledge()
        h._mark(self.folder, "commit")
        self._exited.return_value = True
        self.complete_legacy_install.side_effect = ValueError("identity mismatch")
        self.assertEqual(self.handoff.poll(), "cleanup-failed")

    def test_handoff_cleanup_does_not_delete_extra_files(self):
        unrelated = self.folder / "other.exe"
        unrelated.write_bytes(b"unrelated")
        h._mark(self.folder, "abort")
        self.assertEqual(self.handoff.poll(), "abort")
        self.assertEqual(unrelated.read_bytes(), b"unrelated")
        self.complete_legacy_install.assert_not_called()

    def test_resume_is_bounded_and_opt_in(self):
        valid = {"mode": "usb", "target": "COM4", "device_id": "PM-123"}
        self.assertEqual(h._resume(valid), valid)
        self.assertIsNone(h._resume(None))
        for invalid in ({**valid, "mode": "other"}, {**valid, "target": "\n"},
                        {**valid, "extra": "x"}, {**valid, "device_id": "X" * 257}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                h._resume(invalid)


class CanonicalHandoffTests(TestCase):
    def test_canonical_rollback_never_overwrites_unknown_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old, backup = root / h.CANONICAL, root / "previous.exe"
            old.write_bytes(b"unknown")
            backup.write_bytes(b"old")
            with patch.object(h, "_path_identity", side_effect=lambda p: p.read_bytes()):
                with self.assertRaises(ValueError):
                    h._restore(old, backup, b"old", b"new")
                self.assertEqual(old.read_bytes(), b"unknown")
                self.assertEqual(backup.read_bytes(), b"old")

    def test_partial_replace_missing_canonical_restores_old(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old, backup = root / h.CANONICAL, root / "previous.exe"
            backup.write_bytes(b"old")
            with patch.object(h, "_path_identity", side_effect=lambda p: p.read_bytes()):
                h._restore(old, backup, b"old", b"new")
            self.assertEqual(old.read_bytes(), b"old")
            self.assertFalse(backup.exists())

    def test_keep_backup_if_helper_was_unable_to_cleanup(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "previous.exe"
            backup.write_bytes(b"old")
            handoff = h.Handoff(root, root / h.CANONICAL, 42, None)
            h._mark(root, "success")
            with patch.object(h, "_close"), patch.object(h, "_exited", return_value=True):
                self.assertEqual(handoff.poll(), "cleanup-failed")
            self.assertEqual(backup.read_bytes(), b"old")

    def test_child_does_not_claim_success_if_helper_crashes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            handoff = h.Handoff(root, root / h.CANONICAL, 42, None)
            handoff.acknowledge()
            with patch.object(h, "_close"), patch.object(h, "_exited", return_value=True):
                self.assertEqual(handoff.poll(), "abort")
