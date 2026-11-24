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
        self.handoff = h.Handoff(self.folder, self.old, (1, 2, 3), 42, monotonic() + 60, None)
        for name in ("_exited", "_close", "_remove_exact"):
            context = patch.object(h, name)
            setattr(self, name, context.start())
            self.addCleanup(context.stop)
        self._exited.return_value = False

    def test_commit_and_predecessor_exit_are_both_required(self):
        self.handoff.acknowledge()
        self.assertEqual(self.handoff.poll(), "pending")
        self._remove_exact.assert_not_called()
        h._mark(self.folder, "commit")
        self.assertEqual(self.handoff.poll(), "pending")
        self._remove_exact.assert_not_called()
        self._exited.return_value = True
        self.assertEqual(self.handoff.poll(), "complete")
        self._remove_exact.assert_called_once_with(self.old, (1, 2, 3))

    def test_predecessor_exit_without_commit_or_abort_preserves_old(self):
        self.handoff.acknowledge()
        self._exited.return_value = True
        self.assertEqual(self.handoff.poll(), "abort")
        self._remove_exact.assert_not_called()

    def test_replaced_file_remains(self):
        self.handoff.acknowledge()
        h._mark(self.folder, "commit")
        self._exited.return_value = True
        self._remove_exact.side_effect = ValueError("identity mismatch")
        self.assertEqual(self.handoff.poll(), "cleanup-failed")

    def test_handoff_cleanup_does_not_delete_extra_files(self):
        unrelated = self.folder / "other.exe"
        unrelated.write_bytes(b"unrelated")
        h._mark(self.folder, "abort")
        self.assertEqual(self.handoff.poll(), "abort")
        self.assertEqual(unrelated.read_bytes(), b"unrelated")
        self._remove_exact.assert_not_called()

    def test_resume_is_bounded_and_opt_in(self):
        valid = {"mode": "usb", "target": "COM4", "device_id": "PM-123"}
        self.assertEqual(h._resume(valid), valid)
        self.assertIsNone(h._resume(None))
        for invalid in ({**valid, "mode": "other"}, {**valid, "target": "\n"},
                        {**valid, "extra": "x"}, {**valid, "device_id": "X" * 257}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                h._resume(invalid)
