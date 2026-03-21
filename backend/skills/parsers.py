from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from .discovery import SKILL_SOURCE_META_NAME


_FRONTMATTER_RE = re.compile(r"^\s*---\s*\r?\n(.*?)\r?\n?---\s*\r?\n?(.*)$", re.DOTALL)
_CLAWHUB_METADATA_FILES = (
    ".clawhub/origin.json",
    ".clawhub/origin.yaml",
    ".clawhub/origin.yml",
    ".clawhub/metadata.json",
    ".clawhub/metadata.yaml",
    ".clawhub/metadata.yml",
    "metadata.openclaw",
    "metadata.openclaw.json",
    "metadata.openclaw.yaml",
    "metadata.openclaw.yml",
)


def parse_frontmatter(raw_text: str) -> tuple[dict[str, Any], str]:
    text = str(raw_text or "")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.strip()
    frontmatter_text = match.group(1)
    body = match.group(2).strip()
    try:
        payload = yaml.safe_load(frontmatter_text) or {}
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}, body


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_source_metadata(root_path: Path) -> dict[str, Any]:
    meta_path = root_path / SKILL_SOURCE_META_NAME
    if not meta_path.exists():
        return {}
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_source_metadata(root_path: Path, metadata: dict[str, Any]) -> None:
    payload = {str(key): value for key, value in dict(metadata or {}).items() if value not in (None, "")}
    if not payload:
        return
    (root_path / SKILL_SOURCE_META_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_clawhub_metadata(root_path: Path) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for relative_path in _CLAWHUB_METADATA_FILES:
        data = _load_structured_file(root_path / relative_path)
        if not isinstance(data, dict):
            continue
        merged = _deep_merge(merged, data)
    if merged:
        merged.setdefault("source_platform", "clawhub")
    return merged


def build_site_metadata(
    *,
    root_path: Path,
    source_meta: dict[str, Any],
    manifest_payload: dict[str, Any],
    clawhub_meta: dict[str, Any],
) -> dict[str, Any]:
    combined = {
        "source_meta": dict(source_meta or {}),
        "manifest": dict(manifest_payload or {}),
        "clawhub": dict(clawhub_meta or {}),
    }
    metadata: dict[str, Any] = {}

    platform = _first_value(
        combined,
        "source_platform",
        "platform",
        "registry",
        "provider",
        "site",
    )
    if not platform:
        platform = _infer_platform_from_payload(combined)
    if platform:
        metadata["source_platform"] = str(platform).strip().lower()

    for target_key, aliases in (
        ("package_name", ("package_name", "package", "slug", "skill_slug", "skill_name", "name")),
        ("version", ("version", "package_version")),
        ("author", ("author", "author_name", "publisher", "creator", "owner")),
        ("source_repo", ("source_repo", "repo_url", "repository", "upstream_repo", "git_url")),
        ("source_url", ("source_url", "url", "source_page_url", "homepage")),
        ("source_ref", ("source_ref", "ref", "git_ref", "version_ref")),
        ("source_subdir", ("source_subdir", "subdir", "source_path", "path")),
    ):
        value = _first_value(combined, *aliases)
        if value not in (None, ""):
            metadata[target_key] = str(value).strip()

    if clawhub_meta:
        metadata["origin_path"] = str((root_path / ".clawhub").resolve()) if (root_path / ".clawhub").exists() else str(root_path.resolve())
    return metadata


def _load_structured_file(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".json" or path.name.endswith(".openclaw"):
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            return payload
    try:
        payload = yaml.safe_load(text) or {}
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _iter_nested_values(value: Any):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key, nested
            yield from _iter_nested_values(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_nested_values(item)


def _first_value(payload: dict[str, Any], *aliases: str) -> Any:
    wanted = {alias.lower() for alias in aliases}
    for key, value in _iter_nested_values(payload):
        if str(key).strip().lower() in wanted and value not in (None, ""):
            return value
    return None


def _infer_platform_from_payload(payload: dict[str, Any]) -> str:
    haystacks: list[str] = []
    for value in payload.values():
        if isinstance(value, dict):
            haystacks.append(json.dumps(value, ensure_ascii=False).lower())
    joined = "\n".join(haystacks)
    if "clawhub" in joined or "openclaw" in joined:
        return "clawhub"
    if "skillsmp" in joined:
        return "skillsmp"
    return ""
