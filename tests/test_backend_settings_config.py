from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend import settings_config
from backend.local_capabilities import GAME_BROWSER_CAPABILITY, GAME_BROWSER_CAPABILITY_HEADER


class BackendSettingsConfigTests(unittest.TestCase):
    def test_settings_payload_does_not_expose_process_api_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "native-secret-value"}, clear=False):
                payload = settings_config.settings_payload(
                    {},
                    config_path=Path(tmp) / "missing.json",
                    defaults=backend_app.NEO_DEFAULTS,
                    allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
                )

        self.assertNotIn("local_api_token", payload)
        self.assertEqual(payload["game_capability"], GAME_BROWSER_CAPABILITY)
        self.assertNotEqual(payload["game_capability"], "native-secret-value")

    def test_settings_config_route_does_not_disclose_process_api_token(self) -> None:
        sentinel = "native-route-secret-value"
        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": sentinel}, clear=False):
            with TestClient(backend_app.app) as client:
                response = client.get("/api/settings/config")
                vision_response = client.get(
                    "/api/vision/context",
                    headers={GAME_BROWSER_CAPABILITY_HEADER: GAME_BROWSER_CAPABILITY},
                )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("local_api_token", response.json())
        self.assertNotIn(sentinel, response.text)
        self.assertEqual(vision_response.status_code, 403)

    def test_legacy_config_receives_bounded_game_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = settings_config.normalize_private_config(
                {
                    "brain": {},
                    "game": {
                        "enabled": True,
                        "min_reaction_interval_sec": -5,
                        "max_reactions_per_minute": 999,
                        "reaction_instruction": "x" * 700,
                        "categories": {"combat": False},
                    },
                },
                config_path=Path(tmp) / "missing.json",
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )

        self.assertEqual(config["game"]["min_reaction_interval_sec"], 0)
        self.assertEqual(config["game"]["max_reactions_per_minute"], 30)
        self.assertEqual(len(config["game"]["reaction_instruction"]), 500)
        self.assertFalse(config["game"]["categories"]["combat"])
        self.assertTrue(config["game"]["categories"]["outcome"])

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
                        "chat": {
                            "tts_api_key": "fish-secret",
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
            self.assertEqual(private_config["chat"]["tts_api_key"], "fish-secret")
            self.assertNotIn("runtime", private_config)
            self.assertNotIn("api_key", public["brain"])
            self.assertNotIn("api_key_clear", public["brain"])
            self.assertEqual(public["brain"]["api_key_preview"], "se***ue")
            self.assertNotIn("api_key", public["human_ops"]["observe_model"])
            self.assertNotIn("api_key_clear", public["human_ops"]["observe_model"])
            self.assertEqual(public["human_ops"]["observe_model"]["api_key_preview"], "ob***et")
            self.assertNotIn("tts_api_key", public["chat"])
            self.assertNotIn("tts_api_key_clear", public["chat"])
            self.assertEqual(public["chat"]["tts_api_key_preview"], "fi***et")

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

    def test_apply_settings_update_preserves_or_clears_fish_audio_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "pet_config.json"
            current = settings_config.normalize_private_config(
                {"chat": {"tts_api_key": "saved-fish-secret"}},
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )

            preserved = settings_config.apply_settings_update(
                {"chat": {"tts_provider": "fish_audio", "tts_api_key": ""}},
                current=current,
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )
            self.assertEqual(preserved["chat"]["tts_api_key"], "saved-fish-secret")

            cleared = settings_config.apply_settings_update(
                {"chat": {"tts_api_key_clear": True}},
                current=current,
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )
            self.assertNotIn("tts_api_key", cleared["chat"])
            self.assertNotIn("tts_api_key_clear", cleared["chat"])

    def test_action_authorization_mode_is_explicit_and_migrates_legacy_review_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "pet_config.json"

            legacy_full = settings_config.normalize_private_config(
                {"human_ops": {"require_act_review": False}},
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )
            explicit_full = settings_config.normalize_private_config(
                {"human_ops": {"authorization_mode": "full", "require_act_review": True}},
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )
            invalid = settings_config.normalize_private_config(
                {"human_ops": {"authorization_mode": "unknown", "require_act_review": True}},
                config_path=config_path,
                defaults=backend_app.NEO_DEFAULTS,
                allowed_keys=backend_app.ALLOWED_CONFIG_KEYS,
            )

        self.assertEqual(legacy_full["human_ops"]["authorization_mode"], "full")
        self.assertFalse(legacy_full["human_ops"]["require_act_review"])
        self.assertEqual(explicit_full["human_ops"]["authorization_mode"], "full")
        self.assertFalse(explicit_full["human_ops"]["require_act_review"])
        self.assertEqual(invalid["human_ops"]["authorization_mode"], "review")
        self.assertTrue(invalid["human_ops"]["require_act_review"])

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
