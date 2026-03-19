from __future__ import annotations

import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

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
        scripts_dir.joinpath(f"{script_name}.py").write_text(
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
        manifest["scripts"] = [
            {
                "name": script_name,
                "description": f"Run {script_name}",
                "path": f"scripts/{script_name}.py",
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
            imported_dir = root / "third_party_skills"
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
