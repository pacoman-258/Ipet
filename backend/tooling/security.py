from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


MAX_FILE_BYTES = 2 * 1024 * 1024


class SecurityError(RuntimeError):
    pass


class SecurityPolicy:
    def __init__(self, file_allowlist: list[str], network_allow_domains: list[str]) -> None:
        self.file_allowlist = [Path(p).resolve() for p in file_allowlist if str(p).strip()]
        self.network_allow_domains = {d.strip().lower() for d in network_allow_domains if str(d).strip()}

    def ensure_file_path(self, raw_path: str) -> Path:
        if not raw_path or not str(raw_path).strip():
            raise SecurityError("path is empty")
        resolved = Path(raw_path).expanduser().resolve()
        for allowed in self.file_allowlist:
            try:
                resolved.relative_to(allowed)
                return resolved
            except Exception:
                continue
        raise SecurityError(f"path is not allowed: {resolved}")

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
