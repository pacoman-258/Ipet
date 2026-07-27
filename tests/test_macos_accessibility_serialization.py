from __future__ import annotations

import unittest
from unittest import mock

from body import macos_accessibility


class MacOSAccessibilitySerializationTests(unittest.TestCase):
    def tearDown(self) -> None:
        with macos_accessibility._AX_NATIVE_OPERATION_CONDITION:
            macos_accessibility._AX_NATIVE_OPERATION_ACTIVE = False
            macos_accessibility._AX_NATIVE_FOREGROUND_WAITERS = 0
            macos_accessibility._AX_NATIVE_OPERATION_CONDITION.notify_all()

    def test_background_wait_for_native_channel_has_deadline(self) -> None:
        with macos_accessibility._AX_NATIVE_OPERATION_CONDITION:
            macos_accessibility._AX_NATIVE_OPERATION_ACTIVE = True

        with mock.patch.object(
            macos_accessibility,
            "AX_NATIVE_BACKGROUND_WAIT_TIMEOUT_SEC",
            0.01,
        ):
            with self.assertRaisesRegex(
                TimeoutError,
                "timed out waiting for serialized macOS Accessibility access",
            ):
                with macos_accessibility._ax_native_operation(
                    foreground=False
                ):
                    self.fail("timed-out operation must not enter the channel")

    def test_foreground_waiter_count_is_released_after_timeout(self) -> None:
        with macos_accessibility._AX_NATIVE_OPERATION_CONDITION:
            macos_accessibility._AX_NATIVE_OPERATION_ACTIVE = True

        with mock.patch.object(
            macos_accessibility,
            "AX_NATIVE_FOREGROUND_WAIT_TIMEOUT_SEC",
            0.01,
        ):
            with self.assertRaises(TimeoutError):
                with macos_accessibility._ax_native_operation(
                    foreground=True
                ):
                    self.fail("timed-out operation must not enter the channel")

        self.assertEqual(
            macos_accessibility._AX_NATIVE_FOREGROUND_WAITERS,
            0,
        )


if __name__ == "__main__":
    unittest.main()
