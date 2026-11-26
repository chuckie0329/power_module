import unittest
from unittest.mock import AsyncMock, patch

from pc_client.app import APPLY_PROTECTION
from pc_client.connection import Client, DeviceError
from pc_client.tests import test_memory_group_layout

BLOCKS = [
    ("set_protection", {"ovp_mV": 6100, "ocp_mA": 4400}),
    ("set_opp", {"opp_mW": 22000}),
]


def reply(revision, values, group=0, enabled=False):
    return {"state_rev": revision, "applied": {"group": group, "enabled": enabled},
            "protection": values}


class ProtectionSequenceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = Client(None)
        self.client.link = object()
        self.client.ready = True

    async def test_both_write_with_revision_from_first_readback(self):
        client = self.client
        with patch.object(client, "change", new=AsyncMock(side_effect=[
                reply(24, BLOCKS[0][1]), reply(25, BLOCKS[1][1])])) as change:
            result = await client.apply_protection(0, 23, BLOCKS)
        self.assertEqual(result, {"group": 0, "verified": ["set_protection", "set_opp"], "error": ""})
        self.assertEqual([c.args for c in change.await_args_list],
                         [(*BLOCKS[0], 23), (*BLOCKS[1], 24)])

    async def test_first_reply_must_match_group_output_and_values(self):
        for first in (reply(24, BLOCKS[0][1], group=1), reply(24, BLOCKS[0][1], enabled=True),
                      reply(24, {}), reply(None, BLOCKS[0][1])):
            with self.subTest(first=first), patch.object(self.client, "change", new=AsyncMock(return_value=first)) as change:
                result = await self.client.apply_protection(0, 23, BLOCKS)
                self.assertEqual(change.await_count, 1)
                self.assertEqual(result["verified"], [])
                self.assertEqual(result["error"], "READBACK_MISMATCH")

    async def test_failed_second_write_reports_verified_first(self):
        with patch.object(self.client, "change", new=AsyncMock(side_effect=[
                reply(24, BLOCKS[0][1]), DeviceError("REV_CONFLICT")])):
            result = await self.client.apply_protection(0, 23, BLOCKS)
        self.assertEqual(result["verified"], ["set_protection"])
        self.assertEqual(result["error"], "REV_CONFLICT")

    async def test_does_not_continue_on_replaced_connection(self):
        async def replace(*args):
            self.client.link = object()
            return reply(24, BLOCKS[0][1])
        with patch.object(self.client, "change", new=AsyncMock(side_effect=replace)) as change:
            result = await self.client.apply_protection(0, 23, BLOCKS)
        self.assertEqual(change.await_count, 1)
        self.assertEqual(result["verified"], ["set_protection"])
        self.assertEqual(result["error"], "LINK_ERROR")


class ProtectionButtonTests(unittest.TestCase):
    def setUp(self):
        h = test_memory_group_layout.MemoryGroupTests()
        h.setUpClass()
        h.setUp()
        self.addCleanup(h.doCleanups)
        self.window = h.window
        h.live(enabled=False)
        self.sent = []

        def send(action, coroutine):
            args = coroutine.cr_frame.f_locals
            self.sent.append((action, args["group"], args["revision"], args["operations"]))
            coroutine.close()

        self.window.worker.submit.side_effect = send

    def test_only_edited_opp_is_sent_and_button_serializes(self):
        w = self.window
        w.apply_protection.click()
        self.assertFalse(self.sent)
        self.assertFalse(hasattr(w, "apply_opp"))
        w.opp.setValue(22)
        w.apply_protection.click()
        w.apply_protection.click()
        self.assertEqual(self.sent, [(APPLY_PROTECTION, 0, 23, BLOCKS[1:])])

    def test_both_edited_fields_and_partial_result(self):
        w = self.window
        w.ovp.setValue(6.1)
        w.opp.setValue(22)
        w.apply_protection.click()
        self.assertEqual(self.sent, [(APPLY_PROTECTION, 0, 23, BLOCKS)])
        w.finished(APPLY_PROTECTION, {"group": 0, "verified": ["set_protection"], "error": "REV_CONFLICT"}, "")
        self.assertFalse(w.protection_dirty)
        self.assertTrue(w.opp_dirty)
        self.assertIn("OVP/OCP 已读回", w.message.text())
        self.assertIn("其余未确认", w.message.text())

    def test_preflight_invalid_ocp_does_not_send_opp_or_ovp(self):
        w = self.window
        w.state["protection"]["ocp_step_mA"] = 10
        w.ocp.setValue(4.401)
        w.opp.setValue(22)
        w.apply_protection.click()
        self.assertFalse(self.sent)

    def test_output_on_blocks_all_writes(self):
        w = self.window
        w.ovp.setValue(6.1)
        w.opp.setValue(22)
        w.state["applied"]["enabled"] = True
        w.render()
        self.assertFalse(w.apply_protection.isEnabled())
        w.set_protection()
        self.assertFalse(self.sent)
