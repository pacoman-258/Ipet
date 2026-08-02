from __future__ import annotations

import ast
import importlib
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class _Signal:
    def __init__(self) -> None:
        self.connections: list[object] = []

    def connect(self, callback) -> None:
        self.connections.append(callback)


class _FakeSettings:
    def __init__(self) -> None:
        self.attributes: list[tuple[object, object]] = []

    def setAttribute(self, attribute, value) -> None:
        self.attributes.append((attribute, value))


class _FakePage:
    def __init__(self, *, feature_signal: bool = True) -> None:
        self.background_colors: list[object] = []
        self.web_channels: list[object] = []
        if feature_signal:
            self.featurePermissionRequested = _Signal()

    def setBackgroundColor(self, color) -> None:
        self.background_colors.append(color)

    def setWebChannel(self, channel) -> None:
        self.web_channels.append(channel)


class _FakeBrowser:
    def __init__(self, parent, *, feature_signal: bool = True) -> None:
        self.parent = parent
        self.page_obj = _FakePage(feature_signal=feature_signal)
        self.settings_obj = _FakeSettings()
        self.customContextMenuRequested = _Signal()
        self.loadFinished = _Signal()
        self.mouse_tracking_values: list[bool] = []
        self.attributes: list[tuple[object, object]] = []
        self.auto_fill_background_values: list[bool] = []
        self.style_sheets: list[str] = []
        self.sizes: list[tuple[int, int]] = []
        self.context_menu_policies: list[object] = []
        self.event_filters: list[object] = []
        self.urls: list[object] = []

    def page(self) -> _FakePage:
        return self.page_obj

    def settings(self) -> _FakeSettings:
        return self.settings_obj

    def setMouseTracking(self, enabled: bool) -> None:
        self.mouse_tracking_values.append(enabled)

    def setAttribute(self, attribute, enabled: bool) -> None:
        self.attributes.append((attribute, enabled))

    def setAutoFillBackground(self, enabled: bool) -> None:
        self.auto_fill_background_values.append(enabled)

    def setStyleSheet(self, value: str) -> None:
        self.style_sheets.append(value)

    def resize(self, width: int, height: int) -> None:
        self.sizes.append((width, height))

    def setContextMenuPolicy(self, policy) -> None:
        self.context_menu_policies.append(policy)

    def installEventFilter(self, owner) -> None:
        self.event_filters.append(owner)

    def setUrl(self, url) -> None:
        self.urls.append(url)


class _FakeChannel:
    def __init__(self, page) -> None:
        self.page = page
        self.registrations: list[tuple[str, object]] = []

    def registerObject(self, name: str, obj) -> None:
        self.registrations.append((name, obj))


class _FakeApplication:
    def __init__(self) -> None:
        self.event_filters: list[object] = []

    def installEventFilter(self, owner) -> None:
        self.event_filters.append(owner)


class _FakeQt:
    class ContextMenuPolicy:
        CustomContextMenu = "custom-context-menu"

    class WidgetAttribute:
        WA_TranslucentBackground = "translucent-background"


class _FakeWebAttribute:
    LocalContentCanAccessFileUrls = "local-file"
    LocalContentCanAccessRemoteUrls = "remote-url"
    WebGLEnabled = "webgl"
    Accelerated2dCanvasEnabled = "canvas"


class _FakeQUrl:
    @staticmethod
    def fromLocalFile(path: str) -> tuple[str, str]:
        return ("local-file-url", path)


class _Owner:
    def __init__(self) -> None:
        self.central_widgets: list[object] = []

    def setCentralWidget(self, widget) -> None:
        self.central_widgets.append(widget)

    def width(self) -> int:
        return 420

    def height(self) -> int:
        return 640

    def show_context_menu(self, pos) -> None:
        del pos

    def on_feature_permission_requested(self, security_origin, feature) -> None:
        del security_origin, feature

    def on_web_loaded(self, ok: bool) -> None:
        del ok


def _browser_factory(created: list[_FakeBrowser], *, feature_signal: bool = True):
    def factory(parent):
        browser = _FakeBrowser(parent, feature_signal=feature_signal)
        created.append(browser)
        return browser

    return factory


def _parse_source(relative_path: str) -> ast.Module:
    path = ROOT_DIR / relative_path
    return ast.parse(path.read_text(encoding="utf-8"))


def _desktop_pet_init_source() -> str:
    path = ROOT_DIR / "main.py"
    tree = _parse_source("main.py")
    desktop_pet = next(
        node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "DesktopPet"
    )
    init = next(
        node for node in desktop_pet.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[init.lineno - 1 : init.end_lineno])


