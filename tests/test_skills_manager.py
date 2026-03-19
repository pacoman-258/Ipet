from __future__ import annotations

import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from backend.skills.manager import SkillManager


TEST_TMP_ROOT = Path(__file__).resolve().parent / ".tmp_skills_manager"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)


@contextmanager
def _workspace_tempdir() -> Path:
    path = TEST_TMP_ROOT / f"case_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _write_skill(
    root: Path,
    *,
    name: str = "Workspace Helper",
    description: str = "Helps with common workspace tasks.",
    body: str = "Always inspect the workspace before editing.",
    with_manifest: bool = False,
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
    agents_dir = skill_dir / "agents"
    agents_dir.mkdir(exist_ok=True)
    agents_dir.joinpath("openai.yaml").write_text(
        "\n".join(
            [
                'display_name: "Workspace Helper+"',
                'short_description: "Scoped repo helper"',
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "references").mkdir(exist_ok=True)
    (skill_dir / "references" / "guide.txt").write_text("reference", encoding="utf-8")
    (skill_dir / "assets").mkdir(exist_ok=True)
    (skill_dir / "assets" / "badge.txt").write_text("asset", encoding="utf-8")
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
                    "version": "1.0.0",
                    "tool_allowlist": ["local", "server.*"],
                    "scripts": [
                        {
                            "name": "echo_args",
                            "description": "Echo provided arguments",
                            "path": "scripts/echo.py",
                            "input_schema": {
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                            },
                            "timeout_sec": 12,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return skill_dir


class SkillManagerTests(unittest.TestCase):
    def test_validate_skill_dir_parses_skill_metadata_and_manifest(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            skill_dir = _write_skill(root / "source-skill", with_manifest=True)

            skill = manager.validate_skill_dir(skill_dir)

            self.assertEqual(skill.skill_id, "workspace-helper")
            self.assertEqual(skill.display_name, "Workspace Helper+")
            self.assertEqual(skill.short_description, "Scoped repo helper")
            self.assertTrue(skill.ok)
            self.assertEqual(skill.manifest.tool_allowlist, ("local", "server.*"))
            self.assertEqual(len(skill.manifest.scripts), 1)
            self.assertEqual(skill.manifest.scripts[0].name, "echo_args")
            self.assertEqual(skill.references, ("references/guide.txt",))
            self.assertEqual(skill.assets, ("assets/badge.txt",))

    def test_validate_skill_dir_rejects_missing_required_frontmatter(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            skill_dir = root / "invalid-skill"
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_dir.joinpath("SKILL.md").write_text(
                "\n".join(["---", 'name: "Broken Skill"', "---", "", "No description."]),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "requires description"):
                manager.validate_skill_dir(skill_dir)

    def test_validate_skill_dir_rejects_script_path_escape(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            outside_script = root / "escape.py"
            outside_script.write_text("print('hi')", encoding="utf-8")
            skill_dir = _write_skill(root / "escape-skill")
            skill_dir.joinpath("skill.json").write_text(
                json.dumps(
                    {
                        "scripts": [
                            {
                                "name": "escape",
                                "description": "bad",
                                "path": "../escape.py",
                                "input_schema": {"type": "object", "properties": {}},
                                "timeout_sec": 10,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "escapes skill directory"):
                manager.validate_skill_dir(skill_dir)

    def test_import_local_directory_and_delete_imported_skill(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            source_dir = _write_skill(root / "source-skill", with_manifest=True)

            result = manager.import_local_directory(source_dir)
            installed_path = result.target_path

            self.assertTrue(installed_path.exists())
            self.assertEqual(result.skill.source_type, "imported")
            self.assertIn(result.skill.skill_id, {item.skill_id for item in manager.list_skills()})

            manager.delete_imported_skill(result.skill.skill_id)

            self.assertFalse(installed_path.exists())

    def test_install_from_git_clones_into_managed_directory(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)

            def fake_run(command: list[str], check: bool, cwd: str) -> None:
                self.assertEqual(command[:4], ["git", "clone", "--depth", "1"])
                target_dir = Path(command[-1])
                _write_skill(target_dir, name="Git Skill")
                return None

            with patch("backend.skills.manager.subprocess.run", side_effect=fake_run) as run_mock:
                result = manager.install_from_git("https://example.com/repo.git")

            self.assertTrue(run_mock.called)
            self.assertEqual(result.skill.skill_id, "git-skill")
            self.assertTrue(result.target_path.exists())
            self.assertEqual(result.metadata["repo_url"], "https://example.com/repo.git")


if __name__ == "__main__":
    unittest.main()
