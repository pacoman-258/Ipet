from __future__ import annotations

import json
import shutil
import subprocess
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend.skills.manager import SkillImportResult, SkillManager
from backend.skills.runtime import SkillRuntime


TEST_TMP_ROOT = Path(__file__).resolve().parent / ".tmp_skills_compat"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)


@contextmanager
def _workspace_tempdir() -> Path:
    path = TEST_TMP_ROOT / f"tmp_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _write_skill(
    root: Path,
    *,
    name: str = "Repo Guide",
    description: str = "Guidance",
    body: str = "Always inspect the repo before editing.",
    with_manifest: bool = False,
    with_resources: bool = False,
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
        (skill_dir / "assets").mkdir(exist_ok=True)
        (skill_dir / "assets" / "sample.txt").write_text("asset", encoding="utf-8")
    if with_manifest:
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        scripts_dir.joinpath("echo.py").write_text(
            "\n".join(
                [
                    "import json, sys",
                    "payload = json.load(sys.stdin)",
                    "print(json.dumps({'ok': True, 'result': payload}, ensure_ascii=False))",
                ]
            ),
            encoding="utf-8",
        )
        skill_dir.joinpath("skill.json").write_text(
            json.dumps(
                {
                    "scripts": [
                        {
                            "name": "echo",
                            "description": "Echo args",
                            "path": "scripts/echo.py",
                            "input_schema": {"type": "object", "properties": {"value": {"type": "string"}}},
                            "timeout_sec": 10,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return skill_dir


def _fake_git_run_factory(source_repo: Path):
    def _fake_run(args, check=False, cwd=None, **_kwargs):
        command = list(args)
        if command[:3] == ["git", "clone", "--depth"]:
            clone_target = Path(command[-1])
            clone_target.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_repo, clone_target, dirs_exist_ok=True)
            return subprocess.CompletedProcess(command, 0)
        if command[:2] == ["git", "checkout"]:
            return subprocess.CompletedProcess(command, 0)
        return subprocess.CompletedProcess(command, 0)

    return _fake_run


class SkillsCompatibilityContractsTests(unittest.TestCase):
    def setUp(self) -> None:
        backend_app.SESSION_STORE.clear()
        backend_app.PENDING_CHAT_TURNS.clear()
        backend_app._reset_agent_graph_runtime()
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()
        backend_app.SESSION_STORE.clear()
        backend_app.PENDING_CHAT_TURNS.clear()
        backend_app._reset_agent_graph_runtime()

    def test_install_from_git_supports_subdir_and_records_source_metadata(self) -> None:
        with _workspace_tempdir() as root:
            repo_root = root / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            _write_skill(repo_root / "skills" / "pptx", name="PPTX Helper", description="Read PPTX", with_resources=True)
            manager = SkillManager(root)

            with mock.patch("backend.skills.manager.subprocess.run", side_effect=_fake_git_run_factory(repo_root)):
                result = manager.install_from_git(
                    "https://github.com/anthropics/skills.git",
                    subdir="skills/pptx",
                )

        self.assertEqual(result.skill.skill_id, "pptx-helper")
        self.assertEqual(result.metadata["source_subdir"], "skills/pptx")
        self.assertEqual(result.skill.source_repo, "https://github.com/anthropics/skills.git")
        self.assertEqual(result.skill.source_subdir, "skills/pptx")
        self.assertEqual(result.skill.compatibility_mode, "standard_adapter")
        self.assertEqual(result.skill.adapter_profile, "anthropics.skills.pptx")

    def test_install_from_git_rejects_missing_subdir(self) -> None:
        with _workspace_tempdir() as root:
            repo_root = root / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            _write_skill(repo_root / "skills" / "pptx", name="PPTX Helper", description="Read PPTX")
            manager = SkillManager(root)

            with mock.patch("backend.skills.manager.subprocess.run", side_effect=_fake_git_run_factory(repo_root)):
                with self.assertRaisesRegex(ValueError, "skill subdir not found"):
                    manager.install_from_git(
                        "https://github.com/anthropics/skills.git",
                        subdir="skills/missing",
                    )

    def test_standard_prompt_skill_exposes_resource_tools_and_blocks_escape(self) -> None:
        with _workspace_tempdir() as root:
            _write_skill(root / "skills" / "builtin" / "repo-guide", with_resources=True)
            manager = SkillManager(root)
            runtime = SkillRuntime(manager)

            resolved = runtime.resolve_active_skills(["repo-guide"])
            tools = {tool.name: tool for tool in list(resolved.resource_tools) + list(resolved.script_tools)}

            listed = tools["skill.repo-guide.list_resources"].call({})
            read_ok = tools["skill.repo-guide.read_resource"].call({"path": "references/guide.md"})
            read_bad = tools["skill.repo-guide.read_resource"].call({"path": "../outside.txt"})

        self.assertTrue(listed.ok)
        self.assertIn("references/guide.md", json.dumps(listed.structured_data, ensure_ascii=False))
        self.assertTrue(read_ok.ok)
        self.assertIn("Guide", json.dumps(read_ok.structured_data, ensure_ascii=False))
        self.assertFalse(read_bad.ok)
        self.assertIn("resource not available", read_bad.error)

    def test_pptx_adapter_tools_are_registered_and_missing_dependency_is_clear(self) -> None:
        with _workspace_tempdir() as root:
            skill_dir = _write_skill(
                root / "third_party_skills" / "pptx",
                name="PPTX",
                description="PPTX helper",
                with_resources=True,
            )
            skill_dir.joinpath(".skill_source.json").write_text(
                json.dumps(
                    {
                        "source_type": "git",
                        "source_repo": "https://github.com/anthropics/skills",
                        "source_subdir": "skills/pptx",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manager = SkillManager(root)
            runtime = SkillRuntime(manager)

            resolved = runtime.resolve_active_skills(["pptx"])
            tools = {tool.name: tool for tool in resolved.adapter_tools}
            bridge = runtime.build_tool_bridge(base_bridge=None, resolved=resolved)
            bridge_tool_names = [tool.name for tool in bridge.list_registered_tools()]
            result = tools["skill.pptx.extract_text"].call({"path": str(root / "missing.pptx")})

        self.assertIn("skill.pptx.extract_text", tools)
        self.assertIn("skill.pptx.thumbnail", tools)
        self.assertIn("skill.pptx.unpack_xml", tools)
        self.assertIn("skill.pptx.extract_text", bridge_tool_names)
        self.assertIn("skill.pptx.thumbnail", bridge_tool_names)
        self.assertIn("skill.pptx.unpack_xml", bridge_tool_names)
        self.assertFalse(result.ok)
        self.assertIn("pptx file not found", result.error)

    def test_import_git_endpoint_forwards_subdir(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            imported_skill = manager.validate_skill_dir(_write_skill(root / "source", with_resources=True), source_type="imported")
            install_mock = mock.Mock(
                return_value=SkillImportResult(
                    skill=imported_skill,
                    target_path=root / "third_party_skills" / imported_skill.skill_id,
                    metadata={},
                )
            )
            with mock.patch.object(backend_app, "_get_skill_manager", return_value=manager), mock.patch.object(
                manager,
                "install_from_git",
                install_mock,
            ):
                resp = self.client.post(
                    "/api/skills/import-git",
                    json={
                        "repo_url": "https://github.com/anthropics/skills.git",
                        "subdir": "skills/pptx",
                    },
                )

        self.assertEqual(resp.status_code, 200)
        install_mock.assert_called_once()
        self.assertEqual(install_mock.call_args.kwargs["subdir"], "skills/pptx")

    def test_settings_page_contains_skill_git_subdir_field(self) -> None:
        resp = self.client.get("/settings")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id="skill-git-subdir"', resp.text)


if __name__ == "__main__":
    unittest.main()
