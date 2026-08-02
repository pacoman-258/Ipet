from __future__ import annotations

from typing import Any


class DesktopContextMenuController:
    def __init__(self, owner: Any) -> None:
        self.owner = owner

    def show_context_menu(self, pos: Any) -> None:
        x = int(pos.x())
        y = int(pos.y())
        script = (
            "window.PET_APP && "
            f"window.PET_APP.showContextMenuAt({x}, {y});"
        )
        self.owner.browser.page().runJavaScript(script)
