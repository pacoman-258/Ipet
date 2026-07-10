from __future__ import annotations

import argparse
import html.parser
from pathlib import Path


NOISY_DIRS = {
    ".app-logs",
    ".cache",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".service-logs",
    ".uv-cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
NOISY_FILES = {
    ".DS_Store",
    ".pet_desktop_command.json",
    ".pet_desktop_command.response.json",
    ".pet_desktop_host.heartbeat.json",
    ".tmp_index_inline.js",
    "Todo.txt",
    "pet_config.json",
}
ENTRYPOINTS = ("main.py", "index.html", "settings.html", "backend/app.py")
DATA_DIRS = (
    "data/chat_topics",
    "data/ipet_conversations",
    "data/ipet_memory",
    "data/ipet_task_runs",
    "data/skills/imported",
)
NOISY_PATH_PREFIXES = ("backend/audio_cache", *DATA_DIRS)
LOG_DIRS = (".service-logs", ".app-logs")
ERROR_MARKERS = ("ERROR", "Traceback", "Exception", "CRITICAL", "WARNING")


class _TitleParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.parts.append(data.strip())

    @property
    def title(self) -> str:
        return " ".join(part for part in self.parts if part).strip()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _format_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _iter_files(root: Path):
    for path in root.rglob("*"):
        relative_parts = path.relative_to(root).parts
        relative_path = path.relative_to(root).as_posix()
        if any(part in NOISY_DIRS for part in relative_parts):
            continue
        if any(relative_path == prefix or relative_path.startswith(f"{prefix}/") for prefix in NOISY_PATH_PREFIXES):
            continue
        if path.name in NOISY_FILES or path.name.startswith(".pet_desktop_command.response."):
            continue
        if path.name.startswith("tmp_") and path.suffix == ".log":
            continue
        if path.is_file():
            yield path


def _read_text_prefix(path: Path, max_chars: int = 12000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def _html_title(path: Path) -> str:
    parser = _TitleParser()
    parser.feed(_read_text_prefix(path))
    return parser.title or "(no title)"


def _tail_lines(path: Path, limit: int) -> list[str]:
    lines = _read_text_prefix(path, max_chars=200000).splitlines()
    return [line for line in lines[-limit:] if line.strip()]


def _error_lines(path: Path, limit: int) -> list[str]:
    lines = _read_text_prefix(path, max_chars=200000).splitlines()
    hits = [line.strip() for line in lines if any(marker in line for marker in ERROR_MARKERS)]
    return hits[-limit:]


def build_repo_summary(root: Path, largest_limit: int = 8, recent_limit: int = 8) -> str:
    files = list(_iter_files(root))
    largest = sorted(files, key=lambda path: path.stat().st_size, reverse=True)[:largest_limit]
    recent = sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)[:recent_limit]
    lines = ["## Repo Map", "", "### Entrypoints"]
    for entrypoint in ENTRYPOINTS:
        path = root / entrypoint
        if path.exists():
            lines.append(f"- `{entrypoint}` ({_format_size(path.stat().st_size)})")
    lines.extend(["", "### Largest Files"])
    for path in largest:
        lines.append(f"- `{_relative(path, root)}` ({_format_size(path.stat().st_size)})")
    lines.extend(["", "### Recently Modified Files"])
    for path in recent:
        lines.append(f"- `{_relative(path, root)}`")
    return "\n".join(lines).strip()


def build_reports_summary(root: Path, limit: int = 12) -> str:
    reports_dir = root / "docs" / "reports"
    lines = ["## Recent Reports"]
    if not reports_dir.exists():
        lines.append("- No `docs/reports/` directory found.")
        return "\n".join(lines)
    reports = sorted(reports_dir.glob("*.html"), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]
    if not reports:
        lines.append("- No HTML reports found.")
        return "\n".join(lines)
    for report in reports:
        lines.append(f"- `{_relative(report, root)}`: {_html_title(report)}")
    return "\n".join(lines)


def build_data_summary(root: Path) -> str:
    lines = ["## Local Data"]
    for dirname in DATA_DIRS:
        path = root / dirname
        if not path.exists():
            lines.append(f"- `{dirname}`: missing")
            continue
        files = [item for item in path.rglob("*") if item.is_file()]
        total = sum(item.stat().st_size for item in files)
        newest = max(files, key=lambda item: item.stat().st_mtime, default=None)
        suffix = f", newest `{_relative(newest, root)}`" if newest else ""
        lines.append(f"- `{dirname}`: {len(files)} files, {_format_size(total)}{suffix}")
    return "\n".join(lines)


def build_logs_summary(root: Path, tail_lines: int = 8, error_limit: int = 8) -> str:
    lines = ["## Logs"]
    seen = False
    for dirname in LOG_DIRS:
        log_dir = root / dirname
        if not log_dir.exists():
            continue
        for log_file in sorted(log_dir.glob("*.log"), key=lambda path: path.stat().st_mtime, reverse=True):
            seen = True
            lines.append(f"### `{_relative(log_file, root)}`")
            errors = _error_lines(log_file, error_limit)
            if errors:
                lines.append("Recent error-like lines:")
                lines.extend(f"- {line}" for line in errors)
            tail = _tail_lines(log_file, tail_lines)
            if tail:
                lines.append("Tail:")
                lines.extend(f"- {line}" for line in tail)
    if not seen:
        lines.append("- No log files found in `.service-logs/` or `.app-logs/`.")
    return "\n".join(lines)


def build_summary(root: Path, sections: set[str]) -> str:
    builders = {
        "repo": build_repo_summary,
        "reports": build_reports_summary,
        "data": build_data_summary,
        "logs": build_logs_summary,
    }
    return "\n\n".join(builders[section](root) for section in ("repo", "reports", "data", "logs") if section in sections)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create compact Ipet project summaries for coding agents.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Project root.")
    parser.add_argument("--repo", action="store_true", help="Summarize entrypoints, large files, and recent files.")
    parser.add_argument("--reports", action="store_true", help="Summarize recent HTML reports.")
    parser.add_argument("--data", action="store_true", help="Summarize local data directories by metadata only.")
    parser.add_argument("--logs", action="store_true", help="Summarize local logs by errors and tail snippets.")
    parser.add_argument("--all", action="store_true", help="Include every summary section.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sections = {
        name
        for name in ("repo", "reports", "data", "logs")
        if args.all or getattr(args, name)
    }
    if not sections:
        sections = {"repo", "reports", "data", "logs"}
    print(build_summary(args.root.resolve(), sections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
