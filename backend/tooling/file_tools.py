from __future__ import annotations

import fnmatch
import shutil
from pathlib import Path
from typing import Any

from .security import MAX_FILE_BYTES, SecurityPolicy, SecurityError


class FileTools:
    def __init__(self, policy: SecurityPolicy) -> None:
        self.policy = policy

    def _entry(self, path: Path, *, base: Path | None = None) -> dict[str, Any]:
        try:
            display_path = path.relative_to(base).as_posix() if base is not None else str(path)
        except Exception:
            display_path = str(path)
        item_type = "directory" if path.is_dir() else "file"
        size = 0 if path.is_dir() else path.stat().st_size
        return {
            "name": path.name,
            "path": display_path,
            "type": item_type,
            "is_dir": path.is_dir(),
            "is_file": path.is_file(),
            "size": size,
            "modified_at": int(path.stat().st_mtime),
        }

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
            "type": "file",
            "created": True,
            "overwrote": bool(overwrite and existed_before),
            "bytes_written": len(data),
            "final_size": len(data),
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
            "bytes_read": p.stat().st_size,
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
            "type": "file",
            "bytes_written": len(data),
            "append": append,
            "final_size": final_size,
            "encoding": encoding,
        }

    def move_file(self, src_path: str, dst_path: str) -> dict[str, Any]:
        src = self.policy.ensure_file_path(src_path)
        dst = self.policy.ensure_file_path(dst_path)
        if not src.exists():
            raise FileNotFoundError(str(src))
        if src.is_dir():
            raise IsADirectoryError(str(src))
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)
        return {"from": str(src), "to": str(dst), "moved": True, "type": "file"}

    def copy_file(self, src_path: str, dst_path: str, overwrite: bool = False) -> dict[str, Any]:
        src = self.policy.ensure_file_path(src_path)
        dst = self.policy.ensure_file_path(dst_path)
        if not src.exists():
            raise FileNotFoundError(str(src))
        if src.is_dir():
            raise IsADirectoryError(str(src))
        if dst.exists() and not overwrite:
            raise FileExistsError(str(dst))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return {
            "from": str(src),
            "to": str(dst),
            "copied": True,
            "type": "file",
            "bytes_copied": dst.stat().st_size,
        }

    def delete_file(self, path: str, missing_ok: bool = False) -> dict[str, Any]:
        p = self.policy.ensure_file_path(path)
        if not p.exists():
            if missing_ok:
                return {"path": str(p), "deleted": False, "missing": True}
            raise FileNotFoundError(str(p))
        if p.is_dir():
            raise IsADirectoryError(str(p))
        size = p.stat().st_size
        p.unlink()
        return {"path": str(p), "deleted": True, "type": "file", "bytes_removed": size}

    def stat_path(self, path: str) -> dict[str, Any]:
        p = self.policy.ensure_file_path(path)
        if not p.exists():
            raise FileNotFoundError(str(p))
        return {"path": str(p), "entry": self._entry(p)}

    def list_dir(self, path: str, recursive: bool = False, max_entries: int = 500) -> dict[str, Any]:
        p = self.policy.ensure_file_path(path)
        if not p.exists():
            raise FileNotFoundError(str(p))
        if not p.is_dir():
            raise NotADirectoryError(str(p))

        entries: list[dict[str, Any]] = []
        iterator = p.rglob("*") if recursive else p.iterdir()
        truncated = False
        for item in iterator:
            entries.append(self._entry(item, base=p))
            if len(entries) >= max(1, int(max_entries)):
                truncated = True
                break
        return {"path": str(p), "entries": entries, "count": len(entries), "truncated": truncated}

    def search_files(
        self,
        path: str,
        pattern: str = "*",
        recursive: bool = True,
        max_entries: int = 100,
    ) -> dict[str, Any]:
        p = self.policy.ensure_file_path(path)
        if not p.exists():
            raise FileNotFoundError(str(p))
        if not p.is_dir():
            raise NotADirectoryError(str(p))

        effective_pattern = str(pattern or "*").strip() or "*"
        iterator = p.rglob("*") if recursive else p.iterdir()
        matches: list[dict[str, Any]] = []
        truncated = False
        for item in iterator:
            if not fnmatch.fnmatch(item.name, effective_pattern):
                continue
            matches.append(self._entry(item, base=p))
            if len(matches) >= max(1, int(max_entries)):
                truncated = True
                break
        return {
            "path": str(p),
            "pattern": effective_pattern,
            "entries": matches,
            "count": len(matches),
            "truncated": truncated,
        }
