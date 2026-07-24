from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from backend.settings_assets import (
    NO_STORE_HEADERS,
    SETTINGS_CSS_ASSET,
    SETTINGS_FORM_JS_ASSET,
    SETTINGS_HTML_ASSET,
    SETTINGS_JS_ASSET,
    SETTINGS_LIVE2D_JS_ASSET,
    SETTINGS_MODEL_PICKER_JS_ASSET,
    SettingsAsset,
    settings_asset_response,
)


class BackendSettingsAssetsTests(unittest.TestCase):
    def test_asset_response_adds_no_store_headers_and_media_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_path = Path(temp_dir) / "settings.css"
            asset_path.write_text("body { color: black; }\n", encoding="utf-8")
            response = settings_asset_response(
                SettingsAsset(
                    path=asset_path,
                    media_type="text/css",
                    missing_detail="settings.css not found.",
                )
            )

        self.assertIsInstance(response, FileResponse)
        self.assertEqual(response.media_type, "text/css")
        for name, value in NO_STORE_HEADERS.items():
            with self.subTest(header=name):
                self.assertEqual(response.headers.get(name.lower()), value)

    def test_asset_response_raises_original_404_detail_when_missing(self) -> None:
        missing_asset = SettingsAsset(
            path=Path("missing-settings.js"),
            media_type="application/javascript",
            missing_detail="settings.js not found.",
        )

        with self.assertRaises(HTTPException) as raised:
            settings_asset_response(missing_asset)

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "settings.js not found.")

    def test_default_specs_preserve_existing_paths_media_types_and_404_details(self) -> None:
        expected = (
            (SETTINGS_HTML_ASSET, "settings.html", None, "settings.html not found."),
            (SETTINGS_CSS_ASSET, "settings.css", "text/css", "settings.css not found."),
            (SETTINGS_JS_ASSET, "settings.js", "application/javascript", "settings.js not found."),
            (SETTINGS_FORM_JS_ASSET, "settings_form.js", "application/javascript", "settings_form.js not found."),
            (
                SETTINGS_MODEL_PICKER_JS_ASSET,
                "settings_model_picker.js",
                "application/javascript",
                "settings_model_picker.js not found.",
            ),
            (
                SETTINGS_LIVE2D_JS_ASSET,
                "settings_live2d.js",
                "application/javascript",
                "settings_live2d.js not found.",
            ),
        )
        for asset, name, media_type, missing_detail in expected:
            with self.subTest(name=name):
                self.assertEqual(asset.path.name, name)
                self.assertEqual(asset.media_type, media_type)
                self.assertEqual(asset.missing_detail, missing_detail)


if __name__ == "__main__":
    unittest.main()
