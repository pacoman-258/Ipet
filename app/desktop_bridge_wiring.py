from __future__ import annotations

from typing import Callable


_PET_BRIDGE_SIGNAL_HANDLERS = (
    ("stateChanged", "on_web_state_changed"),
    ("openSettingsRequested", "open_settings_page"),
    ("startAsrWarmupRequested", "request_asr_warmup"),
    ("minimizeWindowRequested", "showMinimized"),
    ("closeWindowRequested", "close"),
    ("showClickPreviewRequested", "show_click_preview"),
    ("hideClickPreviewRequested", "hide_click_preview"),
)


def wire_pet_bridge(owner, bridge):
    for signal_name, handler_name in _PET_BRIDGE_SIGNAL_HANDLERS:
        getattr(bridge, signal_name).connect(getattr(owner, handler_name))
    return bridge


def create_connected_pet_bridge(owner, bridge_factory: Callable[[], object]):
    return wire_pet_bridge(owner, bridge_factory())
