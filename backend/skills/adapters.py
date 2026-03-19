from __future__ import annotations

from pathlib import Path


PPTX_ADAPTER_PROFILE = "anthropics.skills.pptx"
ANTHROPIC_SKILLS_REPO = "https://github.com/anthropics/skills"


def normalize_repo_url(repo_url: str) -> str:
    text = str(repo_url or "").strip()
    if not text:
        return ""
    if text.endswith(".git"):
        text = text[:-4]
    return text.rstrip("/").lower()


def normalize_subdir(subdir: str) -> str:
    text = str(subdir or "").replace("\\", "/").strip().strip("/")
    return "/".join(part for part in text.split("/") if part and part != ".")


def detect_adapter_profile(source_repo: str = "", source_subdir: str = "") -> str:
    repo = normalize_repo_url(source_repo)
    subdir = normalize_subdir(source_subdir)
    if repo == normalize_repo_url(ANTHROPIC_SKILLS_REPO) and subdir == "skills/pptx":
        return PPTX_ADAPTER_PROFILE
    return ""


def resolve_compatibility_mode(
    *,
    has_manifest: bool,
    adapter_profile: str = "",
    source_repo: str = "",
    source_subdir: str = "",
    resource_count: int = 0,
) -> str:
    if adapter_profile:
        return "standard_adapter"
    if has_manifest:
        return "native"
    if normalize_repo_url(source_repo) or normalize_subdir(source_subdir) or resource_count > 0:
        return "standard_prompt"
    return "native"


def safe_relative_subdir(root: Path, subdir: str) -> Path:
    normalized = normalize_subdir(subdir)
    if not normalized:
        return root.resolve()
    candidate = (root / normalized).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError(f"subdir escapes repository root: {subdir}")
    return candidate
