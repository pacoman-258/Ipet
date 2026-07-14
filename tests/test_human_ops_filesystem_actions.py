from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from human_ops import ReviewableProposal
from human_ops.filesystem_actions import FilesystemActionError, execute_filesystem_action, validate_filesystem_action


class HumanOpsFilesystemActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "allowed"
        self.root.mkdir()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    def proposal(self, action_type: str, **arguments) -> ReviewableProposal:
        return ReviewableProposal.act(
            action_type=action_type,
            summary=f"test {action_type}",
            payload=arguments,
        ).approve()

    async def run_action(self, action_type: str, **arguments):
        return await execute_filesystem_action(
            self.proposal(action_type, **arguments),
            allowed_roots=(self.root,),
        )

    async def test_read_list_write_copy_move_delete_are_bounded_and_real(self) -> None:
        created = await self.run_action("file_mkdir", path="notes")
        self.assertTrue(created["directory_created"])

        written = await self.run_action("file_write", path="notes/a.txt", content="你好", overwrite=False)
        self.assertTrue(written["written"])
        self.assertEqual(written["bytes"], len("你好".encode("utf-8")))

        listed = await self.run_action("file_list", path="notes")
        self.assertEqual([entry["name"] for entry in listed["entries"]], ["a.txt"])

        read = await self.run_action("file_read", path="notes/a.txt")
        self.assertEqual(read["content"], "你好")

        copied = await self.run_action("file_copy", source="notes/a.txt", destination="notes/b.txt")
        self.assertTrue(copied["copied"])
        moved = await self.run_action("file_move", source="notes/b.txt", destination="notes/c.txt")
        self.assertTrue(moved["moved"])

        deleted = await self.run_action("file_delete", path="notes/c.txt")
        self.assertEqual(deleted["kind"], "file")
        self.assertFalse((self.root / "notes" / "c.txt").exists())

    async def test_overwrite_and_recursive_delete_are_rejected_by_default(self) -> None:
        await self.run_action("file_write", path="a.txt", content="one")
        with self.assertRaises(FilesystemActionError):
            await self.run_action("file_write", path="a.txt", content="two")
        overwritten = await self.run_action("file_write", path="a.txt", content="two", overwrite=True)
        self.assertTrue(overwritten["overwrote"])

        await self.run_action("file_mkdir", path="folder")
        await self.run_action("file_write", path="folder/a.txt", content="x")
        with self.assertRaises(FilesystemActionError):
            await self.run_action("file_delete", path="folder")

    async def test_path_escape_protected_state_and_unapproved_action_fail(self) -> None:
        outside = Path(self.temp_dir.name) / "outside.txt"
        with self.assertRaises(FilesystemActionError):
            await self.run_action("file_write", path="../outside.txt", content="nope")
        self.assertFalse(outside.exists())

        with self.assertRaises(FilesystemActionError):
            await self.run_action("file_read", path="pet_config.json")

        unapproved = ReviewableProposal.act(
            action_type="file_list",
            summary="未批准",
            payload={"path": "."},
        )
        with self.assertRaises(FilesystemActionError):
            await execute_filesystem_action(unapproved, allowed_roots=(self.root,))

    def test_validation_is_explicit(self) -> None:
        self.assertEqual(validate_filesystem_action("file_read", {"path": "a.txt"}), (True, ""))
        self.assertEqual(validate_filesystem_action("file_write", {"path": "a.txt"}), (False, "file_write missing content"))
        self.assertEqual(validate_filesystem_action("file_move", {"source": "a.txt"}), (False, "file_move missing destination"))
        self.assertEqual(validate_filesystem_action("launch_app", {}), (False, "launch_app"))


if __name__ == "__main__":
    unittest.main()
