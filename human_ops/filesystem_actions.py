from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from brain.contracts import action_names, validate_action_arguments

from .approvals import ReviewableProposal


FILESYSTEM_ACTIONS = frozenset(action_names(("file",)))

MAX_READ_BYTES = 1_000_000
MAX_WRITE_BYTES = 1_000_000
MAX_LIST_ENTRIES = 200

_BLOCKED_PARTS = {".git", ".venv", "__pycache__"}
_BLOCKED_NAMES = {".env", ".env.local", ".env.production", "pet_config.json"}


class FilesystemActionError(RuntimeError):
    """Raised when a bounded Human Ops filesystem action is unsafe or invalid."""


@dataclass(frozen=True)
class FilesystemPolicy:
    allowed_roots: tuple[Path, ...]
    max_read_bytes: int = MAX_READ_BYTES
    max_write_bytes: int = MAX_WRITE_BYTES
    max_list_entries: int = MAX_LIST_ENTRIES

    def __post_init__(self) -> None:
        roots: list[Path] = []
        for value in self.allowed_roots:
            path = Path(value).expanduser().resolve(strict=False)
            if path not in roots:
                roots.append(path)
        if not roots:
            raise FilesystemActionError("Human Ops filesystem requires at least one allowed root.")
        object.__setattr__(self, "allowed_roots", tuple(roots))
        object.__setattr__(self, "max_read_bytes", _bounded_limit(self.max_read_bytes, MAX_READ_BYTES))
        object.__setattr__(self, "max_write_bytes", _bounded_limit(self.max_write_bytes, MAX_WRITE_BYTES))
        object.__setattr__(self, "max_list_entries", _bounded_limit(self.max_list_entries, MAX_LIST_ENTRIES))

    def resolve(self, raw_path: Any, *, must_exist: bool = False) -> Path:
        text = str(raw_path or "").strip()
        if not text:
            raise FilesystemActionError("filesystem action requires a non-empty path.")
        if "\x00" in text:
            raise FilesystemActionError("filesystem path contains a NUL byte.")
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = self.allowed_roots[0] / candidate
        if candidate.is_symlink():
            raise FilesystemActionError("final filesystem path must not be a symbolic link.")
        resolved = candidate.resolve(strict=False)
        root = next((root for root in self.allowed_roots if resolved == root or root in resolved.parents), None)
        if root is None:
            raise FilesystemActionError("filesystem path is outside the configured allowed roots.")
        self._check_blocked_path(resolved)
        if must_exist and not candidate.exists():
            raise FilesystemActionError(f"filesystem path does not exist: {resolved}")
        if not candidate.exists():
            parent = candidate.parent
            if not parent.exists() or not parent.is_dir():
                raise FilesystemActionError(f"filesystem parent directory does not exist: {parent.resolve(strict=False)}")
            parent_resolved = parent.resolve(strict=True)
            if not (parent_resolved == root or root in parent_resolved.parents):
                raise FilesystemActionError("filesystem parent directory is outside the configured allowed roots.")
        return resolved

    def root_for(self, path: Path) -> Path:
        return next(root for root in self.allowed_roots if path == root or root in path.parents)

    @staticmethod
    def _check_blocked_path(path: Path) -> None:
        if any(part in _BLOCKED_PARTS for part in path.parts) or path.name in _BLOCKED_NAMES:
            raise FilesystemActionError("filesystem path is reserved or contains protected local state.")


