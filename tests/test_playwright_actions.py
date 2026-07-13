from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from human_ops.approvals import ReviewableProposal
from human_ops.chrome_profiles import resolve_chrome_profile
from human_ops.playwright_actions import execute_playwright_action, playwright_command


class PlaywrightActionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _chrome_fixture(
        root: str,
        *,
        profiles: tuple[tuple[str, str], ...] = (("Profile 1", "工作"),),
        active: tuple[str, ...] = ("Profile 1",),
    ) -> tuple[Path, dict[str, str]]:
        user_data = Path(root) / "Chrome"
        user_data.mkdir()
        info_cache = {}
        for directory, display_name in profiles:
            source = user_data / directory
            (source / "Network").mkdir(parents=True)
            (source / "Local Storage").mkdir()
            (source / "Preferences").write_text(
                json.dumps({"profile": {"name": display_name}}),
                encoding="utf-8",
            )
            (source / "Network" / "Cookies").write_bytes(f"cookie:{display_name}".encode())
            (source / "Local Storage" / "state").write_text(f"state:{display_name}", encoding="utf-8")
            info_cache[directory] = {"name": display_name}
        (user_data / "Local State").write_text(
            json.dumps(
                {
                    "profile": {
                        "info_cache": info_cache,
                        "profiles_order": [directory for directory, _name in profiles],
                        "last_active_profiles": list(active),
                    }
                }
            ),
            encoding="utf-8",
        )
        return user_data, {
            "IPET_CHROME_USER_DATA_DIR": str(user_data),
            "IPET_PLAYWRIGHT_PROFILE_ROOT": str(Path(root) / "snapshots"),
        }

    def test_builds_only_supported_cli_commands(self) -> None:
        profile = "工作"
        with tempfile.TemporaryDirectory() as temp_dir:
            _user_data, environment = self._chrome_fixture(temp_dir)
            with mock.patch.dict("os.environ", environment):
                open_command = playwright_command(
                    {"profile": profile, "operation": "open", "url": "https://example.com"}
                )
                attach_command = playwright_command({"profile": profile, "operation": "attach"})
        self.assertEqual(open_command[:3], ["open", "https://example.com", "--headed"])
        self.assertIn("/snapshots/", open_command[3])
        self.assertEqual(attach_command, ["attach", "--cdp=chrome"])
        self.assertEqual(playwright_command({"profile": profile, "operation": "click", "ref": "e12"}), ["click", "e12"])
        self.assertEqual(
            playwright_command({"profile": profile, "operation": "fill", "ref": "e4", "text": "hello"}),
            ["fill", "e4", "hello"],
        )
        self.assertEqual(playwright_command({"profile": profile, "operation": "go_back"}), ["go-back"])
        self.assertEqual(playwright_command({"profile": profile, "operation": "tab_select", "index": 2}), ["tab-select", "2"])

    def test_rejects_arbitrary_code_unsafe_urls_and_stale_ref_shapes(self) -> None:
        for arguments in (
            {"profile": "工作", "operation": "eval", "text": "document.cookie"},
            {"profile": "工作", "operation": "run-code", "text": "await page.click('body')"},
            {"profile": "工作", "operation": "open", "url": "file:///etc/passwd"},
            {"profile": "工作", "operation": "click", "ref": "button#submit"},
            {"operation": "snapshot"},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                playwright_command(arguments)

    async def test_execution_requires_approval(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="playwright",
            summary="打开示例页",
            payload={"profile": "工作", "operation": "open", "url": "https://example.com"},
        )
        with self.assertRaisesRegex(RuntimeError, "approved"):
            await execute_playwright_action(proposal)

    async def test_approved_execution_uses_argv_session_and_returns_output(self) -> None:
        calls: list[tuple[list[str], float]] = []

        async def runner(argv, timeout_sec):
            calls.append((list(argv), timeout_sec))
            return 0, "snapshot with ref=e7", ""

        proposal = ReviewableProposal.act(
            action_type="playwright",
            summary="观察页面",
            payload={"profile": "工作", "operation": "snapshot"},
        ).approve()
        with mock.patch("human_ops.playwright_actions._cli_prefix", return_value=["playwright-cli"]):
            result = await execute_playwright_action(proposal, runner=runner)

        self.assertEqual(calls[0][0][:2], ["playwright-cli", "--session"])
        self.assertTrue(calls[0][0][2].startswith("ipet-profile-v4-"))
        self.assertEqual(calls[0][0][3:], ["snapshot"])
        self.assertEqual(calls[0][1], 45.0)
        self.assertTrue(result["playwright_done"])
        self.assertEqual(result["profile"], "工作")
        self.assertFalse(result["headed"])
        self.assertEqual(result["output"], "snapshot with ref=e7")

    async def test_open_forces_visible_window_and_reports_headed_execution(self) -> None:
        calls: list[list[str]] = []

        async def runner(argv, _timeout_sec):
            calls.append(list(argv))
            return 0, "page opened", ""

        proposal = ReviewableProposal.act(
            action_type="playwright",
            summary="打开页面",
            payload={"profile": "个人", "operation": "open", "url": "https://example.com"},
        ).approve()
        with tempfile.TemporaryDirectory() as temp_dir:
            _user_data, environment = self._chrome_fixture(
                temp_dir,
                profiles=(("Default", "个人"),),
                active=("Default",),
            )
            with mock.patch.dict("os.environ", environment), mock.patch(
                "human_ops.playwright_actions._cli_prefix", return_value=["playwright-cli"]
            ):
                result = await execute_playwright_action(proposal, runner=runner)

        self.assertEqual(calls[0][-4:-1], ["open", "https://example.com", "--headed"])
        self.assertTrue(calls[0][-1].startswith("--profile="))
        self.assertTrue(result["headed"])
        self.assertTrue(result["persistent"])
        self.assertFalse(result["profile_reused"])
        self.assertEqual(result["profile_source"], "chrome_snapshot")

    async def test_open_copies_selected_chrome_login_state_without_changing_source(self) -> None:
        calls: list[list[str]] = []

        async def runner(argv, _timeout_sec):
            calls.append(list(argv))
            return 0, "opened", ""

        proposal = ReviewableProposal.act(
            action_type="playwright",
            summary="打开页面",
            payload={"profile": "工作", "operation": "open", "url": "https://example.com"},
        ).approve()
        with tempfile.TemporaryDirectory() as temp_dir:
            user_data, environment = self._chrome_fixture(temp_dir)
            source_cookie = user_data / "Profile 1" / "Network" / "Cookies"
            with mock.patch.dict("os.environ", environment), mock.patch(
                "human_ops.playwright_actions._cli_prefix", return_value=["playwright-cli"]
            ):
                await execute_playwright_action(proposal, runner=runner)
            profile_path = Path(calls[0][-1].split("=", 1)[1])
            self.assertEqual((profile_path / "Default" / "Network" / "Cookies").read_bytes(), b"cookie:\xe5\xb7\xa5\xe4\xbd\x9c")
            self.assertEqual(source_cookie.read_bytes(), b"cookie:\xe5\xb7\xa5\xe4\xbd\x9c")
            self.assertTrue((profile_path / ".ipet-profile.json").is_file())

    async def test_open_reuses_only_active_debuggable_chrome_profile(self) -> None:
        calls: list[list[str]] = []

        async def runner(argv, _timeout_sec):
            calls.append(list(argv))
            return 0, f"done:{argv[-2]}", ""

        proposal = ReviewableProposal.act(
            action_type="playwright",
            summary="打开页面",
            payload={"profile": "工作", "operation": "open", "url": "https://example.com"},
        ).approve()
        with tempfile.TemporaryDirectory() as temp_dir:
            _user_data, environment = self._chrome_fixture(temp_dir)
            with mock.patch.dict("os.environ", environment), mock.patch(
                "human_ops.playwright_actions.chrome_remote_debugging_ready", return_value=True
            ), mock.patch("human_ops.playwright_actions._cli_prefix", return_value=["playwright-cli"]):
                result = await execute_playwright_action(proposal, runner=runner)

        self.assertEqual(calls[0][-2:], ["attach", "--cdp=chrome"])
        self.assertEqual(calls[1][-2:], ["goto", "https://example.com"])
        self.assertTrue(result["attached"])
        self.assertFalse(result["persistent"])
        self.assertTrue(result["profile_reused"])
        self.assertEqual(result["profile_source"], "open_chrome")

    async def test_open_rejects_unknown_profile_and_lists_available_names(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="playwright",
            summary="打开页面",
            payload={"profile": "不存在", "operation": "open", "url": "https://example.com"},
        ).approve()
        with tempfile.TemporaryDirectory() as temp_dir:
            _user_data, environment = self._chrome_fixture(temp_dir)
            with mock.patch.dict("os.environ", environment):
                with self.assertRaisesRegex(ValueError, "Available profiles: 工作"):
                    await execute_playwright_action(proposal)

    async def test_attach_reuses_running_chrome_without_profile_directory(self) -> None:
        calls: list[list[str]] = []

        async def runner(argv, _timeout_sec):
            calls.append(list(argv))
            return 0, "attached", ""

        proposal = ReviewableProposal.act(
            action_type="playwright",
            summary="连接现有 Chrome",
            payload={"profile": "个人", "operation": "attach"},
        ).approve()
        with tempfile.TemporaryDirectory() as temp_dir:
            _user_data, environment = self._chrome_fixture(
                temp_dir,
                profiles=(("Default", "个人"),),
                active=("Default",),
            )
            with mock.patch.dict("os.environ", environment), mock.patch(
                "human_ops.playwright_actions.chrome_remote_debugging_ready", return_value=True
            ), mock.patch("human_ops.playwright_actions._cli_prefix", return_value=["playwright-cli"]):
                result = await execute_playwright_action(proposal, runner=runner)

        self.assertEqual(calls[0][-2:], ["attach", "--cdp=chrome"])
        self.assertTrue(result["attached"])
        self.assertFalse(result["persistent"])
        self.assertEqual(result["profile_source"], "open_chrome")

    async def test_attach_requires_selected_profile_to_be_active(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="playwright",
            summary="连接现有 Chrome",
            payload={"profile": "工作", "operation": "attach"},
        ).approve()
        with tempfile.TemporaryDirectory() as temp_dir:
            _user_data, environment = self._chrome_fixture(temp_dir, active=())
            with mock.patch.dict("os.environ", environment):
                with self.assertRaisesRegex(RuntimeError, "not the active Chrome profile"):
                    await execute_playwright_action(proposal)

    async def test_attach_rejects_ambiguous_multiple_active_profiles(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="playwright",
            summary="连接现有 Chrome",
            payload={"profile": "工作", "operation": "attach"},
        ).approve()
        with tempfile.TemporaryDirectory() as temp_dir:
            _user_data, environment = self._chrome_fixture(
                temp_dir,
                profiles=(("Default", "个人"), ("Profile 1", "工作")),
                active=("Default", "Profile 1"),
            )
            with mock.patch.dict("os.environ", environment):
                with self.assertRaisesRegex(RuntimeError, "Multiple Chrome profiles are active"):
                    await execute_playwright_action(proposal)

    def test_resolves_display_name_and_directory_to_same_chrome_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _user_data, environment = self._chrome_fixture(temp_dir)
            with mock.patch.dict("os.environ", environment):
                self.assertEqual(resolve_chrome_profile("工作").directory, "Profile 1")
                self.assertEqual(resolve_chrome_profile("Profile 1").display_name, "工作")

    async def test_cli_failure_is_explicit(self) -> None:
        async def runner(_argv, _timeout_sec):
            return 1, "", "page closed"

        proposal = ReviewableProposal.act(
            action_type="playwright",
            summary="点击登录",
            payload={"profile": "工作", "operation": "click", "ref": "e3"},
        ).approve()
        with mock.patch("human_ops.playwright_actions._cli_prefix", return_value=["playwright-cli"]):
            with self.assertRaisesRegex(RuntimeError, "page closed"):
                await execute_playwright_action(proposal, runner=runner)

    async def test_same_profile_reuses_named_session_and_other_profile_is_isolated(self) -> None:
        sessions: list[str] = []

        async def runner(argv, _timeout_sec):
            sessions.append(list(argv)[2])
            return 0, "ok", ""

        async def execute(profile: str) -> None:
            proposal = ReviewableProposal.act(
                action_type="playwright",
                summary="观察页面",
                payload={"profile": profile, "operation": "snapshot"},
            ).approve()
            await execute_playwright_action(proposal, runner=runner)

        with mock.patch("human_ops.playwright_actions._cli_prefix", return_value=["playwright-cli"]):
            await execute("工作")
            await execute("工作")
            await execute("个人")

        self.assertEqual(sessions[0], sessions[1])
        self.assertNotEqual(sessions[0], sessions[2])


if __name__ == "__main__":
    unittest.main()
