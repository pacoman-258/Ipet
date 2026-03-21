from __future__ import annotations

from pathlib import Path

from .models import SkillDiscoveryCandidate


SKILL_MD_NAME = "SKILL.md"
SKILL_JSON_NAME = "skill.json"
SKILL_SOURCE_META_NAME = ".skill_source.json"
IGNORED_DISCOVERY_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".uv-cache",
    ".uv-tools",
    "dist",
    "build",
}
RESOURCE_TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml"}
AUTO_SCRIPT_IGNORED_PARTS = {"__pycache__", "helpers", "validators", "schemas"}


def discover_builtin_candidates(base_dir: Path) -> list[SkillDiscoveryCandidate]:
    candidates: list[SkillDiscoveryCandidate] = []
    if not base_dir.exists():
        return candidates
    for child in sorted(base_dir.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        if not child.joinpath(SKILL_MD_NAME).exists():
            continue
        resolved = child.resolve()
        candidates.append(
            SkillDiscoveryCandidate(
                source_type="builtin",
                package_root=resolved,
                discovery_root=resolved,
            )
        )
    return candidates


def discover_imported_candidates(base_dir: Path) -> list[SkillDiscoveryCandidate]:
    candidates: list[SkillDiscoveryCandidate] = []
    if not base_dir.exists():
        return candidates
    for child in sorted(base_dir.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        _walk_imported_tree(child.resolve(), child.resolve(), candidates)
    return candidates


def _walk_imported_tree(current_dir: Path, package_root: Path, out: list[SkillDiscoveryCandidate]) -> None:
    if current_dir.name in IGNORED_DISCOVERY_DIRS:
        return
    if current_dir.joinpath(SKILL_MD_NAME).exists():
        out.append(
            SkillDiscoveryCandidate(
                source_type="imported",
                package_root=package_root,
                discovery_root=current_dir,
            )
        )
        return
    for child in sorted(current_dir.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        _walk_imported_tree(child.resolve(), package_root, out)