def _bounded_limit(value: Any, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(1, min(number, fallback))


def _args(proposal: ReviewableProposal) -> dict[str, Any]:
    value = proposal.payload.get("arguments")
    return dict(value) if isinstance(value, dict) else {}


def validate_filesystem_action(action_type: str, arguments: dict[str, Any] | None = None) -> tuple[bool, str]:
    action = str(action_type or "").strip()
    args = arguments if isinstance(arguments, dict) else {}
    if action not in FILESYSTEM_ACTIONS:
        return False, action or "unknown"
    valid, reason = validate_action_arguments(action, args)
    if not valid:
        return False, reason
    if action == "file_read":
        encoding = str(args.get("encoding") or "utf-8").strip().lower()
        if encoding != "utf-8":
            return False, "file_read only supports utf-8"
    if action == "file_list" and "max_entries" in args:
        try:
            if int(args["max_entries"]) < 1:
                return False, "file_list max_entries must be positive"
        except (TypeError, ValueError):
            return False, "file_list max_entries must be an integer"
    return True, ""


def filesystem_action_label(action_type: str, arguments: dict[str, Any] | None = None) -> str:
    action = str(action_type or "").strip()
    args = arguments if isinstance(arguments, dict) else {}
    path = str(args.get("path") or "").strip()
    if action == "file_list":
        return f"列出目录 {path}"
    if action == "file_read":
        return f"读取文件 {path}"
    if action == "file_write":
        content = str(args.get("content") or "")
        preview = content[:80].replace("\n", "\\n")
        if len(content) > 80:
            preview += "..."
        overwrite = "（覆盖已有文件）" if args.get("overwrite") else "（仅新建文件）"
        return f"写入文件 {path}，{len(content.encode('utf-8'))} 字节{overwrite}：{preview}"
    if action == "file_mkdir":
        return f"创建目录 {path}"
    if action == "file_copy":
        return f"复制 {args.get('source', '')} 到 {args.get('destination', '')}"
    if action == "file_move":
        return f"移动 {args.get('source', '')} 到 {args.get('destination', '')}"
    if action == "file_delete":
        return f"删除 {path}（不递归）"
    return action or "文件动作"


def filesystem_action_success_field(action_type: str) -> str:
    return {
        "file_list": "listed",
        "file_read": "read",
        "file_write": "written",
        "file_mkdir": "directory_created",
        "file_copy": "copied",
        "file_move": "moved",
        "file_delete": "deleted",
    }.get(str(action_type or "").strip(), "")


def _ensure_approved(proposal: ReviewableProposal) -> tuple[str, dict[str, Any]]:
    if proposal.proposal_type != "act" or not proposal.approved:
        raise FilesystemActionError("filesystem action requires an approved Human Ops proposal.")
    action_type = str(proposal.payload.get("action_type") or "").strip()
    arguments = _args(proposal)
    supported, reason = validate_filesystem_action(action_type, arguments)
    if not supported:
        raise FilesystemActionError(reason)
    return action_type, arguments


def _check_regular_file(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise FilesystemActionError(f"{label} must not be a symbolic link.")
    if not path.is_file():
        raise FilesystemActionError(f"{label} must be a regular file: {path}")


def _list_directory(path: Path, policy: FilesystemPolicy, arguments: dict[str, Any]) -> dict[str, Any]:
    if not path.is_dir():
        raise FilesystemActionError(f"file_list target is not a directory: {path}")
    try:
        requested = int(arguments.get("max_entries", policy.max_list_entries))
    except (TypeError, ValueError):
        requested = policy.max_list_entries
    limit = min(max(1, requested), policy.max_list_entries)
    entries: list[dict[str, Any]] = []
    for entry in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
        if len(entries) >= limit:
            break
        if entry.is_symlink():
            entries.append({"name": entry.name, "path": str(entry), "symlink": True})
            continue
        item = {"name": entry.name, "path": str(entry), "is_dir": entry.is_dir(), "is_file": entry.is_file()}
        if entry.is_file():
            item["size"] = entry.stat().st_size
        entries.append(item)
    return {"listed": True, "path": str(path), "entries": entries, "count": len(entries), "limit": limit}


def _read_file(path: Path, policy: FilesystemPolicy) -> dict[str, Any]:
    _check_regular_file(path, label="file_read target")
    size = path.stat().st_size
    if size > policy.max_read_bytes:
        raise FilesystemActionError(f"file_read target exceeds {policy.max_read_bytes} bytes: {path}")
    try:
        content = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FilesystemActionError(f"file_read only supports valid UTF-8 text: {path}") from exc
    return {"read": True, "path": str(path), "content": content, "bytes": size, "encoding": "utf-8"}


def _write_file(path: Path, arguments: dict[str, Any], policy: FilesystemPolicy) -> dict[str, Any]:
    if path.exists() and path.is_dir():
        raise FilesystemActionError(f"file_write target is a directory: {path}")
    if path.exists() and not arguments.get("overwrite", False):
        raise FilesystemActionError("file_write refuses to overwrite an existing file without overwrite=true.")
    content = str(arguments.get("content") or "")
    data = content.encode("utf-8")
    if len(data) > policy.max_write_bytes:
        raise FilesystemActionError(f"file_write content exceeds {policy.max_write_bytes} bytes.")
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.ipet-", delete=False) as handle:
            temp_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass
    return {"written": True, "path": str(path), "bytes": len(data), "overwrote": bool(arguments.get("overwrite"))}


def _mkdir(path: Path) -> dict[str, Any]:
    if path.exists():
        raise FilesystemActionError(f"file_mkdir target already exists: {path}")
    path.mkdir()
    return {"directory_created": True, "path": str(path)}


def _copy_file(source: Path, destination: Path, policy: FilesystemPolicy) -> dict[str, Any]:
    _check_regular_file(source, label="file_copy source")
    if destination.exists():
        raise FilesystemActionError(f"file_copy refuses to overwrite destination: {destination}")
    size = source.stat().st_size
    if size > policy.max_write_bytes:
        raise FilesystemActionError(f"file_copy source exceeds {policy.max_write_bytes} bytes: {source}")
    shutil.copyfile(source, destination)
    return {"copied": True, "source": str(source), "destination": str(destination), "bytes": size}


def _move_file(source: Path, destination: Path) -> dict[str, Any]:
    _check_regular_file(source, label="file_move source")
    if destination.exists():
        raise FilesystemActionError(f"file_move refuses to overwrite destination: {destination}")
    os.rename(source, destination)
    return {"moved": True, "source": str(source), "destination": str(destination)}


def _delete(path: Path, policy: FilesystemPolicy) -> dict[str, Any]:
    if path in policy.allowed_roots:
        raise FilesystemActionError("file_delete cannot delete an allowed root.")
    if path.is_symlink():
        raise FilesystemActionError("file_delete refuses symbolic links.")
    if path.is_dir():
        try:
            path.rmdir()
        except OSError as exc:
            raise FilesystemActionError("file_delete only removes empty directories; recursive deletion is disabled.") from exc
        return {"deleted": True, "path": str(path), "kind": "directory"}
    if not path.is_file():
        raise FilesystemActionError(f"file_delete target does not exist or is not a regular file: {path}")
    path.unlink()
    return {"deleted": True, "path": str(path), "kind": "file"}


def _execute_filesystem_action_sync(proposal: ReviewableProposal, policy: FilesystemPolicy) -> dict[str, Any]:
    action_type, arguments = _ensure_approved(proposal)
    if action_type == "file_list":
        path = policy.resolve(arguments.get("path"), must_exist=True)
        return _list_directory(path, policy, arguments)
    if action_type == "file_read":
        return _read_file(policy.resolve(arguments.get("path"), must_exist=True), policy)
    if action_type == "file_write":
        return _write_file(policy.resolve(arguments.get("path")), arguments, policy)
    if action_type == "file_mkdir":
        return _mkdir(policy.resolve(arguments.get("path")))
    if action_type in {"file_copy", "file_move"}:
        source = policy.resolve(arguments.get("source"), must_exist=True)
        destination = policy.resolve(arguments.get("destination"))
        if action_type == "file_copy":
            return _copy_file(source, destination, policy)
        return _move_file(source, destination)
    if action_type == "file_delete":
        return _delete(policy.resolve(arguments.get("path"), must_exist=True), policy)
    raise FilesystemActionError(f"unsupported filesystem action: {action_type}")


async def execute_filesystem_action(
    proposal: ReviewableProposal,
    *,
    allowed_roots: Iterable[str | Path],
    max_read_bytes: int = MAX_READ_BYTES,
    max_write_bytes: int = MAX_WRITE_BYTES,
    max_list_entries: int = MAX_LIST_ENTRIES,
) -> dict[str, Any]:
    policy = FilesystemPolicy(
        tuple(Path(value) for value in allowed_roots),
        max_read_bytes=max_read_bytes,
        max_write_bytes=max_write_bytes,
        max_list_entries=max_list_entries,
    )
    return await asyncio.to_thread(_execute_filesystem_action_sync, proposal, policy)


__all__ = [
    "FILESYSTEM_ACTIONS",
    "FilesystemActionError",
    "FilesystemPolicy",
    "execute_filesystem_action",
    "filesystem_action_label",
    "filesystem_action_success_field",
    "validate_filesystem_action",
]
