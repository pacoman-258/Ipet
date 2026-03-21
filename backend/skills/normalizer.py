from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .adapters import detect_adapter_profile, normalize_subdir, resolve_compatibility_mode
from .discovery import RESOURCE_TEXT_SUFFIXES, SKILL_JSON_NAME, SKILL_MD_NAME, SKILL_SOURCE_META_NAME
from .models import SkillManifest, SkillRecord, SkillResourceEntry


_ALLOWED_TOOL_KEYS = ("tool_allowlist", "allowed-tools", "allowed_tools")


def safe_skill_id(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(name or "").strip())
    cleaned = cleaned.strip("._-").lower()
    return cleaned or "skill"



def dedupe_strings(items: Any) -> tuple[str, ...]:
    if items is None:
        return ()
    if isinstance(items, str):
        items = [items]
    seen: set[str] = set()
    out: list[str] = []
    for item in items if isinstance(items, (list, tuple, set)) else []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)



def dedupe_skill_aliases(skill_id: str, *candidates: str) -> tuple[str, ...]:
    canonical = safe_skill_id(skill_id)
    seen: set[str] = set()
    aliases: list[str] = []
    for candidate in candidates:
        alias = safe_skill_id(candidate)
        if not alias or alias == canonical or alias in seen:
            continue
        seen.add(alias)
        aliases.append(alias)
    return tuple(aliases)



def relative_files(root: Path, directory_name: str) -> tuple[str, ...]:
    target = root / directory_name
    if not target.exists() or not target.is_dir():
        return ()
    items: list[str] = []
    for child in sorted(target.rglob("*")):
        if not child.is_file():
            continue
        try:
            items.append(child.relative_to(root).as_posix())
        except ValueError:
            continue
    return tuple(items)



