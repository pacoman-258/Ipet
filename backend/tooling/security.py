from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
from typing import Iterable


MAX_FILE_BYTES = 2 * 1024 * 1024


class SecurityError(RuntimeError):
    pass


def normalize_file_allowlist(
    file_allowlist: Iterable[str] | None,
    *,
    default_paths: Iterable[str] | None = None,
) -> list[Path]:
    seen: set[str] = set()
    normalized: list[Path] = []
    for raw_path in file_allowlist or []:
        text = str(raw_path or "").strip()
        if not text:
            continue
        try:
            path = Path(text).expanduser().resolve()
        except Exception:
            path = Path(text).expanduser()
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(path)
    if normalized:
        return normalized
    fallback_paths = list(default_paths or [])
    if not fallback_paths:
        return []
    return normalize_file_allowlist(fallback_paths, default_paths=None)


class SecurityPolicy:
    def __init__(self, file_allowlist: list[str], network_allow_domains: list[str]) -> None:
        self.file_allowlist = normalize_file_allowlist(file_allowlist)
        self.network_allow_domains = {d.strip().lower() for d in network_allow_domains if str(d).strip()}

    def ensure_file_path(self, raw_path: str) -> Path:
        if not raw_path or not str(raw_path).strip():
            raise SecurityError("path is empty")
        path_text = str(raw_path).strip()
        candidate = Path(path_text).expanduser()

        if candidate.is_absolute():
            try:
                resolved = candidate.resolve()
            except Exception:
                resolved = candidate
            for allowed in self.file_allowlist:
                try:
                    resolved.relative_to(allowed)
                    return resolved
                except Exception:
                    continue
            raise SecurityError(f"path is not allowed: {resolved}")

        if not self.file_allowlist:
            raise SecurityError("file allowlist is empty")

        matching_candidates: list[Path] = []
        for allowed in self.file_allowlist:
            try:
                resolved = (allowed / candidate).resolve()
            except Exception:
                resolved = allowed / candidate
            try:
                resolved.relative_to(allowed)
            except Exception:
                continue
            matching_candidates.append(resolved)
            if resolved.exists():
                return resolved
        if matching_candidates:
            return matching_candidates[0]
        raise SecurityError(f"path is not allowed: {candidate}")

    def ensure_url(self, raw_url: str) -> str:
        url = str(raw_url or "").strip()
        if not url:
            raise SecurityError("url is empty")
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        if scheme not in {"http", "https"}:
            raise SecurityError(f"unsupported scheme: {scheme}")
        if host not in self.network_allow_domains:
            raise SecurityError(f"domain is not allowed: {host}")
        return url