class DesktopBrowserSetupTests(unittest.TestCase):
    def _module(self):
        return importlib.import_module("app.desktop_browser_setup")

    def _dependencies(
        self,
        module,
        *,
        created: list[_FakeBrowser],
        application: _FakeApplication | None,
        install_filters=True,
        feature_signal: bool = True,
    ):
        return module.DesktopBrowserSetupDependencies(
            web_engine_view_factory=_browser_factory(created, feature_signal=feature_signal),
            web_channel_factory=_FakeChannel,
            q_url_factory=_FakeQUrl,
            q_color_factory=lambda *rgba: ("color", rgba),
            q_application=type("QApplication", (), {"instance": staticmethod(lambda: application)}),
            qt=_FakeQt,
            web_attribute=_FakeWebAttribute,
            background_color=lambda: (1, 2, 3, 4),
            webgl_enabled=lambda: True,
            install_python_event_filters=install_filters,
        )

    def test_setup_configures_browser_channel_signals_filters_and_url(self) -> None:
        module = self._module()
        owner = _Owner()
        bridge = object()
        application = _FakeApplication()
        created: list[_FakeBrowser] = []

        with tempfile.TemporaryDirectory() as tmp:
            dependencies = self._dependencies(module, created=created, application=application)

            result = module.setup_desktop_browser(
                owner,
                bridge=bridge,
                root_dir=Path(tmp),
                dependencies=dependencies,
            )

            expected_url = str((Path(tmp) / "index.html").resolve())

        self.assertEqual(len(created), 1)
        browser = created[0]
        self.assertIs(result.browser, browser)
        self.assertIs(result.channel.page, browser.page_obj)
        self.assertTrue(result.event_filter_installed)
        self.assertIs(browser.parent, owner)
        self.assertEqual(browser.mouse_tracking_values, [True])
        self.assertEqual(browser.attributes, [])
        self.assertEqual(browser.auto_fill_background_values, [])
        self.assertEqual(browser.style_sheets, [])
        self.assertEqual(browser.page_obj.background_colors, [("color", (1, 2, 3, 4))])
        self.assertEqual(
            browser.settings_obj.attributes,
            [
                ("local-file", True),
                ("remote-url", True),
                ("webgl", True),
                ("canvas", False),
            ],
        )
        self.assertEqual(browser.context_menu_policies, ["custom-context-menu"])
        self.assertEqual(browser.customContextMenuRequested.connections, [owner.show_context_menu])
        self.assertEqual(browser.event_filters, [owner])
        self.assertEqual(application.event_filters, [owner])
        self.assertEqual(
            browser.page_obj.featurePermissionRequested.connections,
            [owner.on_feature_permission_requested],
        )
        self.assertEqual(result.channel.registrations, [("qtBridge", bridge)])
        self.assertEqual(browser.page_obj.web_channels, [result.channel])
        self.assertEqual(owner.central_widgets, [browser])
        self.assertEqual(browser.sizes, [(420, 640)])
        self.assertEqual(browser.loadFinished.connections, [owner.on_web_loaded])
        self.assertEqual(browser.urls, [("local-file-url", expected_url)])

    def test_setup_skips_optional_feature_signal_and_event_filters(self) -> None:
        module = self._module()
        owner = _Owner()
        created: list[_FakeBrowser] = []
        application = _FakeApplication()
        dependencies = self._dependencies(
            module,
            created=created,
            application=application,
            install_filters=False,
            feature_signal=False,
        )

        result = module.setup_desktop_browser(
            owner,
            bridge=object(),
            root_dir=ROOT_DIR,
            dependencies=dependencies,
        )

        self.assertFalse(result.event_filter_installed)
        self.assertEqual(created[0].event_filters, [])
        self.assertEqual(application.event_filters, [])
        self.assertFalse(hasattr(created[0].page_obj, "featurePermissionRequested"))

    def test_setup_makes_the_web_view_translucent_for_a_transparent_page(self) -> None:
        module = self._module()
        owner = _Owner()
        created: list[_FakeBrowser] = []
        dependencies = self._dependencies(
            module,
            created=created,
            application=_FakeApplication(),
        )
        dependencies = module.DesktopBrowserSetupDependencies(
            **{
                **dependencies.__dict__,
                "background_color": lambda: (0, 0, 0, 0),
            }
        )

        module.setup_desktop_browser(
            owner,
            bridge=object(),
            root_dir=ROOT_DIR,
            dependencies=dependencies,
        )

        browser = created[0]
        self.assertEqual(browser.attributes, [("translucent-background", True)])
        self.assertEqual(browser.auto_fill_background_values, [False])
        self.assertEqual(browser.style_sheets, ["background: transparent;"])

    def test_desktop_pet_init_delegates_browser_setup(self) -> None:
        source = _desktop_pet_init_source()

        self.assertIn("setup_desktop_browser", source)
        self.assertNotIn("QWebEngineView(", source)
        self.assertNotIn("QWebChannel(", source)
        self.assertNotIn("settings.setAttribute", source)
        self.assertNotIn(".registerObject(", source)
        self.assertNotIn(".setWebChannel(", source)
        self.assertNotIn(".loadFinished.connect(self.on_web_loaded)", source)

    def test_browser_setup_module_does_not_import_main_or_qt_bindings(self) -> None:
        tree = _parse_source("app/desktop_browser_setup.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name for alias in node.names}
                self.assertNotIn("main", imported)
                self.assertNotIn("PySide6", imported)
                self.assertNotIn("PyQt6", imported)
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "main")
                self.assertFalse(str(node.module).startswith("PySide6"))
                self.assertFalse(str(node.module).startswith("PyQt6"))


if __name__ == "__main__":
    unittest.main()
