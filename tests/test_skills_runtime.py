from __future__ import annotations

import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from backend.skills import SkillAwareToolBridge
from backend.skills.adapters import PPTX_ADAPTER_PROFILE
from backend.skills.manager import SkillManager
from backend.skills.runtime import SkillRuntime, tool_name_matches_pattern
from backend.tool_runtime import Tool, ToolResult


TEST_TMP_ROOT = Path(__file__).resolve().parent / ".tmp_skills_runtime"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)


@contextmanager
def _workspace_tempdir() -> Path:
    path = TEST_TMP_ROOT / f"case_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _make_skill(
    root: Path,
    *,
    name: str = "Repo Guide",
    description: str = "Adds repo-specific guidance.",
    body: str = "Always run tests after changing backend code.",
    allowlist: list[str] | None = None,
    script_name: str | None = None,
    script_source: str | None = None,
    script_relative_path: str | None = None,
    write_script_manifest: bool = True,
) -> Path:
    skill_dir = Path(root)
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f'name: "{name}"',
                f'description: "{description}"',
                "---",
                "",
                body,
            ]
        ),
        encoding="utf-8",
    )
    manifest: dict[str, object] = {}
    if allowlist:
        manifest["tool_allowlist"] = allowlist
    if script_name:
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        relative_path = script_relative_path or f"{script_name}.py"
        script_path = scripts_dir / Path(relative_path)
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(
            script_source
            or "\n".join(
                [
                    "import json, sys",
                    "payload = json.load(sys.stdin)",
                    "print(json.dumps({'ok': True, 'result': {'echo': payload}}, ensure_ascii=False))",
                ]
            ),
            encoding="utf-8",
        )
        if write_script_manifest:
            manifest["scripts"] = [
                {
                    "name": script_name,
                    "description": f"Run {script_name}",
                    "path": f"scripts/{Path(relative_path).as_posix()}",
                    "input_schema": {"type": "object", "properties": {"value": {"type": "string"}}},
                    "timeout_sec": 10,
                }
            ]
    if manifest:
        skill_dir.joinpath("skill.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return skill_dir


def _make_standard_skill(
    root: Path,
    *,
    name: str = "Repo Guide",
    description: str = "Adds repo-specific guidance.",
    body: str = "Always inspect the repo before editing.",
    with_resources: bool = True,
    adapter_profile: str = "",
) -> Path:
    skill_dir = Path(root)
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f'name: "{name}"',
                f'description: "{description}"',
                "---",
                "",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )
    if with_resources:
        (skill_dir / "references").mkdir(exist_ok=True)
        (skill_dir / "references" / "guide.md").write_text("# Guide", encoding="utf-8")
    if adapter_profile == PPTX_ADAPTER_PROFILE:
        skill_dir.joinpath(".skill_source.json").write_text(
            json.dumps(
                {
                    "source_type": "git",
                    "source_repo": "https://github.com/anthropics/skills.git",
                    "source_subdir": "skills/pptx",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return skill_dir


class _FakeBridge:
    def __init__(self) -> None:
        self._tools = [
            Tool(
                name="local.read_file",
                description="Read local file",
                input_schema={"type": "object", "properties": {}},
                invoke=lambda arguments: {"tool": "local.read_file", "arguments": arguments},
                source="local",
            ),
            Tool(
                name="remote.search",
                description="Search remote service",
                input_schema={"type": "object", "properties": {}},
                invoke=lambda arguments: {"tool": "remote.search", "arguments": arguments},
                source="remote",
            ),
        ]

    def list_registered_tools(self) -> list[Tool]:
        return list(self._tools)

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> ToolResult:
        for tool in self._tools:
            if tool.name == tool_name:
                return tool.call(arguments)
        raise KeyError(tool_name)


class SkillRuntimeTests(unittest.TestCase):
    def test_resolve_active_skills_renders_prompt_and_allowlist(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            builtin_dir = root / "skills" / "builtin"
            _make_skill(builtin_dir / "repo-guide", allowlist=["local"])
            _make_skill(builtin_dir / "search-guide", name="Search Guide", body="Prefer explicit search queries.")
            runtime = SkillRuntime(manager)

            resolved = runtime.resolve_active_skills(
                ["repo-guide"],
                default_active_ids=["search-guide"],
            )

            self.assertEqual(resolved.skill_ids, ("repo-guide",))
            self.assertIn("[Skill: Repo Guide]", resolved.prompt_text)
            self.assertEqual(resolved.tool_allowlist, ("local",))
            self.assertFalse(resolved.defaulted)

    def test_builtin_browser_automation_allowlist_filters_to_browser_tools(self) -> None:
        manager = SkillManager(Path(__file__).resolve().parents[1])
        runtime = SkillRuntime(manager)
        resolved = runtime.resolve_active_skills(["browser-automation"])

        class _BrowserBridge:
            def __init__(self) -> None:
                self._tools = [
                    Tool(
                        name="playwright.browser_navigate",
                        description="Navigate a page",
                        input_schema={"type": "object", "properties": {}},
                        invoke=lambda _arguments: {"ok": True},
                    ),
                    Tool(
                        name="playwright_mcp.browser_click",
                        description="Click a page element",
                        input_schema={"type": "object", "properties": {}},
                        invoke=lambda _arguments: {"ok": True},
                    ),
                    Tool(
                        name="read_file",
                        description="Read a file",
                        input_schema={"type": "object", "properties": {}},
                        invoke=lambda _arguments: {"ok": True},
                    ),
                ]

            def list_registered_tools(self) -> list[Tool]:
                return list(self._tools)

        bridge = SkillAwareToolBridge(_BrowserBridge(), runtime, resolved)
        tool_names = [tool.name for tool in bridge.list_registered_tools()]

        self.assertEqual(resolved.tool_allowlist, ("playwright.", "playwright_mcp."))
        self.assertIn("playwright.browser_navigate", tool_names)
        self.assertIn("playwright_mcp.browser_click", tool_names)
        self.assertIn("skill.browser-automation.resolve_mcp_recipe", tool_names)
        self.assertIn("skill.browser-automation.compact_browser_result", tool_names)
        self.assertNotIn("read_file", tool_names)

    def test_browser_automation_resolve_mcp_recipe_prefers_visible_family_and_validates_inputs(self) -> None:
        manager = SkillManager(Path(__file__).resolve().parents[1])
        runtime = SkillRuntime(manager)
        resolved = runtime.resolve_active_skills(["browser-automation"])
        bridge = SkillAwareToolBridge(_FakeBridge(), runtime, resolved)

        playwright_only = bridge.call_tool(
            "skill.browser-automation.resolve_mcp_recipe",
            {
                "visible_tool_names": ["playwright.browser_navigate", "playwright.browser_type"],
                "recipe": "open_and_type",
                "url": "https://example.com",
                "text": "hello",
            },
        )
        playwright_mcp_only = bridge.call_tool(
            "skill.browser-automation.resolve_mcp_recipe",
            {
                "visible_tool_names": ["playwright_mcp.browser_navigate", "playwright_mcp.browser_type"],
                "recipe": "open_and_type",
                "url": "https://example.com",
                "text": "hello",
            },
        )
        both_visible = bridge.call_tool(
            "skill.browser-automation.resolve_mcp_recipe",
            {
                "visible_tool_names": [
                    "playwright.browser_navigate",
                    "playwright.browser_type",
                    "playwright_mcp.browser_navigate",
                    "playwright_mcp.browser_type",
                ],
                "recipe": "open_page",
                "url": "https://example.com",
            },
        )
        missing_required = bridge.call_tool(
            "skill.browser-automation.resolve_mcp_recipe",
            {
                "visible_tool_names": ["playwright.browser_click"],
                "recipe": "click_followup",
            },
        )

        self.assertTrue(playwright_only.ok)
        self.assertEqual(playwright_only.structured_data["family"], "playwright")
        self.assertEqual(
            [item["name"] for item in playwright_only.structured_data["tool_calls"]],
            ["playwright.browser_navigate", "playwright.browser_type"],
        )
        self.assertTrue(playwright_mcp_only.ok)
        self.assertEqual(playwright_mcp_only.structured_data["family"], "playwright_mcp")
        self.assertEqual(
            [item["name"] for item in playwright_mcp_only.structured_data["tool_calls"]],
            ["playwright_mcp.browser_navigate", "playwright_mcp.browser_type"],
        )
        self.assertTrue(both_visible.ok)
        self.assertEqual(both_visible.structured_data["family"], "playwright_mcp")
        self.assertFalse(missing_required.ok)
        self.assertIn("selector is required", missing_required.error)

    def test_browser_automation_compact_browser_result_reduces_long_output(self) -> None:
        manager = SkillManager(Path(__file__).resolve().parents[1])
        runtime = SkillRuntime(manager)
        resolved = runtime.resolve_active_skills(["browser-automation"])
        bridge = SkillAwareToolBridge(_FakeBridge(), runtime, resolved)

        result = bridge.call_tool(
            "skill.browser-automation.compact_browser_result",
            {
                "raw_text": (
                    "Result one has the main heading.\n"
                    "Result two explains the form state in more detail.\n"
                    "Result three repeats the success banner."
                ),
                "structured_data": {
                    "title": "Example page",
                    "status": "ready",
                },
                "max_items": 4,
            },
        )

        self.assertTrue(result.ok)
        self.assertIn("Compressed raw text and structured data", result.structured_data["summary"])
        self.assertLessEqual(len(result.structured_data["items"]), 4)
        self.assertTrue(result.structured_data["raw_preview"].startswith("Result one"))

    def test_script_tools_execute_and_namespace_correctly(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            builtin_dir = root / "skills" / "builtin"
            _make_skill(builtin_dir / "repo-guide", script_name="echo")
            runtime = SkillRuntime(manager)
            resolved = runtime.resolve_active_skills(["repo-guide"])

            tools = runtime.build_script_tools(resolved)
            result = tools[0].call({"value": "hello"})

            self.assertEqual(tools[0].name, "skill.repo-guide.echo")
            self.assertTrue(result.ok)
            self.assertEqual(result.structured_data, {"echo": {"value": "hello"}})

    def test_script_tool_returns_error_for_invalid_json_stdout(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            builtin_dir = root / "skills" / "builtin"
            _make_skill(
                builtin_dir / "broken-skill",
                name="Broken Skill",
                script_name="broken",
                script_source="print('not json')",
            )
            runtime = SkillRuntime(manager)
            resolved = runtime.resolve_active_skills(["broken-skill"])

            result = runtime.build_script_tools(resolved)[0].call({})

            self.assertFalse(result.ok)
            self.assertIn("not valid JSON", result.error)

    def test_auto_discovered_cli_script_tools_run_without_manifest(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            imported_dir = root / "Hermes" / "skills" / "imported"
            _make_skill(
                imported_dir / "pptx-helper",
                name="PPTX Helper",
                description="Imported CLI helper",
                script_name="thumbnail",
                write_script_manifest=False,
                script_source="\n".join(
                    [
                        "import sys",
                        "if __name__ == '__main__':",
                        "    print(' '.join(sys.argv[1:]))",
                    ]
                ),
            )
            runtime = SkillRuntime(manager)
            resolved = runtime.resolve_active_skills(["pptx-helper"])

            tools = runtime.build_script_tools(resolved)
            result = tools[0].call({"args": ["slides.pptx", "preview"]})

            self.assertEqual([tool.name for tool in tools], ["skill.pptx-helper.thumbnail"])
            self.assertTrue(result.ok)
            self.assertEqual(result.content, "slides.pptx preview")
            self.assertEqual(tools[0].metadata["script_name"], "thumbnail")

    def test_auto_discovered_scripts_skip_internal_helper_dirs(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            imported_dir = root / "Hermes" / "skills" / "imported"
            _make_skill(
                imported_dir / "pptx-helper",
                name="PPTX Helper",
                description="Imported CLI helper",
                script_name="unpack",
                script_relative_path="office/unpack.py",
                write_script_manifest=False,
                script_source="\n".join(["if __name__ == '__main__':", "    print('unpacked')"]),
            )
            _make_skill(
                imported_dir / "pptx-helper",
                name="PPTX Helper",
                description="Imported CLI helper",
                script_name="helper",
                script_relative_path="office/helpers/helper.py",
                write_script_manifest=False,
                script_source="\n".join(["if __name__ == '__main__':", "    print('helper')"]),
            )
            runtime = SkillRuntime(manager)
            resolved = runtime.resolve_active_skills(["pptx-helper"])

            tool_names = [tool.name for tool in runtime.build_script_tools(resolved)]

            self.assertIn("skill.pptx-helper.office.unpack", tool_names)
            self.assertNotIn("skill.pptx-helper.office.helpers.helper", tool_names)

    def test_auto_discovered_office_pack_tool_accepts_structured_arguments(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            imported_dir = root / "Hermes" / "skills" / "imported"
            _make_skill(
                imported_dir / "pptx-helper",
                name="PPTX Helper",
                description="Imported CLI helper",
                script_name="pack",
                script_relative_path="office/pack.py",
                write_script_manifest=False,
                script_source="\n".join(
                    [
                        "import json, sys",
                        "if __name__ == '__main__':",
                        "    print(json.dumps({'argv': sys.argv[1:]}, ensure_ascii=False))",
                    ]
                ),
            )
            runtime = SkillRuntime(manager)
            resolved = runtime.resolve_active_skills(["pptx-helper"])

            tools = {tool.name: tool for tool in runtime.build_script_tools(resolved)}
            pack_tool = tools["skill.pptx-helper.office.pack"]
            result = pack_tool.call(
                {
                    "input_directory": "work/unpacked",
                    "output_file": "dist/output.pptx",
                    "original_file": "source/template.pptx",
                    "validate": False,
                }
            )

            self.assertTrue(result.ok)
            self.assertEqual(
                result.structured_data,
                {
                    "argv": [
                        "work/unpacked",
                        "dist/output.pptx",
                        "--original",
                        "source/template.pptx",
                        "--validate",
                        "false",
                    ]
                },
            )
            self.assertIn("final .pptx", pack_tool.description)
            self.assertEqual(pack_tool.input_schema.get("required"), ["input_directory", "output_file"])

    def test_skill_aware_tool_bridge_filters_base_tools_and_adds_skill_tools(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            builtin_dir = root / "skills" / "builtin"
            _make_skill(builtin_dir / "repo-guide", allowlist=["local"], script_name="echo")
            runtime = SkillRuntime(manager)
            resolved = runtime.resolve_active_skills(["repo-guide"])
            bridge = runtime.build_tool_bridge(_FakeBridge(), resolved)

            tool_names = [tool.name for tool in bridge.list_registered_tools()]
            denied = bridge.call_tool("remote.search", {})
            allowed = bridge.call_tool("local.read_file", {"path": "README.md"})
            skill_result = bridge.call_tool("skill.repo-guide.echo", {"value": "ok"})

            self.assertEqual(tool_names, ["local.read_file", "skill.repo-guide.echo"])
            self.assertFalse(denied.ok)
            self.assertTrue(allowed.ok)
            self.assertTrue(skill_result.ok)

    def test_skill_aware_tool_bridge_exposes_standard_resource_tools(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            builtin_dir = root / "skills" / "builtin"
            _make_standard_skill(builtin_dir / "repo-guide")
            runtime = SkillRuntime(manager)
            resolved = runtime.resolve_active_skills(["repo-guide"])
            bridge = runtime.build_tool_bridge(_FakeBridge(), resolved)

            tool_names = [tool.name for tool in bridge.list_registered_tools()]
            listed = bridge.call_tool("skill.repo-guide.list_resources", {})
            read_ok = bridge.call_tool("skill.repo-guide.read_resource", {"path": "references/guide.md"})

            self.assertIn("skill.repo-guide.list_resources", tool_names)
            self.assertIn("skill.repo-guide.read_resource", tool_names)
            self.assertTrue(listed.ok)
            self.assertTrue(read_ok.ok)
            self.assertIn("Guide", json.dumps(read_ok.structured_data, ensure_ascii=False))

    def test_skill_aware_tool_bridge_exposes_pptx_adapter_tools(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            imported_dir = root / "Hermes" / "skills" / "imported"
            _make_standard_skill(
                imported_dir / "pptx",
                name="PPTX",
                description="PPTX helper",
                adapter_profile=PPTX_ADAPTER_PROFILE,
            )
            runtime = SkillRuntime(manager)
            resolved = runtime.resolve_active_skills(["pptx"])
            bridge = runtime.build_tool_bridge(_FakeBridge(), resolved)

            tool_names = [tool.name for tool in bridge.list_registered_tools()]

            self.assertIn("skill.pptx.extract_text", tool_names)
            self.assertIn("skill.pptx.thumbnail", tool_names)
            self.assertIn("skill.pptx.unpack_xml", tool_names)

    def test_tool_name_matches_pattern_supports_exact_and_prefix_rules(self) -> None:
        self.assertTrue(tool_name_matches_pattern("local.read_file", "local"))
        self.assertTrue(tool_name_matches_pattern("server.search", "server.*"))
        self.assertTrue(tool_name_matches_pattern("server.search", "server."))
        self.assertFalse(tool_name_matches_pattern("remote.search", "local"))


if __name__ == "__main__":
    unittest.main()
