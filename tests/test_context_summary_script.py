from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "context_summary.py"


def _load_context_summary():
    spec = importlib.util.spec_from_file_location("context_summary", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ContextSummaryScriptTests(unittest.TestCase):
    def test_repo_summary_lists_entrypoints_and_skips_noisy_paths(self) -> None:
        context_summary = _load_context_summary()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("print('pet')\n", encoding="utf-8")
            (root / "index.html").write_text("<html>pet</html>\n", encoding="utf-8")
            (root / "backend").mkdir()
            (root / "backend" / "app.py").write_text("app = object()\n", encoding="utf-8")
            (root / "backend" / "audio_cache").mkdir()
            (root / "backend" / "audio_cache" / "voice.mp3").write_bytes(b"audio")
            (root / "data" / "chat_topics").mkdir(parents=True)
            (root / "data" / "chat_topics" / "summary.json").write_text("{}", encoding="utf-8")
            (root / "pet_config.json").write_text("{}", encoding="utf-8")
            (root / ".pet_desktop_host.heartbeat.json").write_text("{}", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")

            summary = context_summary.build_repo_summary(root)

        self.assertIn("## Repo Map", summary)
        self.assertIn("- `main.py`", summary)
        self.assertIn("- `index.html`", summary)
        self.assertIn("- `backend/app.py`", summary)
        self.assertNotIn("__pycache__", summary)
        self.assertNotIn("backend/audio_cache", summary)
        self.assertNotIn("data/chat_topics", summary)
        self.assertNotIn("pet_config.json", summary)
        self.assertNotIn(".pet_desktop_host.heartbeat.json", summary)

    def test_reports_summary_extracts_recent_html_titles(self) -> None:
        context_summary = _load_context_summary()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "docs" / "reports"
            reports.mkdir(parents=True)
            (reports / "2026-06-09-example.html").write_text(
                "<!doctype html><html><head><title>Example Report</title></head><body>"
                "<h1>Example Heading</h1><p>Goal text</p></body></html>",
                encoding="utf-8",
            )

            summary = context_summary.build_reports_summary(root, limit=5)

        self.assertIn("## Recent Reports", summary)
        self.assertIn("2026-06-09-example.html", summary)
        self.assertIn("Example Report", summary)

    def test_logs_summary_includes_errors_and_tail_without_full_log(self) -> None:
        context_summary = _load_context_summary()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / ".service-logs"
            logs.mkdir()
            (logs / "backend.log").write_text(
                "\n".join(
                    [
                        "line 1",
                        "line 2",
                        "ERROR failed to observe",
                        "line 4",
                        "line 5",
                        "line 6",
                    ]
                ),
                encoding="utf-8",
            )

            summary = context_summary.build_logs_summary(root, tail_lines=2, error_limit=5)

        self.assertIn("## Logs", summary)
        self.assertIn("ERROR failed to observe", summary)
        self.assertIn("line 5", summary)
        self.assertIn("line 6", summary)
        self.assertNotIn("line 1", summary)


if __name__ == "__main__":
    unittest.main()
