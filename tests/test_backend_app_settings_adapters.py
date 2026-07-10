from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import backend.app as backend_app
from backend import app_settings_adapters
from backend import settings_config


class BackendAppSettingsAdapterTests(unittest.TestCase):
    def test_adapter_delegates_to_settings_config_with_explicit_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "pet_config.json"
            deps = app_settings_adapters.settings_adapter_dependencies(
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )

            with mock.patch.object(settings_config, "deep_merge", return_value={"merged": True}) as helper:
                self.assertEqual(app_settings_adapters.deep_merge({"brain": {}}, {"brain": {"model_name": "neo"}}), {"merged": True})
            helper.assert_called_once_with({"brain": {}}, {"brain": {"model_name": "neo"}})

            with mock.patch.object(settings_config, "load_raw_config", return_value={"raw": True}) as helper:
                self.assertEqual(app_settings_adapters.load_raw_config(deps), {"raw": True})
            helper.assert_called_once_with(config_path)

            raw = {"brain": {"model_name": "neo"}}
            with mock.patch.object(settings_config, "normalize_private_config", return_value={"normalized": True}) as helper:
                self.assertEqual(app_settings_adapters.normalize_private_config(raw, deps=deps), {"normalized": True})
            helper.assert_called_once_with(
                raw,
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )

            private_config = {"brain": {"api_key": "secret"}}
            with mock.patch.object(settings_config, "settings_payload", return_value={"payload": True}) as helper:
                self.assertEqual(app_settings_adapters.settings_payload(private_config, deps=deps), {"payload": True})
            helper.assert_called_once_with(
                private_config,
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )

            with mock.patch.object(settings_config, "save_config") as helper:
                app_settings_adapters.save_config(private_config, deps=deps)
            helper.assert_called_once_with(private_config, config_path=config_path)

            incoming = {"brain": {"model_name": "neo-update"}}
            with mock.patch.object(settings_config, "apply_settings_update", return_value={"updated": True}) as helper:
                self.assertEqual(
                    app_settings_adapters.apply_settings_update(incoming, current=private_config, deps=deps),
                    {"updated": True},
                )
            helper.assert_called_once_with(
                incoming,
                current=private_config,
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )

    def test_backend_app_wrappers_delegate_through_adapter_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "pet_config.json"
            with mock.patch.object(backend_app, "CONFIG_PATH", config_path):
                with mock.patch.object(
                    backend_app._settings_adapter_helpers,
                    "deep_merge",
                    return_value={"ok": True},
                ) as helper:
                    self.assertEqual(backend_app._deep_merge({"brain": {}}, {"brain": {"model_name": "neo"}}), {"ok": True})
                helper.assert_called_once_with({"brain": {}}, {"brain": {"model_name": "neo"}})

                with mock.patch.object(
                    backend_app._settings_adapter_helpers,
                    "load_raw_config",
                    return_value={"raw": True},
                ) as helper:
                    self.assertEqual(backend_app._load_raw_config(), {"raw": True})
                helper.assert_called_once()

                raw = {"brain": {"model_name": "neo"}}
                with mock.patch.object(
                    backend_app._settings_adapter_helpers,
                    "normalize_private_config",
                    return_value={"normalized": True},
                ) as helper:
                    self.assertEqual(backend_app._normalize_private_config(raw), {"normalized": True})
                helper.assert_called_once()

                private_config = {"brain": {"api_key": "secret"}}
                with mock.patch.object(
                    backend_app._settings_adapter_helpers,
                    "settings_payload",
                    return_value={"payload": True},
                ) as helper:
                    self.assertEqual(backend_app._settings_payload(private_config), {"payload": True})
                helper.assert_called_once()

                with mock.patch.object(backend_app._settings_adapter_helpers, "save_config") as helper:
                    backend_app._save_config(private_config)
                helper.assert_called_once()

                incoming = {"brain": {"model_name": "neo-update"}}
                with mock.patch.object(
                    backend_app._settings_adapter_helpers,
                    "apply_settings_update",
                    return_value={"updated": True},
                ) as helper:
                    self.assertEqual(backend_app._apply_settings_update(incoming, current=private_config), {"updated": True})
                helper.assert_called_once()

                deps = backend_app._settings_adapter_deps()
                self.assertEqual(deps.config_path, config_path)

    def test_adapter_source_does_not_import_backend_app(self) -> None:
        source = Path(app_settings_adapters.__file__).read_text(encoding="utf-8")

        self.assertNotIn("import backend.app", source)
        self.assertNotIn("from backend import app", source)
        self.assertNotIn("from . import app", source)


if __name__ == "__main__":
    unittest.main()
