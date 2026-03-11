import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from astrbot.api import logger
from astrbot.api import message_components as Comp
from astrbot.api.all import AstrBotConfig, Context, Star, register
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@register(
    "astrbot_plugin_jm_send",
    "codex",
    "Download JM album PDF via MCP and send to QQ",
    "1.1.2",
)
class JmSendPlugin(Star):
    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)
        self.config = config or AstrBotConfig()
        self._sem = asyncio.Semaphore(self._config_int("max_concurrency", 1))
        self._data_dir = Path(__file__).resolve().parent / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._last_download_file = self._data_dir / "last_download.json"

    @filter.command("jm", alias={"本子", "禁漫"})
    async def jm(self, event: AstrMessageEvent, album_id: str = ""):
        event.should_call_llm(True)
        event.stop_event()
        async for resp in self._handle_download(event, album_id, source="command"):
            yield resp

    @filter.command("jm_last")
    async def jm_last(self, event: AstrMessageEvent):
        event.should_call_llm(True)
        event.stop_event()
        last = self._load_last_download()
        if not last:
            yield event.plain_result("No download record yet.")
            return
        yield event.plain_result(
            "Last download:\n"
            f"album_id: {last.get('album_id', '')}\n"
            f"file: {last.get('filename', '')}\n"
            f"path: {last.get('pdf_path', '')}\n"
            f"time: {last.get('time', '')}"
        )

    @filter.regex(r"^.*(?:下载|下).*(?:本子|禁漫|jm|JM).*$", priority=10)
    async def jm_natural_language(self, event: AstrMessageEvent):
        if not self._config_bool("enable_nlp_trigger", True):
            return

        album_id = self._extract_album_id_from_text(event.get_message_str())
        if not album_id:
            return

        event.should_call_llm(True)
        event.stop_event()
        async for resp in self._handle_download(event, album_id, source="natural"):
            yield resp

    async def _handle_download(
        self,
        event: AstrMessageEvent,
        album_id: str,
        source: str = "unknown",
    ):
        album_id = self._normalize_album_id(album_id)
        if not album_id:
            yield event.plain_result("Usage: /jm <album_id>, example: /jm 350234")
            return

        if not self._is_allowed(event):
            yield event.plain_result("This command is not allowed in current chat.")
            return

        yield event.plain_result(f"Task accepted. Downloading album {album_id}, please wait...")

        async with self._sem:
            result = await self._call_mcp_download(album_id)

        if not result.get("ok"):
            err = result.get("error") or "unknown error"
            suggestion = result.get("suggestion")
            msg = f"Download failed: {err}"
            if suggestion:
                msg += f"\nHint: {suggestion}"
            last = self._load_last_download()
            if last and self._config_bool("report_local_path", True):
                msg += f"\nLast local file: {last.get('pdf_path', '')}"
            yield event.plain_result(msg)
            return

        pdf_path = str(result.get("pdf_path") or "")
        filename = str(result.get("filename") or Path(pdf_path).name)
        file_path = Path(pdf_path)
        if not pdf_path or not file_path.is_file():
            yield event.plain_result("Download finished but PDF file not found on disk.")
            return

        self._save_last_download(
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "source": source,
                "album_id": album_id,
                "filename": filename,
                "pdf_path": str(file_path.resolve()),
                "size_bytes": int(file_path.stat().st_size),
            }
        )

        sent, send_mode, send_error = await self._send_pdf(event, str(file_path.resolve()), filename)
        if sent:
            msg = f"Done. Sent: {filename} ({send_mode})"
            if self._config_bool("report_local_path", True):
                msg += f"\nLocal path: {str(file_path.resolve())}"
            yield event.plain_result(msg)
        else:
            msg = "PDF generated but upload failed."
            if send_error:
                msg += f"\nReason: {send_error}"
            if self._config_bool("report_local_path", True):
                msg += f"\nLocal path: {str(file_path.resolve())}"
            yield event.plain_result(msg)
            yield event.chain_result(
                [
                    Comp.Plain("Fallback file component:"),
                    Comp.File(name=filename, file=str(file_path.resolve())),
                ]
            )

    async def _call_mcp_download(self, album_id: str) -> dict[str, Any]:
        cwd = self._config_str("mcp_cwd", "").strip() or None
        command = self._config_str("mcp_command", "python")
        args = self._config_list("mcp_args", ["main.py"])

        preflight_error = self._preflight_mcp_process(command=command, args=args, cwd=cwd)
        if preflight_error:
            return {
                "ok": False,
                "error": preflight_error,
                "suggestion": (
                    "Set mcp_cwd to your jmmcp project directory and ensure mcp_command "
                    "uses an interpreter/environment that has jmcomic + img2pdf installed."
                ),
            }

        params = StdioServerParameters(
            command=command,
            args=args,
            cwd=cwd,
            env=self._build_mcp_env(),
            encoding="utf-8",
            encoding_error_handler="replace",
        )

        tool_name = self._config_str("mcp_tool_name", "download_jm_album_pdf")
        save_dir = self._config_str("save_dir", "")
        timeout_sec = float(self._config_int("tool_timeout_sec", 900))

        try:
            async with asyncio.timeout(timeout_sec):
                async with stdio_client(params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()

                        arguments: dict[str, Any] = {"album_id": album_id}
                        if save_dir:
                            arguments["save_dir"] = save_dir

                        call_ret = await session.call_tool(tool_name, arguments)

            data = self._extract_tool_payload(call_ret)
            if not isinstance(data, dict):
                return {"ok": False, "error": "invalid MCP response payload"}
            return data

        except TimeoutError:
            return {"ok": False, "error": f"MCP call timeout after {int(timeout_sec)}s"}
        except Exception as exc:
            summary = self._summarize_exception(exc)
            suggestion = self._suggest_from_error(summary)
            logger.exception("MCP call failed: %s", summary)
            ret: dict[str, Any] = {"ok": False, "error": summary}
            if suggestion:
                ret["suggestion"] = suggestion
            return ret

    def _extract_tool_payload(self, call_ret: Any) -> Any:
        structured = getattr(call_ret, "structuredContent", None)
        if structured is not None:
            return structured

        if getattr(call_ret, "isError", False):
            content = getattr(call_ret, "content", None)
            if isinstance(content, list):
                for item in content:
                    text = getattr(item, "text", None)
                    if isinstance(text, str) and text.strip():
                        return {"ok": False, "error": text.strip()}
            return {"ok": False, "error": "MCP tool returned isError=true"}

        content = getattr(call_ret, "content", None)
        if isinstance(content, list):
            for item in content:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    try:
                        return json.loads(text)
                    except Exception:
                        continue
        return None

    def _preflight_mcp_process(
        self,
        command: str,
        args: list[Any],
        cwd: str | None,
    ) -> str:
        if not command.strip():
            return "mcp_command is empty"

        # If command is an absolute/relative executable path, validate existence.
        command_path = Path(command)
        if any(sep in command for sep in ("\\", "/")) and not command_path.exists():
            return f"mcp_command not found: {command}"

        if args:
            first = str(args[0]).strip()
            # common case: args = ["main.py"]
            if first.endswith(".py"):
                script_path = Path(first)
                if not script_path.is_absolute():
                    if not cwd:
                        return (
                            f"mcp_cwd is empty and script is relative: {first}. "
                            "This may launch the wrong main.py."
                        )
                    target = Path(cwd) / script_path
                else:
                    target = script_path

                if not target.exists():
                    return f"MCP script not found: {target}"

        return ""

    def _iter_leaf_exceptions(self, exc: BaseException):
        nested = getattr(exc, "exceptions", None)
        if nested and isinstance(nested, (list, tuple)):
            for sub in nested:
                if isinstance(sub, BaseException):
                    yield from self._iter_leaf_exceptions(sub)
            return
        yield exc

    def _summarize_exception(self, exc: BaseException, max_items: int = 3) -> str:
        parts: list[str] = []
        for sub in self._iter_leaf_exceptions(exc):
            msg = str(sub).strip()
            label = sub.__class__.__name__
            text = f"{label}: {msg}" if msg else label
            if text not in parts:
                parts.append(text)
            if len(parts) >= max_items:
                break

        if not parts:
            raw = str(exc).strip()
            return f"{exc.__class__.__name__}: {raw}" if raw else exc.__class__.__name__
        return " | ".join(parts)

    def _suggest_from_error(self, error_text: str) -> str:
        text = error_text.lower()

        if (
            "winerror 2" in text
            or "no such file or directory" in text
            or "not recognized as an internal or external command" in text
        ):
            return (
                "MCP start command/path is invalid. Check mcp_command and mcp_args, "
                "and prefer absolute path for uvx/python executable."
            )

        if "winerror 5" in text or "permission denied" in text or "access is denied" in text:
            return (
                "Permission denied when starting MCP. In plugin mcp_env, set UV_CACHE_DIR and UV_TOOL_DIR "
                "to writable directories."
            )

        if "timeout" in text:
            return "Increase tool_timeout_sec and verify your MCP process can be started manually."

        if "download failed" in text or "connect" in text or "connection" in text:
            return (
                "MCP started but network/download failed. Check jmcomic access, proxy settings, "
                "and optional JM_OPTION_PATH."
            )

        return ""

    async def _send_pdf(
        self,
        event: AstrMessageEvent,
        pdf_path: str,
        filename: str,
    ) -> tuple[bool, str, str]:
        max_mb = self._config_int("max_file_mb", 200)
        size_mb = Path(pdf_path).stat().st_size / 1024 / 1024
        if size_mb > max_mb:
            return False, "size-check", f"file too large: {size_mb:.2f}MB > {max_mb}MB"

        errors: list[str] = []
        try:
            if self._config_bool("prefer_chain_send", True):
                try:
                    await event.send(MessageChain([Comp.File(name=filename, file=pdf_path)]))
                    return True, "chain-send", ""
                except Exception as exc:
                    errors.append(f"chain-send failed: {exc}")

            bot = getattr(event, "bot", None)
            api = getattr(bot, "api", None) if bot else None
            if api is None:
                return False, "api-send", "event.bot.api not available"

            group_id = self._extract_group_id(event)
            if group_id is not None:
                payload = {
                    "group_id": int(group_id),
                    "file": pdf_path,
                    "name": filename,
                }
                group_file_folder = self._config_str("group_file_folder", "").strip()
                if group_file_folder:
                    payload["folder"] = group_file_folder

                ret = await api.call_action(
                    "upload_group_file",
                    payload,
                )
                if self._action_ok(ret):
                    return True, "api-group", ""
                errors.append(f"upload_group_file returned: {ret}")

            user_id = self._extract_user_id(event)
            if user_id is not None:
                ret = await api.call_action(
                    "upload_private_file",
                    {
                        "user_id": int(user_id),
                        "file": pdf_path,
                        "name": filename,
                    },
                )
                if self._action_ok(ret):
                    return True, "api-private", ""
                errors.append(f"upload_private_file returned: {ret}")

        except Exception as exc:
            logger.exception("QQ file upload failed")
            errors.append(str(exc))

        return False, "all-failed", "; ".join(errors)

    def _action_ok(self, ret: Any) -> bool:
        if ret is None:
            return True
        if isinstance(ret, dict):
            status = ret.get("status")
            retcode = ret.get("retcode")
            if status in (None, "ok") and retcode in (None, 0):
                return True
            return False
        return True

    def _is_allowed(self, event: AstrMessageEvent) -> bool:
        allow_groups = self._config_list("allow_group_ids", [])
        if not allow_groups:
            return True

        group_id = self._extract_group_id(event)
        if group_id is None:
            return False
        return str(group_id) in {str(x) for x in allow_groups}

    def _extract_group_id(self, event: AstrMessageEvent) -> Optional[str]:
        message_obj = getattr(event, "message_obj", None)
        group_id = getattr(message_obj, "group_id", None) if message_obj else None
        if group_id is not None:
            return str(group_id)
        return None

    def _extract_user_id(self, event: AstrMessageEvent) -> Optional[str]:
        message_obj = getattr(event, "message_obj", None)
        user_id = getattr(message_obj, "user_id", None) if message_obj else None
        if user_id is not None:
            return str(user_id)

        sender_id = getattr(event, "get_sender_id", None)
        if callable(sender_id):
            try:
                sid = sender_id()
                if sid is not None:
                    return str(sid)
            except Exception:
                return None
        return None

    def _normalize_album_id(self, text: str) -> str:
        text = str(text or "").strip()
        if text.isdigit():
            return text
        return ""

    def _extract_album_id_from_text(self, text: str) -> str:
        text = str(text or "").strip()
        direct = self._normalize_album_id(text)
        if direct:
            return direct
        m = re.search(r"(?<!\d)(\d{5,9})(?!\d)", text)
        if m:
            return self._normalize_album_id(m.group(1))
        return ""

    def _save_last_download(self, data: dict[str, Any]) -> None:
        try:
            self._last_download_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("failed to write last download record")

    def _load_last_download(self) -> dict[str, Any] | None:
        try:
            if not self._last_download_file.exists():
                return None
            return json.loads(self._last_download_file.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("failed to load last download record")
            return None

    def _build_mcp_env(self) -> dict[str, str]:
        env = dict(os.environ)

        merged = self._config_dict("mcp_env", {})
        for k, v in merged.items():
            env[str(k)] = str(v)

        return env

    def _config_str(self, key: str, default: str) -> str:
        try:
            value = self.config.get(key, default)
        except Exception:
            value = default
        return str(value) if value is not None else default

    def _config_int(self, key: str, default: int) -> int:
        try:
            value = self.config.get(key, default)
            return int(value)
        except Exception:
            return default

    def _config_bool(self, key: str, default: bool) -> bool:
        try:
            value = self.config.get(key, default)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        except Exception:
            return default

    def _config_list(self, key: str, default: list[Any]) -> list[Any]:
        try:
            value = self.config.get(key, default)
            if isinstance(value, list):
                return value
        except Exception:
            pass
        return default

    def _config_dict(self, key: str, default: dict[str, Any]) -> dict[str, Any]:
        try:
            value = self.config.get(key, default)
            if isinstance(value, dict):
                return value
        except Exception:
            pass
        return default
