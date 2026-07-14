from __future__ import annotations

import unittest

import backend.app as backend_app
from backend import settings_defaults


class BackendSettingsDefaultsTests(unittest.TestCase):
    def test_app_reexports_settings_defaults_constants_for_compatibility(self) -> None:
        self.assertIs(backend_app.NEO_DEFAULTS, settings_defaults.NEO_DEFAULTS)
        self.assertIs(backend_app.ALLOWED_CONFIG_KEYS, settings_defaults.ALLOWED_CONFIG_KEYS)
        self.assertEqual(backend_app.ALLOWED_CONFIG_KEYS, set(backend_app.NEO_DEFAULTS))

    def test_settings_defaults_returns_independent_copy(self) -> None:
        defaults = settings_defaults.settings_defaults()
        defaults["brain"]["model_name"] = "changed"
        defaults["skills"]["recipes"].append({"name": "temporary"})

        self.assertEqual(settings_defaults.NEO_DEFAULTS["brain"]["model_name"], "gpt-5.4")
        self.assertEqual(settings_defaults.NEO_DEFAULTS["skills"]["recipes"], [])
        self.assertTrue(settings_defaults.NEO_DEFAULTS["human_ops"]["filesystem"]["enabled"])
        self.assertEqual(settings_defaults.NEO_DEFAULTS["human_ops"]["playwright_profile"], "")
        self.assertEqual(settings_defaults.NEO_DEFAULTS["human_ops"]["filesystem"]["allowed_roots"], [])
        self.assertEqual(settings_defaults.allowed_config_keys(), settings_defaults.ALLOWED_CONFIG_KEYS)


if __name__ == "__main__":
    unittest.main()
