from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse


ROOT_DIR = Path(__file__).resolve().parents[1]

NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@dataclass(frozen=True)
class SettingsAsset:
    path: Path
    missing_detail: str
    media_type: str | None = None


SETTINGS_HTML_ASSET = SettingsAsset(
    path=ROOT_DIR / "settings.html",
    missing_detail="settings.html not found.",
)
SETTINGS_CSS_ASSET = SettingsAsset(
    path=ROOT_DIR / "settings.css",
    media_type="text/css",
    missing_detail="settings.css not found.",
)
SETTINGS_JS_ASSET = SettingsAsset(
    path=ROOT_DIR / "settings.js",
    media_type="application/javascript",
    missing_detail="settings.js not found.",
)
SETTINGS_FORM_JS_ASSET = SettingsAsset(
    path=ROOT_DIR / "settings_form.js",
    media_type="application/javascript",
    missing_detail="settings_form.js not found.",
)
SETTINGS_MODEL_PICKER_JS_ASSET = SettingsAsset(
    path=ROOT_DIR / "settings_model_picker.js",
    media_type="application/javascript",
    missing_detail="settings_model_picker.js not found.",
)
SETTINGS_LIVE2D_JS_ASSET = SettingsAsset(
    path=ROOT_DIR / "settings_live2d.js",
    media_type="application/javascript",
    missing_detail="settings_live2d.js not found.",
)


def settings_asset_response(asset: SettingsAsset) -> FileResponse:
    if not asset.path.exists():
        raise HTTPException(status_code=404, detail=asset.missing_detail)
    return FileResponse(
        asset.path,
        media_type=asset.media_type,
        headers=dict(NO_STORE_HEADERS),
    )


def settings_page_response() -> FileResponse:
    return settings_asset_response(SETTINGS_HTML_ASSET)


def settings_css_response() -> FileResponse:
    return settings_asset_response(SETTINGS_CSS_ASSET)


def settings_js_response() -> FileResponse:
    return settings_asset_response(SETTINGS_JS_ASSET)


def settings_form_js_response() -> FileResponse:
    return settings_asset_response(SETTINGS_FORM_JS_ASSET)


def settings_model_picker_js_response() -> FileResponse:
    return settings_asset_response(SETTINGS_MODEL_PICKER_JS_ASSET)


def settings_live2d_js_response() -> FileResponse:
    return settings_asset_response(SETTINGS_LIVE2D_JS_ASSET)
