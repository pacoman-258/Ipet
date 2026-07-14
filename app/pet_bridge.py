from __future__ import annotations

from typing import Callable


def create_pet_bridge_class(
    qobject_base,
    signal_factory: Callable[..., object],
    slot_decorator: Callable[..., Callable],
    *,
    print_func: Callable[..., None] = print,
):
    class PetBridge(qobject_base):
        stateChanged = signal_factory(str)
        openSettingsRequested = signal_factory()
        startAsrWarmupRequested = signal_factory()
        minimizeWindowRequested = signal_factory()
        closeWindowRequested = signal_factory()
        showClickPreviewRequested = signal_factory(str)
        hideClickPreviewRequested = signal_factory()
        showApprovalNotificationRequested = signal_factory(str)
        approvalNotificationDecision = signal_factory(str)
        cancelApprovalNotificationRequested = signal_factory(str)

        @slot_decorator(str)
        def petStateChanged(self, payload: str) -> None:
            self.stateChanged.emit(payload)

        @slot_decorator(str)
        def log(self, text: str) -> None:
            print_func(f"[WEB] {text}")

        @slot_decorator()
        def openSettingsPage(self) -> None:
            self.openSettingsRequested.emit()

        @slot_decorator()
        def startAsrWarmup(self) -> None:
            self.startAsrWarmupRequested.emit()

        @slot_decorator()
        def minimizeWindow(self) -> None:
            self.minimizeWindowRequested.emit()

        @slot_decorator()
        def closeWindow(self) -> None:
            self.closeWindowRequested.emit()

        @slot_decorator(str)
        def showClickPreview(self, payload: str) -> None:
            self.showClickPreviewRequested.emit(str(payload or ""))

        @slot_decorator()
        def hideClickPreview(self) -> None:
            self.hideClickPreviewRequested.emit()

        @slot_decorator(str)
        def showApprovalNotification(self, payload: str) -> None:
            self.showApprovalNotificationRequested.emit(str(payload or ""))

        @slot_decorator(str)
        def cancelApprovalNotification(self, proposal_id: str) -> None:
            self.cancelApprovalNotificationRequested.emit(str(proposal_id or ""))

    return PetBridge
