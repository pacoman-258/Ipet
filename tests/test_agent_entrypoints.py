from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class AgentEntrypointTests(unittest.TestCase):
    def test_canonical_guide_contains_cross_tool_contract(self) -> None:
        guide = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("## Cross-Tool Collaboration Contract", guide)
        self.assertIn("one writer by default", guide)
        self.assertIn("single source of project-wide agent rules", guide)

    def test_claude_imports_canonical_guide_without_duplication(self) -> None:
        claude_entrypoint = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")

        self.assertEqual(claude_entrypoint, "@AGENTS.md\n")


if __name__ == "__main__":
    unittest.main()
