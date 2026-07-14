from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NativeApprovalNotificationTests(unittest.TestCase):
    def test_helper_registers_two_actions_and_fails_closed(self) -> None:
        source = (ROOT / "app/native_approval_notifier/main.m").read_text(encoding="utf-8")
        self.assertIn('@"IPET_APPROVE" title:@"批准"', source)
        self.assertIn('@"IPET_REJECT" title:@"拒绝"', source)
        self.assertIn("UNNotificationCategoryOptionCustomDismissAction", source)
        self.assertIn('reason:@"notification_permission_denied"', source)
        self.assertIn('finish:NO reason:@"timed_out"', source)
        self.assertIn("removeDeliveredNotificationsWithIdentifiers", source)

    def test_controller_uses_stdin_and_never_puts_approval_text_in_arguments(self) -> None:
        source = (ROOT / "app/native_approval_notifications.py").read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn('subprocess.Popen(\n                [str(executable)]', source)
        self.assertIn("process.communicate(json.dumps(request", source)
        self.assertIn('"approved": False', source)
        self.assertNotIn("shell=True", source)
