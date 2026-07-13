from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_LOCAL_STATE = "Local State"
_SNAPSHOT_MARKER = ".ipet-profile.json"
_PROFILE_STATE_FILES = (
    "Preferences",
    "Cookies",
    "Cookies-journal",
)
_PROFILE_STATE_DIRECTORIES = (
    "Network",
    "Local Storage",
    "IndexedDB",
    "Session Storage",
    "WebStorage",
    "Storage",
    "SharedStorage",
)


@dataclass(frozen=True)
class ChromeProfile:
    directory: str
    display_name: str
    user_data_dir: Path
    active: bool = False

    @property
    def source_directory(self) -> Path:
        return self.user_data_dir / self.directory


def chrome_user_data_directory() -> Path:
    configured = str(os.environ.get("IPET_CHROME_USER_DATA_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"


def _load_local_state(user_data_dir: Path) -> dict[str, Any]:
    path = user_data_dir / _LOCAL_STATE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("Chrome personal profiles are unavailable because Local State was not found.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Chrome personal profiles could not be read: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Chrome Local State does not contain an object.")
    return value


def list_chrome_profiles() -> tuple[ChromeProfile, ...]:
    user_data_dir = chrome_user_data_directory()
    state = _load_local_state(user_data_dir)
    profile_state = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    info_cache = profile_state.get("info_cache") if isinstance(profile_state.get("info_cache"), dict) else {}
    active_profiles = {
        str(value).strip()
        for value in profile_state.get("last_active_profiles", [])
        if str(value).strip()
    }
    order = [
        str(value).strip()
        for value in profile_state.get("profiles_order", [])
        if str(value).strip()
    ]
    for directory in info_cache:
        normalized = str(directory).strip()
        if normalized and normalized not in order:
            order.append(normalized)

    profiles: list[ChromeProfile] = []
    for directory in order:
        metadata = info_cache.get(directory) if isinstance(info_cache.get(directory), dict) else {}
        display_name = str(metadata.get("name") or directory).strip() or directory
        if not (user_data_dir / directory).is_dir():
            continue
        profiles.append(
            ChromeProfile(
                directory=directory,
                display_name=display_name,
                user_data_dir=user_data_dir,
                active=directory in active_profiles,
            )
        )
    return tuple(profiles)


def active_chrome_profiles() -> tuple[ChromeProfile, ...]:
    return tuple(profile for profile in list_chrome_profiles() if profile.active)


def resolve_chrome_profile(name: str) -> ChromeProfile:
    requested = str(name or "").strip()
    if not requested:
        raise ValueError("Playwright profile must be explicitly selected by the user.")
    folded = requested.casefold()
    profiles = list_chrome_profiles()
    matches = [
        profile
        for profile in profiles
        if folded in {profile.display_name.casefold(), profile.directory.casefold()}
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        directories = ", ".join(profile.directory for profile in matches)
        raise ValueError(
            f'Chrome profile name "{requested}" is ambiguous; use its directory name: {directories}.'
        )
    available = ", ".join(profile.display_name for profile in profiles) or "none"
    raise ValueError(f'Chrome profile "{requested}" was not found. Available profiles: {available}.')


def chrome_profile_snapshot_directory(name: str) -> Path:
    profile = resolve_chrome_profile(name)
    configured = str(os.environ.get("IPET_PLAYWRIGHT_PROFILE_ROOT") or "").strip()
    root = (
        Path(configured).expanduser()
        if configured
        else Path(__file__).resolve().parents[1] / "data" / "playwright_profile_snapshots"
    )
    digest = hashlib.sha256(
        f"chrome-profile-snapshot-v1:{profile.directory.casefold()}".encode("utf-8")
    ).hexdigest()
    return root / digest


def _write_snapshot_local_state(source: dict[str, Any], destination: Path, profile: ChromeProfile) -> None:
    source_profile = source.get("profile") if isinstance(source.get("profile"), dict) else {}
    source_cache = source_profile.get("info_cache") if isinstance(source_profile.get("info_cache"), dict) else {}
    source_info = source_cache.get(profile.directory) if isinstance(source_cache.get(profile.directory), dict) else {}
    profile_info = {
        "name": profile.display_name,
        "is_ephemeral": False,
        "is_using_default_name": False,
    }
    if "avatar_icon" in source_info:
        profile_info["avatar_icon"] = source_info["avatar_icon"]
    snapshot: dict[str, Any] = {
        "profile": {
            "info_cache": {"Default": profile_info},
            "last_active_profiles": ["Default"],
            "last_used": "Default",
            "profiles_order": ["Default"],
        }
    }
    if isinstance(source.get("os_crypt"), dict):
        snapshot["os_crypt"] = source["os_crypt"]
    destination.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _copy_profile_state(profile: ChromeProfile, destination: Path) -> None:
    destination.mkdir(parents=True, mode=0o700)
    source = profile.source_directory
    for name in _PROFILE_STATE_FILES:
        source_path = source / name
        if source_path.is_file():
            shutil.copy2(source_path, destination / name)
    for name in _PROFILE_STATE_DIRECTORIES:
        source_path = source / name
        if source_path.is_dir():
            shutil.copytree(source_path, destination / name, symlinks=True)


def prepare_chrome_profile_snapshot(name: str) -> tuple[Path, ChromeProfile, bool]:
    profile = resolve_chrome_profile(name)
    destination = chrome_profile_snapshot_directory(name)
    marker = destination / _SNAPSHOT_MARKER
    if marker.is_file() and (destination / "Default").is_dir():
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
        if metadata.get("source_directory") == profile.directory:
            return destination, profile, True
    if destination.exists():
        raise RuntimeError(
            "The Ipet Chrome profile snapshot is incomplete. It was not overwritten; choose another profile "
            "or remove the incomplete snapshot after reviewing it."
        )

    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        source_state = _load_local_state(profile.user_data_dir)
        _write_snapshot_local_state(source_state, temporary / _LOCAL_STATE, profile)
        _copy_profile_state(profile, temporary / "Default")
        (temporary / _SNAPSHOT_MARKER).write_text(
            json.dumps(
                {
                    "schema": 1,
                    "source": "google_chrome",
                    "source_directory": profile.directory,
                    "display_name": profile.display_name,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination, profile, False


def chrome_remote_debugging_ready(profile: ChromeProfile) -> bool:
    active_profiles = active_chrome_profiles()
    if len(active_profiles) != 1 or active_profiles[0].directory != profile.directory:
        return False
    active_port = profile.user_data_dir / "DevToolsActivePort"
    try:
        port = int(active_port.read_text(encoding="utf-8").splitlines()[0].strip())
    except (OSError, ValueError, IndexError):
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False
