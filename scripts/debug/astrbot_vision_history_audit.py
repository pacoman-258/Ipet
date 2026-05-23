#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable


TARGET_ID = "ipet-vision-analysis"
DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_FILES = 20000
TEXT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".toml",
}
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run audit for AstrBot history tied to ipet-vision-analysis. "
            "By default this only reports candidates and does not delete anything."
        )
    )
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="AstrBot data/root directory to scan. Can be passed more than once.",
    )
    parser.add_argument(
        "--target",
        default=TARGET_ID,
        help=f"Reserved session/user id to find. Default: {TARGET_ID}",
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=DEFAULT_MAX_FILE_BYTES,
        help="Skip content scanning for files larger than this many bytes.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=DEFAULT_MAX_FILES,
        help="Stop after visiting this many files per root.",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete conservatively matched candidate files. Requires --confirm-target.",
    )
    parser.add_argument(
        "--confirm-target",
        default="",
        help=f"Must equal {TARGET_ID} before --delete can remove candidate files.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    roots = discover_roots(repo_root, args.root)
    report = audit_roots(
        roots,
        target=str(args.target or TARGET_ID).strip() or TARGET_ID,
        max_file_bytes=max(0, int(args.max_file_bytes)),
        max_files=max(1, int(args.max_files)),
    )
    report["dry_run"] = not bool(args.delete)
    report["delete_requested"] = bool(args.delete)
    if args.delete:
        report["delete_results"] = delete_candidates(
            report["candidates"],
            target=report["target"],
            confirm_target=str(args.confirm_target or ""),
        )
    else:
        report["delete_hint"] = (
            f"Re-run with --delete --confirm-target {report['target']} to remove only "
            "candidate files whose filename contains the target id. Database rows and "
            "JSON internals are never modified by this helper."
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def discover_roots(repo_root: Path, explicit_roots: Iterable[str]) -> list[Path]:
    roots: list[Path] = []
    for value in explicit_roots:
        text = str(value or "").strip()
        if text:
            roots.append(Path(text).expanduser())
    if roots:
        return _dedupe_paths(roots)

    env_candidates = [
        os.environ.get("ASTRBOT_DATA_DIR"),
        os.environ.get("ASTRBOT_HOME"),
        os.environ.get("ASTRBOT_ROOT"),
        os.environ.get("IPET_ASTRBOT_DATA_DIR"),
    ]
    common_candidates = [
        repo_root / "AstrBot",
        repo_root / "data" / "astrbot",
        repo_root / "data",
        repo_root.parent / "AstrBot",
        Path.home() / ".astrbot",
        Path.home() / "AstrBot",
    ]
    for value in env_candidates:
        if value:
            roots.append(Path(value).expanduser())
    roots.extend(common_candidates)
    return _dedupe_paths(path for path in roots if path.exists())


def audit_roots(
    roots: Iterable[Path],
    *,
    target: str,
    max_file_bytes: int,
    max_files: int,
) -> dict[str, Any]:
    checked_roots: list[str] = []
    missing_roots: list[str] = []
    candidates: list[dict[str, Any]] = []
    totals = {
        "candidate_files": 0,
        "candidate_size_bytes": 0,
        "target_occurrences": 0,
        "message_count": 0,
        "image_url_count": 0,
        "char_count": 0,
    }

    for root in roots:
        root = root.expanduser()
        if not root.exists():
            missing_roots.append(str(root))
            continue
        checked_roots.append(str(root))
        for path in iter_files(root, max_files=max_files):
            candidate = audit_path(path, target=target, max_file_bytes=max_file_bytes)
            if not candidate:
                continue
            candidates.append(candidate)
            totals["candidate_files"] += 1
            totals["candidate_size_bytes"] += int(candidate.get("size_bytes") or 0)
            totals["target_occurrences"] += int(candidate.get("target_occurrences") or 0)
            totals["message_count"] += int(candidate.get("message_count") or 0)
            totals["image_url_count"] += int(candidate.get("image_url_count") or 0)
            totals["char_count"] += int(candidate.get("char_count") or 0)

    return {
        "target": target,
        "checked_roots": checked_roots,
        "missing_roots": missing_roots,
        "candidates": candidates,
        "totals": totals,
        "notes": [
            "Default mode is dry-run and read-only.",
            "SQLite findings are reported as candidate rows/tables; this helper does not modify database rows.",
            "If no roots are found, pass --root /path/to/AstrBot/data or set ASTRBOT_DATA_DIR.",
        ],
    }


def iter_files(root: Path, *, max_files: int) -> Iterable[Path]:
    visited = 0
    if root.is_file():
        yield root
        return
    for path in root.rglob("*"):
        if visited >= max_files:
            return
        if not path.is_file():
            continue
        visited += 1
        yield path


def audit_path(path: Path, *, target: str, max_file_bytes: int) -> dict[str, Any] | None:
    try:
        size_bytes = path.stat().st_size
    except OSError:
        return None
    target_in_name = target.casefold() in path.name.casefold()
    suffix = path.suffix.casefold()
    if suffix in SQLITE_SUFFIXES and _looks_like_sqlite(path):
        sqlite_candidate = audit_sqlite(path, target=target, size_bytes=size_bytes)
        if sqlite_candidate:
            return sqlite_candidate
    if suffix not in TEXT_SUFFIXES and not target_in_name:
        return None
    if size_bytes > max_file_bytes:
        if not target_in_name:
            return None
        return {
            "path": str(path),
            "kind": "filename",
            "size_bytes": size_bytes,
            "target_occurrences": 0,
            "message_count": 0,
            "image_url_count": 0,
            "char_count": 0,
            "delete_supported": True,
            "note": "Filename matches target, but file content was not scanned because it exceeds --max-file-bytes.",
        }
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    target_occurrences = text.casefold().count(target.casefold())
    if not target_occurrences and not target_in_name:
        return None
    stats = analyze_text_payload(text, suffix=suffix)
    return {
        "path": str(path),
        "kind": "text",
        "size_bytes": size_bytes,
        "target_occurrences": target_occurrences,
        "message_count": stats["message_count"],
        "image_url_count": stats["image_url_count"],
        "char_count": stats["char_count"],
        "delete_supported": target_in_name,
        "note": "Delete support is limited to whole files whose filename contains the target id.",
    }


def analyze_text_payload(text: str, *, suffix: str) -> dict[str, int]:
    stats = {
        "message_count": 0,
        "image_url_count": text.count("image_url") + text.count("data:image"),
        "char_count": len(text),
    }
    if suffix == ".json":
        try:
            obj = json.loads(text)
        except Exception:
            return stats
        json_stats = analyze_json_value(obj)
        stats.update(json_stats)
    elif suffix == ".jsonl":
        message_count = 0
        image_url_count = 0
        char_count = 0
        for line in text.splitlines():
            try:
                obj = json.loads(line)
            except Exception:
                continue
            item_stats = analyze_json_value(obj)
            message_count += item_stats["message_count"]
            image_url_count += item_stats["image_url_count"]
            char_count += item_stats["char_count"]
        stats.update(
            {
                "message_count": message_count,
                "image_url_count": image_url_count,
                "char_count": char_count,
            }
        )
    return stats


def analyze_json_value(value: Any) -> dict[str, int]:
    stats = {"message_count": 0, "image_url_count": 0, "char_count": 0}
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            keys = {str(key).casefold() for key in item.keys()}
            if keys.intersection({"role", "content", "message", "messages", "sender"}):
                if any(str(key).casefold() in {"role", "content", "message", "sender"} for key in item.keys()):
                    stats["message_count"] += 1
            for key, nested in item.items():
                key_text = str(key).casefold()
                if "image_url" in key_text:
                    stats["image_url_count"] += 1
                stack.append(nested)
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str):
            stats["char_count"] += len(item)
            lowered = item.casefold()
            stats["image_url_count"] += lowered.count("image_url") + lowered.count("data:image")
    return stats


def audit_sqlite(path: Path, *, target: str, size_bytes: int) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {
            "path": str(path),
            "kind": "sqlite_unreadable",
            "size_bytes": size_bytes,
            "target_occurrences": 0,
            "message_count": 0,
            "image_url_count": 0,
            "char_count": 0,
            "delete_supported": False,
            "note": f"Could not open SQLite database read-only: {exc}",
        }
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
            ).fetchall()
        ]
        for table in tables:
            columns = conn.execute(f"pragma table_info({_quote_identifier(table)})").fetchall()
            text_columns = [row[1] for row in columns if _is_textish_sqlite_type(str(row[2] or ""))]
            for column in text_columns:
                query = (
                    f"select rowid, {_quote_identifier(column)} from {_quote_identifier(table)} "
                    f"where {_quote_identifier(column)} like ? limit 20"
                )
                for rowid, value in conn.execute(query, (f"%{target}%",)).fetchall():
                    text = str(value or "")
                    matches.append(
                        {
                            "table": table,
                            "column": column,
                            "rowid": rowid,
                            "target_occurrences": text.casefold().count(target.casefold()),
                            "image_url_count": text.count("image_url") + text.count("data:image"),
                            "char_count": len(text),
                        }
                    )
    except sqlite3.Error as exc:
        return {
            "path": str(path),
            "kind": "sqlite_error",
            "size_bytes": size_bytes,
            "target_occurrences": 0,
            "message_count": 0,
            "image_url_count": 0,
            "char_count": 0,
            "delete_supported": False,
            "note": f"SQLite scan failed: {exc}",
        }
    finally:
        conn.close()
    if not matches:
        return None
    return {
        "path": str(path),
        "kind": "sqlite",
        "size_bytes": size_bytes,
        "target_occurrences": sum(int(item["target_occurrences"]) for item in matches),
        "message_count": len(matches),
        "image_url_count": sum(int(item["image_url_count"]) for item in matches),
        "char_count": sum(int(item["char_count"]) for item in matches),
        "matched_records": matches,
        "delete_supported": False,
        "note": "SQLite rows are reported only. This helper will not mutate database internals.",
    }


def delete_candidates(candidates: list[dict[str, Any]], *, target: str, confirm_target: str) -> list[dict[str, Any]]:
    if confirm_target != target:
        return [
            {
                "deleted": False,
                "error": f"--confirm-target must equal {target}",
            }
        ]
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        path = Path(str(candidate.get("path") or ""))
        result = {"path": str(path), "deleted": False}
        if not candidate.get("delete_supported"):
            result["skipped"] = "delete_not_supported_for_this_candidate"
        elif not path.is_file():
            result["skipped"] = "candidate_is_not_a_file"
        elif target.casefold() not in path.name.casefold():
            result["skipped"] = "filename_does_not_contain_target"
        else:
            try:
                path.unlink()
                result["deleted"] = True
            except OSError as exc:
                result["error"] = str(exc)
        results.append(result)
    return results


def _looks_like_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _is_textish_sqlite_type(type_name: str) -> bool:
    normalized = type_name.strip().upper()
    return not normalized or any(token in normalized for token in ("TEXT", "CHAR", "CLOB", "JSON", "VARCHAR"))


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        resolved = str(path.expanduser())
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path.expanduser())
    return result


if __name__ == "__main__":
    sys.exit(main())
