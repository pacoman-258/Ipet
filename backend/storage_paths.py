from __future__ import annotations

from pathlib import Path


def hermes_root(root_dir: Path) -> Path:
    return Path(root_dir) / "Hermes"


def hermes_skills_dir(root_dir: Path) -> Path:
    return hermes_root(root_dir) / "skills" / "imported"


def hermes_mcp_dir(root_dir: Path) -> Path:
    return hermes_root(root_dir) / "mcp"


def legacy_third_party_skills_dir(root_dir: Path) -> Path:
    return Path(root_dir) / "third_party_skills"


def legacy_third_party_mcp_dir(root_dir: Path) -> Path:
    return Path(root_dir) / "third_party_mcp"
