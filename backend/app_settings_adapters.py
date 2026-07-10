from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import settings_config as _settings_config_helpers


@dataclass(frozen=True)
class SettingsAdapterDependencies:
    config_path: Path
    defaults: dict[str, Any]
    allowed_keys: Iterable[str]


def settings_adapter_dependencies(
    *,
    config_path: Path,
    defaults: dict[str, Any],
    allowed_keys: Iterable[str],
) -> SettingsAdapterDependencies:
    return SettingsAdapterDependencies(
        config_path=config_path,
        defaults=defaults,
        allowed_keys=allowed_keys,
    )


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    return _settings_config_helpers.deep_merge(base, override)


def load_raw_config(deps: SettingsAdapterDependencies) -> dict[str, Any]:
    return _settings_config_helpers.load_raw_config(deps.config_path)


def normalize_private_config(
    raw: dict[str, Any] | None = None,
    *,
    deps: SettingsAdapterDependencies,
) -> dict[str, Any]:
    return _settings_config_helpers.normalize_private_config(
        raw,
        config_path=deps.config_path,
        defaults=deps.defaults,
        allowed_keys=deps.allowed_keys,
    )


def secret_preview(value: str) -> str:
    return _settings_config_helpers.secret_preview(value)


def public_config(private_config: dict[str, Any]) -> dict[str, Any]:
    return _settings_config_helpers.public_config(private_config)


def settings_payload(
    private_config: dict[str, Any] | None = None,
    *,
    deps: SettingsAdapterDependencies,
) -> dict[str, Any]:
    return _settings_config_helpers.settings_payload(
        private_config,
        config_path=deps.config_path,
        defaults=deps.defaults,
        allowed_keys=deps.allowed_keys,
    )


def apply_settings_update(
    incoming: dict[str, Any],
    *,
    current: dict[str, Any] | None = None,
    deps: SettingsAdapterDependencies,
) -> dict[str, Any]:
    return _settings_config_helpers.apply_settings_update(
        incoming,
        current=current,
        config_path=deps.config_path,
        defaults=deps.defaults,
        allowed_keys=deps.allowed_keys,
    )


def save_config(private_config: dict[str, Any], *, deps: SettingsAdapterDependencies) -> None:
    _settings_config_helpers.save_config(private_config, config_path=deps.config_path)
