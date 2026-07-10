from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import main


ROOT_DIR = Path(__file__).resolve().parents[1]


class _FakePermissionPolicy:
    PermissionGrantedByUser = "grant"
    PermissionDeniedByUser = "deny"


class _FakeFeature:
    MediaAudioCapture = "audio"
    MediaVideoCapture = "video"
    MediaAudioVideoCapture = "audio-video"


class _FakeWebEnginePage:
    PermissionPolicy = _FakePermissionPolicy
    Feature = _FakeFeature


class _FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, object]] = []

    def setFeaturePermission(self, security_origin, feature, policy) -> None:
        self.calls.append((security_origin, feature, policy))


class _FakeBrowser:
    def __init__(self) -> None:
        self.page_obj = _FakePage()

    def page(self) -> _FakePage:
        return self.page_obj


class _FakeOwner:
    def __init__(self) -> None:
        self.browser = _FakeBrowser()


class _FakeSecurityOrigin:
    def __init__(
        self,
        *,
        local_file: bool | Exception = False,
        scheme: str | Exception = "https",
    ) -> None:
        self.local_file = local_file
        self.scheme_value = scheme

    def isLocalFile(self):
        if isinstance(self.local_file, Exception):
            raise self.local_file
        return self.local_file

    def scheme(self):
        if isinstance(self.scheme_value, Exception):
            raise self.scheme_value
        return self.scheme_value


def _parse_source(relative_path: str) -> ast.Module:
    path = ROOT_DIR / relative_path
    if not path.exists():
        raise AssertionError(f"{relative_path} should exist")
    return ast.parse(path.read_text(encoding="utf-8"))


def _desktop_pet_method(name: str) -> ast.FunctionDef:
    tree = _parse_source("main.py")
    desktop_pet = next(
        node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "DesktopPet"
    )
    return next(
        node
        for node in desktop_pet.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


class DesktopWebPermissionsSplitTests(unittest.TestCase):
    def _controller(self, owner=None, q_web_engine_page=None):
        module = importlib.import_module("app.desktop_web_permissions")
        return module.DesktopWebPermissionsController(
            owner or _FakeOwner(),
            q_web_engine_page=q_web_engine_page or _FakeWebEnginePage,
        )

    def test_web_permissions_module_exists_without_importing_main(self) -> None:
        tree = _parse_source("app/desktop_web_permissions.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertNotIn("main", {alias.name for alias in node.names})
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "main")

        module = importlib.import_module("app.desktop_web_permissions")
        self.assertTrue(hasattr(module, "DesktopWebPermissionsController"))

    def test_permission_policy_maps_grant_and_deny(self) -> None:
        controller = self._controller()

        self.assertEqual(controller.feature_permission_policy(True), "grant")
        self.assertEqual(controller.feature_permission_policy(False), "deny")
        self.assertIsNone(
            self._controller(q_web_engine_page=SimpleNamespace).feature_permission_policy(True)
        )

    def test_file_origin_detection_uses_local_file_or_file_scheme(self) -> None:
        controller = self._controller()

        self.assertTrue(controller.is_local_security_origin(_FakeSecurityOrigin(local_file=True)))
        self.assertTrue(
            controller.is_local_security_origin(
                _FakeSecurityOrigin(local_file=RuntimeError("boom"), scheme="FILE")
            )
        )
        self.assertFalse(controller.is_local_security_origin(_FakeSecurityOrigin(scheme="https")))
        self.assertFalse(
            controller.is_local_security_origin(
                _FakeSecurityOrigin(local_file=RuntimeError("boom"), scheme=RuntimeError("boom"))
            )
        )

    def test_local_audio_capture_is_granted(self) -> None:
        owner = _FakeOwner()
        origin = _FakeSecurityOrigin(local_file=True)

        self._controller(owner).on_feature_permission_requested(origin, _FakeFeature.MediaAudioCapture)

        self.assertEqual(owner.browser.page_obj.calls, [(origin, "audio", "grant")])

    def test_remote_audio_video_and_audio_video_capture_are_denied(self) -> None:
        owner = _FakeOwner()
        origin = _FakeSecurityOrigin(scheme="https")
        controller = self._controller(owner)

        controller.on_feature_permission_requested(origin, _FakeFeature.MediaAudioCapture)
        controller.on_feature_permission_requested(origin, _FakeFeature.MediaVideoCapture)
        controller.on_feature_permission_requested(origin, _FakeFeature.MediaAudioVideoCapture)

        self.assertEqual(
            owner.browser.page_obj.calls,
            [
                (origin, "audio", "deny"),
                (origin, "video", "deny"),
                (origin, "audio-video", "deny"),
            ],
        )

    def test_missing_permission_policy_silently_skips(self) -> None:
        owner = _FakeOwner()
        controller = self._controller(owner, q_web_engine_page=SimpleNamespace)

        controller.on_feature_permission_requested(
            _FakeSecurityOrigin(local_file=True),
            _FakeFeature.MediaAudioCapture,
        )

        self.assertEqual(owner.browser.page_obj.calls, [])

    def test_desktop_pet_feature_permission_method_delegates_to_controller(self) -> None:
        method = _desktop_pet_method("on_feature_permission_requested")
        self.assertLessEqual(method.end_lineno - method.lineno, 2)

        controller = mock.Mock()
        owner = SimpleNamespace(_web_permissions_controller=controller)

        main.DesktopPet.on_feature_permission_requested(owner, "origin", "feature")

        controller.on_feature_permission_requested.assert_called_once_with("origin", "feature")


if __name__ == "__main__":
    unittest.main()