def discover_resource_entries(root_path: Path) -> tuple[SkillResourceEntry, ...]:
    resources: list[SkillResourceEntry] = []
    seen_paths: set[str] = set()

    def _append(path: Path, category: str) -> None:
        if not path.exists() or not path.is_file():
            return
        try:
            relative = path.relative_to(root_path).as_posix()
        except ValueError:
            return
        if relative in seen_paths:
            return
        seen_paths.add(relative)
        try:
            size_bytes = int(path.stat().st_size)
        except Exception:
            size_bytes = 0
        resources.append(
            SkillResourceEntry(
                path=relative,
                category=category,
                absolute_path=path.resolve(),
                size_bytes=size_bytes,
            )
        )

    _append(root_path / SKILL_MD_NAME, "prompt")
    for directory_name, category in (("references", "reference"), ("assets", "asset")):
        directory = root_path / directory_name
        if not directory.exists() or not directory.is_dir():
            continue
        for child in sorted(directory.rglob("*")):
            _append(child, category)
    for child in sorted(root_path.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_file():
            continue
        if child.name in {SKILL_MD_NAME, SKILL_JSON_NAME, SKILL_SOURCE_META_NAME}:
            continue
        if child.suffix.lower() not in RESOURCE_TEXT_SUFFIXES:
            continue
        _append(child, "document")
    return tuple(resources)



def manifest_allowlist_from_sources(frontmatter: dict[str, Any], manifest_payload: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for source in (manifest_payload, frontmatter):
        for key in _ALLOWED_TOOL_KEYS:
            raw = source.get(key) if isinstance(source, dict) else None
            values.extend(dedupe_strings(raw))
    return dedupe_strings(values)



def derive_platform(*, has_manifest: bool, root_path: Path, site_metadata: dict[str, Any]) -> str:
    platform = str(site_metadata.get("source_platform") or "").strip().lower()
    if platform == "clawhub" or (root_path / ".clawhub").exists():
        return "clawhub"
    if has_manifest:
        return "native"
    return "standard"



def derive_capabilities(
    *,
    prompt_body: str,
    resources: tuple[SkillResourceEntry, ...],
    manifest: SkillManifest,
    adapter_profile: str,
) -> tuple[str, ...]:
    capabilities: list[str] = []
    if str(prompt_body or "").strip():
        capabilities.append("prompt")
    if any(entry.category != "prompt" for entry in resources):
        capabilities.append("resources")
    if manifest.scripts:
        capabilities.append("scripts")
    if adapter_profile:
        capabilities.append("adapter")
    return tuple(capabilities)



def first_non_empty_line(text: str) -> str:
    for line in str(text or "").splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned
    return ""



def normalize_skill_record(
    *,
    source_type: str,
    root_path: Path,
    package_root: Path,
    skill_md_path: Path,
    prompt_body: str,
    frontmatter: dict[str, Any],
    metadata: dict[str, Any],
    source_meta: dict[str, Any],
    site_metadata: dict[str, Any],
    manifest: SkillManifest,
    has_manifest: bool,
    resources: tuple[SkillResourceEntry, ...],
    preferred_name: str = "",
) -> SkillRecord:
    strict_frontmatter = source_type == "builtin"
    errors: list[str] = []
    warnings: list[str] = []

    site_name = str(site_metadata.get("package_name") or "").strip()
    inferred_name = preferred_name or site_name or root_path.name
    name = str(frontmatter.get("name") or inferred_name).strip()
    if not str(frontmatter.get("name") or "").strip():
        if strict_frontmatter:
            errors.append("SKILL.md requires name in frontmatter")
        else:
            warnings.append("SKILL.md missing name in frontmatter; using fallback")

    site_description = str(site_metadata.get("description") or site_metadata.get("summary") or "").strip()
    body_description = first_non_empty_line(prompt_body)
    description_fallback = site_description or body_description or name
    description = str(frontmatter.get("description") or description_fallback).strip()
    if not str(frontmatter.get("description") or "").strip():
        if strict_frontmatter:
            errors.append("SKILL.md requires description in frontmatter")
        else:
            warnings.append("SKILL.md missing description in frontmatter; using fallback")

    display_name = str(metadata.get("display_name") or name).strip() or name
    short_description = str(metadata.get("short_description") or description).strip() or description
    source_repo = str(site_metadata.get("source_repo") or source_meta.get("source_repo") or source_meta.get("repo_url") or "").strip()
    source_ref = str(site_metadata.get("source_ref") or source_meta.get("source_ref") or source_meta.get("ref") or "").strip()
    source_subdir = normalize_subdir(
        site_metadata.get("source_subdir") or source_meta.get("source_subdir") or source_meta.get("subdir") or ""
    )
    platform = derive_platform(has_manifest=has_manifest, root_path=root_path, site_metadata=site_metadata)
    skill_id = safe_skill_id(preferred_name or name or root_path.name)
    adapter_profile = detect_adapter_profile(
        source_repo=source_repo,
        source_subdir=source_subdir,
        skill_id=skill_id,
        site_metadata=site_metadata,
    )
    compatibility_mode = resolve_compatibility_mode(
        platform=platform,
        has_manifest=has_manifest,
        adapter_profile=adapter_profile,
        source_repo=source_repo,
        source_subdir=source_subdir,
        resource_count=len(resources),
    )

    aliases = dedupe_skill_aliases(skill_id, root_path.name, name, display_name, site_name)
    return SkillRecord(
        skill_id=skill_id,
        name=name or root_path.name,
        description=description,
        source_type=source_type,
        root_path=root_path,
        skill_md_path=skill_md_path,
        prompt_body=prompt_body,
        aliases=aliases,
        manifest=manifest,
        has_manifest=has_manifest,
        ok=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        references=relative_files(root_path, "references"),
        assets=relative_files(root_path, "assets"),
        resources=resources,
        display_name=display_name,
        short_description=short_description,
        has_openai_metadata=bool(metadata),
        source_repo=source_repo,
        source_ref=source_ref,
        source_subdir=source_subdir,
        compatibility_mode=compatibility_mode,
        adapter_profile=adapter_profile,
        platform=platform,
        package_root=package_root,
        discovery_root=root_path,
        capabilities=derive_capabilities(
            prompt_body=prompt_body,
            resources=resources,
            manifest=manifest,
            adapter_profile=adapter_profile,
        ),
        site_metadata=dict(site_metadata),
    )
