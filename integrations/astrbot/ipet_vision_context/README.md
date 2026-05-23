# Deprecated Ipet Vision Context AstrBot Reference

This directory is deprecated/reference-only. It is not the supported runtime
path, and it should not be installed as the recommended AstrBot production
plugin.

The supported path is Ipet-owned: the Vision Broker runs before
`/api/chat/stream` enters Hermes, AstrBot, or any future runtime. Ipet owns the
passive timeline, active observe, and the Observe-Reason-Retry target/retry/stop
loop. Runtimes receive bounded text evidence by default. Raw screenshots are not
persisted and are not passed to AstrBot by default.

`/api/vision/observe` remains an Ipet local API for the desktop host, debugging,
and local experiments. It is not an AstrBot plugin dependency.

These files remain only to document the old shape and keep compatibility tests
around small helper functions. If you experiment with them manually, the local
vision endpoints require the same token that Ipet uses for screen frame capture:
set `IPET_LOCAL_API_TOKEN` and send it as `X-Ipet-Local-Token`.

Important boundaries:

- Do not treat this directory as the production vision path.
- Do not make AstrBot, a plugin, or the model own active screenshot policy, target selection, retry, or stopping.
- Do not persist Ipet vision context in AstrBot memory or knowledge bases.
- Do not pass raw screenshots to AstrBot in the default runtime path.
- Treat passive timeline as background context only. Without active broker evidence, the runtime must not claim it just inspected a fresh screenshot.
- Treat missing or stale context as unavailable. If passive evidence is insufficient, the current runtime path should let the Ipet broker gather active evidence before the runtime call.
- If evidence is still unsupported, answer “我无法从当前截图确认” without inventing observations.

Legacy helper behavior:

1. Ipet captures locally and keeps only the latest frame plus the latest 5 passive semantic screen-change events in memory.
2. `ipet_passive_vision_timeline` can request `GET http://127.0.0.1:8008/api/vision/context?lane=passive` with `X-Ipet-Local-Token` for old experiments.
3. `ipet_active_observe(text, target_hint, attempt_reason, exclude_seen)` can request a single `POST http://127.0.0.1:8008/api/vision/observe?lane=active&include_image=true` for manual compatibility checks.
4. The helper may return one-shot `base64://...` image URLs because old experiments used them. That is not the default runtime path and should not be wired into production AstrBot chat.
5. Current production flow does not hide a retry loop inside AstrBot. Ipet broker owns retry/stop before the runtime receives bounded evidence text.
