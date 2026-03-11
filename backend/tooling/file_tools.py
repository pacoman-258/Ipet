from __future__ import annotations

from pathlib import Path
from typing import Any

from .security import MAX_FILE_BYTES, SecurityPolicy, SecurityError


class FileTools:
    def __init__(self, policy: SecurityPolicy) -> None:
        self.policy = policy

    def _read_text(self, path: Path, encoding: str) -> str:
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise SecurityError(f"file too large (> {MAX_FILE_BYTES} bytes)")
        return raw.decode(encoding)

    def create_file(
        self,
        path: str,
        content: str = "",
        encoding: str = "utf-8",
        create_dirs: bool = True,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        p = self.policy.ensure_file_path(path)
        existed_before = p.exists()
        if existed_before and not overwrite:
            raise FileExistsError(str(p))
        if existed_before and p.is_dir():
            raise IsADirectoryError(str(p))
        if create_dirs:
            p.parent.mkdir(parents=True, exist_ok=True)

        data = (content or "").encode(encoding)
        if len(data) > MAX_FILE_BYTES:
            raise SecurityError(f"content too large (> {MAX_FILE_BYTES} bytes)")

        with p.open("wb") as f:
            f.write(data)
        return {
            "path": str(p),
            "created": True,
            "overwrote": bool(overwrite and existed_before),
            "bytes_written": len(data),
            "encoding": encoding,
        }

    def read_file(self, path: str, encoding: str = "utf-8") -> dict[str, Any]:
        p = self.policy.ensure_file_path(path)
        if not p.exists():
            raise FileNotFoundError(str(p))
        if p.is_dir():
            raise IsADirectoryError(str(p))
        return {
            "path": str(p),
            "content": self._read_text(p, encoding),
            "encoding": encoding,
        }

    def write_file(
        self,
        path: str,
        content: str,
        encoding: str = "utf-8",
        create_dirs: bool = True,
        append: bool = True,
    ) -> dict[str, Any]:
        p = self.policy.ensure_file_path(path)
        if create_dirs:
            p.parent.mkdir(parents=True, exist_ok=True)
        data = (content or "").encode(encoding)
        if len(data) > MAX_FILE_BYTES:
            raise SecurityError(f"content too large (> {MAX_FILE_BYTES} bytes)")
        existing = p.stat().st_size if p.exists() else 0
        final_size = existing + len(data) if append else len(data)
        if final_size > MAX_FILE_BYTES:
            raise SecurityError(f"final file too large (> {MAX_FILE_BYTES} bytes)")

        mode = "ab" if append else "wb"
        with p.open(mode) as f:
            f.write(data)
        return {
            "path": str(p),
            "bytes_written": len(data),
            "append": append,
            "final_size": final_size,
        }

    def move_file(self, src_path: str, dst_path: str) -> dict[str, Any]:
        src = self.policy.ensure_file_path(src_path)
        dst = self.policy.ensure_file_path(dst_path)
        if not src.exists():
            raise FileNotFoundError(str(src))
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)
        return {"from": str(src), "to": str(dst)}

    def list_dir(self, path: str, recursive: bool = False, max_entries: int = 500) -> dict[str, Any]:
        p = self.policy.ensure_file_path(path)
        if not p.exists():
            raise FileNotFoundError(str(p))
        if not p.is_dir():
            raise NotADirectoryError(str(p))

        entries: list[str] = []
        iterator = p.rglob("*") if recursive else p.iterdir()
        for item in iterator:
            rel = item.relative_to(p).as_posix()
            entries.append(rel + ("/" if item.is_dir() else ""))
            if len(entries) >= max(1, int(max_entries)):
                break
        return {"path": str(p), "entries": entries, "count": len(entries)}
