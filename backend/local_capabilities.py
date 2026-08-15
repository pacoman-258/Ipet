from __future__ import annotations

import secrets


GAME_BROWSER_CAPABILITY_HEADER = "X-Ipet-Game-Capability"
GAME_BROWSER_CAPABILITY = secrets.token_urlsafe(32)


__all__ = ["GAME_BROWSER_CAPABILITY", "GAME_BROWSER_CAPABILITY_HEADER"]
