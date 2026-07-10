from __future__ import annotations

from typing import Any


ResizeEdges = tuple[bool, bool, bool, bool]


class WindowInteractionController:
    def __init__(
        self,
        owner: Any,
        *,
        qt: Any,
        q_event: Any,
        q_application: Any,
        q_point: Any,
    ) -> None:
        self.owner = owner
        self.qt = qt
        self.q_event = q_event
        self.q_application = q_application
        self.q_point = q_point

    def event_filter(self, watched: Any, event: Any) -> bool | None:
        owner = self.owner
        qt = self.qt
        q_event = self.q_event

        if not self._is_browser_related(watched):
            return None

        edit_mode = bool(owner.config.get("pet", {}).get("edit_mode", False))

        if event.type() == q_event.Type.MouseButtonPress and event.button() == qt.MouseButton.LeftButton:
            if edit_mode:
                local = self._local_point(watched, event)
                owner._drag_start_global = self._global_point(event)
                owner._drag_start_geometry = owner.geometry()
                owner._resize_edges = self._hit_edges(local)
                owner._window_resizing = any(owner._resize_edges)
                move_zone = local.y() <= 40
                owner._window_dragging = (
                    (not owner._window_resizing)
                    and move_zone
                    and not self._hit_titlebar_button_exclusion(local)
                )
                if owner._window_resizing:
                    owner.browser.setCursor(self._cursor_from_edges(owner._resize_edges))
                    return True
                if owner._window_dragging:
                    owner.browser.setCursor(qt.CursorShape.ClosedHandCursor)
                    return True

            alt_pressed = bool(self.q_application.keyboardModifiers() & qt.KeyboardModifier.AltModifier)
            if alt_pressed and not owner.window_locked:
                owner.drag_offset = self._global_point(event) - owner.frameGeometry().topLeft()
                return True

        if event.type() == q_event.Type.MouseButtonRelease and event.button() == qt.MouseButton.LeftButton:
            if owner._window_dragging or owner._window_resizing:
                owner._window_dragging = False
                owner._window_resizing = False
                owner._resize_edges = (False, False, False, False)
                owner.browser.setCursor(qt.CursorShape.ArrowCursor if not edit_mode else qt.CursorShape.OpenHandCursor)
                owner.config["window"]["x"] = owner.x()
                owner.config["window"]["y"] = owner.y()
                owner.config["window"]["width"] = owner.width()
                owner.config["window"]["height"] = owner.height()
                owner.sync_panel()
                return True

        if event.type() == q_event.Type.MouseMove and event.buttons() & qt.MouseButton.LeftButton:
            if edit_mode and (owner._window_dragging or owner._window_resizing):
                gp = self._global_point(event)
                delta = gp - owner._drag_start_global
                if owner._window_resizing:
                    self._apply_resize(owner._resize_edges, delta)
                else:
                    owner.move(owner._drag_start_geometry.topLeft() + delta)

                owner.config["window"]["x"] = owner.x()
                owner.config["window"]["y"] = owner.y()
                owner.config["window"]["width"] = owner.width()
                owner.config["window"]["height"] = owner.height()
                owner.sync_panel()
                return True

            alt_pressed = bool(self.q_application.keyboardModifiers() & qt.KeyboardModifier.AltModifier)
            if alt_pressed and not owner.window_locked:
                owner.move(self._global_point(event) - owner.drag_offset)
                owner.config["window"]["x"] = owner.x()
                owner.config["window"]["y"] = owner.y()
                owner.sync_panel()
                return True

        if event.type() == q_event.Type.MouseMove and edit_mode and not (event.buttons() & qt.MouseButton.LeftButton):
            local = self._local_point(watched, event)
            edges = self._hit_edges(local)
            if any(edges):
                owner.browser.setCursor(self._cursor_from_edges(edges))
            elif local.y() <= 40 and not self._hit_titlebar_button_exclusion(local):
                owner.browser.setCursor(qt.CursorShape.OpenHandCursor)
            else:
                owner.browser.setCursor(qt.CursorShape.ArrowCursor)
            return False

        if event.type() == q_event.Type.Leave and edit_mode and not (owner._window_dragging or owner._window_resizing):
            owner.browser.setCursor(qt.CursorShape.ArrowCursor)
            return False

        return None

    def _is_browser_related(self, obj: Any) -> bool:
        browser = self.owner.browser
        if obj is browser:
            return True
        if hasattr(obj, "parent"):
            p = obj.parent()
            while p is not None:
                if p is browser:
                    return True
                p = p.parent() if hasattr(p, "parent") else None
        return False

    def _event_pos_in_browser(self, watched: Any, event: Any) -> Any:
        if not hasattr(event, "position"):
            return self.q_point(0, 0)
        local = event.position().toPoint()
        if watched is self.owner.browser:
            return local
        if hasattr(watched, "mapTo"):
            try:
                return watched.mapTo(self.owner.browser, local)
            except Exception:
                return local
        return local

    def _global_point(self, event: Any) -> Any:
        return event.globalPosition().toPoint()

    def _local_point(self, watched: Any, event: Any) -> Any:
        return self._event_pos_in_browser(watched, event)

    def _hit_edges(self, point: Any) -> ResizeEdges:
        owner = self.owner
        rect = owner.rect()
        left = point.x() <= owner.resize_margin
        right = point.x() >= rect.width() - owner.resize_margin
        top = point.y() <= owner.resize_margin
        bottom = point.y() >= rect.height() - owner.resize_margin
        return (left, top, right, bottom)

    def _hit_titlebar_button_exclusion(self, point: Any) -> bool:
        if point.y() > 40:
            return False
        return point.x() >= max(0, self.owner.width() - 96)

    def _cursor_from_edges(self, edges: ResizeEdges) -> Any:
        left, top, right, bottom = edges
        cursor_shape = self.qt.CursorShape
        if (left and top) or (right and bottom):
            return cursor_shape.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return cursor_shape.SizeBDiagCursor
        if left or right:
            return cursor_shape.SizeHorCursor
        if top or bottom:
            return cursor_shape.SizeVerCursor
        return cursor_shape.ArrowCursor

    def _apply_resize(self, edges: ResizeEdges, delta: Any) -> None:
        owner = self.owner
        left, top, right, bottom = edges
        geometry = owner._drag_start_geometry
        min_w = max(owner.minimumWidth(), 120)
        min_h = max(owner.minimumHeight(), 120)

        new_left = geometry.left()
        new_top = geometry.top()
        new_right = geometry.right()
        new_bottom = geometry.bottom()

        if left:
            new_left = geometry.left() + delta.x()
            if new_right - new_left + 1 < min_w:
                new_left = new_right - min_w + 1
        if right:
            new_right = geometry.right() + delta.x()
            if new_right - new_left + 1 < min_w:
                new_right = new_left + min_w - 1
        if top:
            new_top = geometry.top() + delta.y()
            if new_bottom - new_top + 1 < min_h:
                new_top = new_bottom - min_h + 1
        if bottom:
            new_bottom = geometry.bottom() + delta.y()
            if new_bottom - new_top + 1 < min_h:
                new_bottom = new_top + min_h - 1

        owner.setGeometry(new_left, new_top, new_right - new_left + 1, new_bottom - new_top + 1)
