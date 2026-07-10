from __future__ import annotations

from typing import Any


class DesktopWebPermissionsController:
    def __init__(self, owner: Any, *, q_web_engine_page: Any) -> None:
        self.owner = owner
        self.q_web_engine_page = q_web_engine_page

    def feature_permission_policy(self, granted: bool):
        policy_enum = getattr(self.q_web_engine_page, "PermissionPolicy", None)
        if policy_enum is None:
            return None
        name = "PermissionGrantedByUser" if granted else "PermissionDeniedByUser"
        return getattr(policy_enum, name, None)

    def is_local_security_origin(self, security_origin: Any) -> bool:
        try:
            if security_origin.isLocalFile():
                return True
        except Exception:
            pass
        try:
            return str(security_origin.scheme() or "").lower() == "file"
        except Exception:
            return False

    def on_feature_permission_requested(self, security_origin: Any, feature: Any) -> None:
        page = self.owner.browser.page()
        grant_policy = self.feature_permission_policy(True)
        deny_policy = self.feature_permission_policy(False)
        if grant_policy is None or deny_policy is None:
            return

        audio_feature = self._feature("MediaAudioCapture")
        video_feature = self._feature("MediaVideoCapture")
        av_feature = self._feature("MediaAudioVideoCapture")

        if feature == audio_feature and self.is_local_security_origin(security_origin):
            page.setFeaturePermission(security_origin, feature, grant_policy)
            return
        if feature in {audio_feature, video_feature, av_feature}:
            page.setFeaturePermission(security_origin, feature, deny_policy)

    def _feature(self, name: str):
        return getattr(getattr(self.q_web_engine_page, "Feature", object), name, None)
