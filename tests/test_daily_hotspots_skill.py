from __future__ import annotations

import codecs
import importlib.util
import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path


TEST_TMP_ROOT = Path(__file__).resolve().parent / ".tmp_daily_hotspots"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "third_party_skills" / "daily-hotspots" / "scripts" / "save_report.py"


def _load_save_report_module():
    spec = importlib.util.spec_from_file_location("daily_hotspots_save_report", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _workspace_tempdir() -> Path:
    path = TEST_TMP_ROOT / f"case_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _write_config(root: Path, allowlist: list[str]) -> None:
    payload = {
        "chat": {
            "tooling": {
                "file_allowlist": allowlist,
            }
        }
    }
    root.joinpath("pet_config.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    root.joinpath("backend").mkdir(parents=True, exist_ok=True)


def _sample_payload() -> dict[str, object]:
    return {
        "generated_at": "2026-03-21T09:30:45+08:00",
        "platforms": [
            {
                "platform": "百度贴吧",
                "items": ["吧友热议话题 A", "吧友热议话题 B"],
                "summary": ["话题更偏社区讨论。", "互动性强，带有持续发酵迹象。"],
                "source_note": "Tieba results from search extraction.",
            },
            {
                "platform": "Bilibili",
                "items": [
                    {"rank": 1, "title": "B站热点 1", "summary": "视频热度高"},
                    {"rank": 2, "title": "B站热点 2"},
                ],
                "summary": "视频与娱乐内容占比较高。",
            },
            {
                "platform": "抖音",
                "items": [
                    {"rank": 1, "title": "抖音热点 1"},
                    {"rank": 2, "title": "抖音热点 2", "summary": "讨论持续升温"},
                ],
                "summary": ["短视频和社会话题并行。"],
                "source_note": "Douyin results from fallback extraction.",
            },
            {
                "platform": "微博",
                "status": "approximate",
                "items": [{"title": "微博热点 1"}],
                "summary": ["当前仅获得近似结果。"],
                "source_note": "Approximate ranking from fallback search.",
            },
            {
                "platform": "小红书",
                "status": "partial",
                "items": [{"title": "小红书热点 1"}],
                "summary": ["生活方式与消费话题更突出。"],
                "source_note": "Partial results from search extraction.",
            },
        ],
        "overall_summary": ["娱乐与社区讨论并行升温。", "跨平台关注点集中在即时事件与情绪表达。"],
        "sources_note": "Bilibili used direct hot tool; other platforms used search or fallback extraction.",
    }


def _sample_content_payload() -> dict[str, object]:
    return {
        "generated_at": "2026-03-21T09:30:45+08:00",
        "content": "\n".join(
            [
                "2026年3月21日热搜",
                "生成时间：2026-03-21 09:30:45 +0800",
                "",
                "【百度贴吧】",
                "1. 吧友热议话题 A",
                "",
                "【Bilibili】",
                "1. 热门视频 A",
                "",
                "【抖音】",
                "1. 短视频热点 A",
                "",
                "【微博】",
                "1. 微博热点 A",
                "",
                "【小红书】",
                "1. 小红书热点 A",
                "",
                "【全局总结】",
                "- 今天的热点以视频、社区讨论和实时事件为主。",
            ]
        ),
    }


def _incomplete_structured_payload() -> dict[str, object]:
    return {
        "generated_at": "2026-03-21T09:30:45+08:00",
        "platforms": [
            {
                "platform": "Bilibili",
                "items": [{"rank": 1, "title": "只采集了 B 站热点"}],
                "summary": ["内容不完整。"],
            }
        ],
    }


def _incomplete_content_payload() -> dict[str, object]:
    return {
        "generated_at": "2026-03-21T09:30:45+08:00",
        "content": "\n".join(
            [
                "2026年3月21日热搜",
                "生成时间：2026-03-21 09:30:45 +0800",
                "",
                "【Bilibili】",
                "1. 只采集了 B 站热点",
            ]
        ),
    }


class DailyHotspotsSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_save_report_module()

    def test_prefers_fileplay_allowlist_directory(self) -> None:
        with _workspace_tempdir() as root:
            primary = root / "workspace"
            preferred = root / "fileplay"
            _write_config(root, [str(primary), str(preferred)])

            result = self.module.save_report(_sample_payload(), repo_root=root)

            self.assertEqual(Path(result["output_dir"]), preferred / "热搜")
            self.assertTrue(Path(result["output_path"]).exists())

    def test_creates_hotspots_directory_when_missing(self) -> None:
        with _workspace_tempdir() as root:
            preferred = root / "fileplay"
            _write_config(root, [str(preferred)])

            output_dir = preferred / "热搜"
            self.assertFalse(output_dir.exists())
            self.module.save_report(_sample_payload(), repo_root=root)

            self.assertTrue(output_dir.exists())
            self.assertTrue(output_dir.is_dir())

    def test_uses_expected_chinese_dated_filename(self) -> None:
        with _workspace_tempdir() as root:
            preferred = root / "fileplay"
            _write_config(root, [str(preferred)])

            result = self.module.save_report(_sample_payload(), repo_root=root)

            self.assertEqual(result["output_file"], "2026年3月21日热搜.txt")

    def test_overwrites_existing_same_day_report(self) -> None:
        with _workspace_tempdir() as root:
            preferred = root / "fileplay"
            _write_config(root, [str(preferred)])
            output_dir = preferred / "热搜"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "2026年3月21日热搜.txt"
            output_path.write_text("old content", encoding="utf-8")

            result = self.module.save_report(_sample_payload(), repo_root=root)
            content = output_path.read_text(encoding="utf-8-sig")

            self.assertEqual(result["output_path"], str(output_path))
            self.assertNotEqual(content, "old content")
            self.assertIn("2026年3月21日热搜", content)
            self.assertIn("【全局总结】", content)

    def test_falls_back_to_first_allowlist_when_fileplay_missing(self) -> None:
        with _workspace_tempdir() as root:
            first = root / "allowed-a"
            second = root / "allowed-b"
            _write_config(root, [str(first), str(second)])

            result = self.module.save_report(_sample_payload(), repo_root=root)

            self.assertEqual(Path(result["output_dir"]), first / "热搜")

    def test_writes_preformatted_content_when_content_field_is_provided(self) -> None:
        with _workspace_tempdir() as root:
            preferred = root / "fileplay"
            _write_config(root, [str(preferred)])

            result = self.module.save_report(_sample_content_payload(), repo_root=root)
            content = Path(result["output_path"]).read_text(encoding="utf-8-sig")

            self.assertIn("2026年3月21日热搜", content)
            self.assertIn("【Bilibili】", content)
            self.assertIn("热门视频 A", content)
            self.assertIn("【全局总结】", content)

    def test_rejects_incomplete_structured_report(self) -> None:
        with _workspace_tempdir() as root:
            preferred = root / "fileplay"
            _write_config(root, [str(preferred)])

            with self.assertRaisesRegex(ValueError, "missing required platforms"):
                self.module.save_report(_incomplete_structured_payload(), repo_root=root)

    def test_rejects_incomplete_preformatted_content(self) -> None:
        with _workspace_tempdir() as root:
            preferred = root / "fileplay"
            _write_config(root, [str(preferred)])

            with self.assertRaisesRegex(ValueError, "missing required platforms"):
                self.module.save_report(_incomplete_content_payload(), repo_root=root)

    def test_writes_utf8_bom_for_windows_friendly_text_files(self) -> None:
        with _workspace_tempdir() as root:
            preferred = root / "fileplay"
            _write_config(root, [str(preferred)])

            result = self.module.save_report(_sample_payload(), repo_root=root)
            raw = Path(result["output_path"]).read_bytes()

            self.assertTrue(raw.startswith(codecs.BOM_UTF8))


if __name__ == "__main__":
    unittest.main()
