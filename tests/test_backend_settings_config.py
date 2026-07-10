from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend import settings_config


class BackendSettingsConfigTests(unittest.TestCase):
    def test_app_helpers_delegate_to_settings_config_with_current_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "pet_config.json"
            with mock.patch.object(backend_app, "CONFIG_PATH", config_path):
                with mock.patch.object(settings_config, "deep_merge", return_value={"ok": True}) as helper:
                    self.assertEqual(backend_app._deep_merge({"brain": {}}, {"brain": {"model_name": "neo"}}), {"ok": True})
                helper.assert_called_once_with({"brain": {}}, {"brain": {"model_name": "neo"}})

                with mock.patch.object(settings_config, "load_raw_config", return_value={"raw": True}) as helper:
                    self.assertEqual(backend_app._load_raw_config(), {"raw": True})
                helper.assert_called_once_with(config_path)

                raw = {"brain": {"model_name": "neo"}}
                with mock.patch.object(settings_config, "normalize_private_config", return_value={"normalized": True}) as helper:
                    self.assertEqual(backend_app._normalize_private_config(raw), {"normalized": True})
                helper.assert_called_once_with(
                    raw,
                    config_path=config_path,
                    defaults=backend_app.NEO_DEFAULTS,
                    allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
                )

                private_config = {"brain": {"api_key": "secret"}}
                with mock.patch.object(settings_config, "settings_payload", return_value={"payload": True}) as helper:
                    self.assertEqual(backend_app._settings_payload(private_config), {"payload": True})
                helper.assert_called_once_with(
                    private_config,
                    config_path=config_path,
                    defaults=backend_app.NEO_DEFAULTS,
                    allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
                )

                with mock.patch.object(settings_config, "save_config") as helper:
                    backend_app._save_config(private_config)
                helper.assert_called_once_with(private_config, config_path=config_path)

                incoming = {"brain": {"model_name": "neo-update"}}
                with mock.patch.object(settings_config, "apply_settings_update", return_value={"updated": True}) as helper:
                    self.assertEqual(backend_app._apply_settings_update(incoming, current=private_config), {"updated": True})
                helper.assert_called_once_with(
                    incoming,
                    current=private_config,
                    config_path=config_path,
                    defaults=backend_app.NEO_DEFAULTS,
                    allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
                )

    def test_settings_put_route_delegates_update_flow_to_app_helper(self) -> None:
        with mock.patch.object(backend_app, "_apply_settings_update", return_value={"brain": {"model_name": "delegated"}}) as helper:
            with mock.patch.object(backend_app, "_save_config") as save_helper:
                with mock.patch.object(backend_app, "_settings_payload", return_value={"payload": True}) as payload_helper:
                    resp = TestClient(backend_app.app).put(
                        "/api/settings/config",
                        json={"config": {"brain": {"model_name": "delegated"}}},
                    )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"payload": True})
        helper.assert_called_once_with({"brain": {"model_name": "delegated"}}, current=mock.ANY)
        save_helper.assert_called_once_with({"brain": {"model_name": "delegated"}})
        payload_helper.assert_called_once_with({"brain": {"model_name": "delegated"}})

    def test_module_keeps_secret_redaction_and_utf8_sig_config_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "pet_config.json"
            config_path.write_text(
                "\ufeff"
                + json.dumps(
                    {
                        "brain": {
                            "model_name": "neo-custom",
                            "api_key": "secret-value",
                            "api_key_clear": True,
                        },
                        "human_ops": {
                            "observe_model": {
                                "enabled": True,
                                "api_key": "observe-secret",
                                "api_key_clear": True,
                            }
                        },
                        "runtime": {"legacy": True},
                    }
                ),
                encoding="utf-8",
            )

            raw = settings_config.load_raw_config(config_path)
            private_config = settings_config.normalize_private_config(
                raw,
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )
            public = settings_config.public_config(private_config)

            self.assertEqual(private_config["brain"]["model_name"], "neo-custom")
            self.assertEqual(private_config["brain"]["api_key"], "secret-value")
            self.assertEqual(private_config["human_ops"]["observe_model"]["api_key"], "observe-secret")
            self.assertNotIn("runtime", private_config)
            self.assertNotIn("api_key", public["brain"])
            self.assertNotIn("api_key_clear", public["brain"])
            self.assertEqual(public["brain"]["api_key_preview"], "se***ue")
            self.assertNotIn("api_key", public["human_ops"]["observe_model"])
            self.assertNotIn("api_key_clear", public["human_ops"]["observe_model"])
            self.assertEqual(public["human_ops"]["observe_model"]["api_key_preview"], "ob***et")

            saved_path = Path(tmp) / "saved_config.json"
            settings_config.save_config(private_config, config_path=saved_path)

            saved_text = saved_path.read_text(encoding="utf-8")
            self.assertTrue(saved_text.endswith("\n"))
            self.assertIn('\n  "brain": {', saved_text)
            self.assertEqual(json.loads(saved_text)["brain"]["api_key"], "secret-value")

    def test_apply_settings_update_preserves_or_clears_brain_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "pet_config.json"
            current = settings_config.normalize_private_config(
                {"brain": {"model_name": "old", "api_key": "saved-secret"}},
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )

            preserved = settings_config.apply_settings_update(
                {"brain": {"model_name": "new", "api_key": ""}},
                current=current,
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )
            self.assertEqual(preserved["brain"]["model_name"], "new")
            self.assertEqual(preserved["brain"]["api_key"], "saved-secret")
            self.assertNotIn("api_key_clear", preserved["brain"])

            cleared = settings_config.apply_settings_update(
                {"brain": {"api_key_clear": True}},
                current=current,
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )
            self.assertNotIn("api_key", cleared["brain"])
            self.assertNotIn("api_key_clear", cleared["brain"])

    def test_apply_settings_update_preserves_observe_secret_clear_and_set_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "pet_config.json"
            current = settings_config.normalize_private_config(
                {
                    "human_ops": {
                        "observe_model": {
                            "enabled": True,
                            "model_name": "observe-old",
                            "api_key": "observe-saved",
                        }
                    }
                },
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )

            updated = settings_config.apply_settings_update(
                {
                    "human_ops": {
                        "observe_model": {
                            "model_name": "observe-new",
                            "api_key": "observe-new-secret",
                        }
                    }
                },
                current=current,
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )
            self.assertEqual(updated["human_ops"]["observe_model"]["model_name"], "observe-new")
            self.assertEqual(updated["human_ops"]["observe_model"]["api_key"], "observe-new-secret")
            self.assertNotIn("api_key_clear", updated["human_ops"]["observe_model"])

            cleared = settings_config.apply_settings_update(
                {"human_ops": {"observe_model": {"api_key_clear": True}}},
                current=current,
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )
            self.assertNotIn("api_key", cleared["human_ops"]["observe_model"])
            self.assertNotIn("api_key_clear", cleared["human_ops"]["observe_model"])


if __name__ == "__main__":
    unittest.main()
