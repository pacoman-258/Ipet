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
    name: str | None = "Workspace Helper",
    description: str | None = "Helps with common workspace tasks.",
    body: str = "Always inspect the workspace before editing.",
    with_manifest: bool = False,
    with_openai_yaml: bool = True,
    frontmatter_extras: dict[str, object] | None = None,
) -> Path:
    skill_dir = Path(root)
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    if name is not None:
        lines.append(f'name: "{name}"')
    if description is not None:
        lines.append(f'description: "{description}"')
    for key, value in (frontmatter_extras or {}).items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        else:
            lines.append(f'{key}: "{value}"')
    lines.extend(["---", "", body, ""])
    skill_dir.joinpath("SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    if with_openai_yaml:
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
            self.assertEqual(skill.platform, "native")
            self.assertEqual(skill.manifest.tool_allowlist, ("local", "server.*"))
            self.assertEqual(len(skill.manifest.scripts), 1)
            self.assertEqual(skill.manifest.scripts[0].name, "echo_args")
            self.assertEqual(skill.references, ("references/guide.txt",))
            self.assertEqual(skill.assets, ("assets/badge.txt",))
            self.assertIn("scripts", skill.capabilities)

    def test_validate_skill_dir_rejects_missing_required_frontmatter_for_builtin(self) -> None:
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

    def test_validate_imported_skill_falls_back_with_warnings(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            skill_dir = _write_skill(
                root / "third_party_skills" / "vendor" / "wrapped-skill",
                name=None,
                description=None,
                body="Imported helper body.",
                with_openai_yaml=False,
            )

            skill = manager.validate_skill_dir(skill_dir, source_type="imported")

            self.assertTrue(skill.ok)
            self.assertEqual(skill.name, "wrapped-skill")
            self.assertEqual(skill.description, "Imported helper body.")
            self.assertEqual(skill.platform, "standard")
            self.assertGreaterEqual(len(skill.warnings), 2)

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

    def test_list_skills_recursively_discovers_imported_wrapped_packages(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            _write_skill(root / "skills" / "builtin" / "repo-guide", name="Repo Guide")
            imported_root = root / "Hermes" / "skills" / "imported"
            _write_skill(imported_root / "vendor-a" / "skill-alpha", name="Skill Alpha")
            _write_skill(imported_root / "vendor-b" / "2025.01" / "skill-beta", name="Skill Beta")
            _write_skill(root / "third_party_skills" / "legacy-skill", name="Legacy Skill")

            skills = manager.list_skills()
            by_id = {item.skill_id: item for item in skills}

            self.assertIn("repo-guide", by_id)
            self.assertIn("skill-alpha", by_id)
            self.assertIn("skill-beta", by_id)
            self.assertNotIn("legacy-skill", by_id)
            self.assertEqual(by_id["skill-alpha"].package_root.name, "vendor-a")
            self.assertEqual(by_id["skill-beta"].package_root.name, "vendor-b")
            self.assertTrue(str(by_id["skill-beta"].discovery_root).endswith("skill-beta"))

    def test_list_skills_ignores_nested_dirs_after_skill_root(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            parent_skill = _write_skill(
                root / "Hermes" / "skills" / "imported" / "package" / "skill-root",
                name="Skill Root",
            )
            _write_skill(parent_skill / "nested-child", name="Should Not Load")

            skills = manager.list_skills()

            self.assertEqual([item.skill_id for item in skills], ["skill-root"])

    def test_frontmatter_allowed_tools_synonyms_are_normalized(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            skill_dir = _write_skill(
                root / "third_party_skills" / "skillsmp" / "repo-guide",
                name="Repo Guide",
                description="Guidance",
                frontmatter_extras={"allowed-tools": ["local", "server.*"]},
            )

            skill = manager.validate_skill_dir(skill_dir, source_type="imported")

            self.assertEqual(skill.manifest.tool_allowlist, ("local", "server.*"))

    def test_clawhub_metadata_is_exposed_without_becoming_required(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            skill_dir = _write_skill(
                root / "third_party_skills" / "obsidian-direct-package" / "obsidian-direct",
                name="Obsidian Direct",
                description="ClawHub packaged skill",
                with_openai_yaml=False,
            )
            (skill_dir / ".clawhub").mkdir(exist_ok=True)
            (skill_dir / ".clawhub" / "origin.json").write_text(
                json.dumps(
                    {
                        "source_platform": "clawhub",
                        "package_name": "obsidian-direct",
                        "author": "openclaw",
                        "source_url": "https://clawhub-skills.example/obsidian-direct",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            skill = manager.validate_skill_dir(skill_dir, source_type="imported")

            self.assertEqual(skill.platform, "clawhub")
            self.assertEqual(skill.site_metadata["source_platform"], "clawhub")
            self.assertEqual(skill.site_metadata["author"], "openclaw")

    def test_import_local_directory_and_delete_imported_skill(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            source_dir = _write_skill(root / "source-skill", with_manifest=True)

            result = manager.import_local_directory(source_dir)
            installed_path = result.target_path

            self.assertTrue(installed_path.exists())
            self.assertEqual(installed_path.parent, root / "Hermes" / "skills" / "imported")
            self.assertEqual(result.skill.source_type, "imported")
            self.assertIn(result.skill.skill_id, {item.skill_id for item in manager.list_skills()})

            manager.delete_imported_skill(result.skill.skill_id)

            self.assertFalse(installed_path.exists())

    def test_import_local_directory_writes_to_hermes_and_ignores_legacy(self) -> None:
        with _workspace_tempdir() as root:
            hermes_dir = root / "Hermes" / "skills" / "imported"
            legacy_dir = root / "third_party_skills"
            manager = SkillManager(root, imported_dir=hermes_dir, legacy_imported_dir=legacy_dir)
            _write_skill(legacy_dir / "legacy-skill", name="Legacy Skill")
            source_dir = _write_skill(root / "source-skill", name="Hermes Skill")

            result = manager.import_local_directory(source_dir)

            self.assertEqual(result.target_path.parent, hermes_dir)
            self.assertTrue((hermes_dir / "hermes-skill").exists())
            self.assertIsNone(manager.get_skill("legacy-skill"))
            self.assertIsNotNone(manager.get_skill("hermes-skill"))

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
            self.assertEqual(result.target_path.parent, root / "Hermes" / "skills" / "imported")
            self.assertEqual(result.metadata["repo_url"], "https://example.com/repo.git")

    def test_get_skill_accepts_directory_name_alias(self) -> None:
        with _workspace_tempdir() as root:
            manager = SkillManager(root)
            imported_dir = root / "Hermes" / "skills" / "imported" / "atr-pptx"
            _write_skill(imported_dir, name="pptx", description="PPTX helper")

            by_id = manager.get_skill("pptx")
            by_alias = manager.get_skill("atr-pptx")

            self.assertIsNotNone(by_id)
            self.assertIsNotNone(by_alias)
            self.assertEqual(by_id.skill_id, "pptx")
            self.assertEqual(by_alias.skill_id, "pptx")
            self.assertIn("atr-pptx", by_alias.aliases)


if __name__ == "__main__":
    unittest.main()
