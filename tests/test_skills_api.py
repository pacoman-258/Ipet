from __future__ import annotations

import asyncio
import json
import unittest
from collections import deque
from contextlib import contextmanager
from pathlib import Path
import shutil
from uuid import uuid4
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend.agent_orchestrator import CapabilityPlan
from backend.agent_graph import GraphTurnOutcome
from backend.chat_topics import TopicStore
from backend.skills.manager import SkillManager
from backend.tool_runtime import Tool


TEST_TMP_ROOT = Path(__file__).resolve().parent / ".tmp_skills_api"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)


@contextmanager
def _workspace_tempdir() -> Path:
    tmp = TEST_TMP_ROOT / f"tmp_{uuid4().hex}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _write_skill(root: Path, *, name: str = "Repo Guide", description: str = "Guidance") -> Path:
    skill_dir = Path(root)
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f'name: "{name}"',
                f'description: "{description}"',
                "---",
                "",
                "Always inspect the repo before editing.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return skill_dir


def _write_script_skill(
    root: Path,
    *,
    name: str,
    description: str,
    script_name: str = "run_task",
    tool_allowlist: list[str] | None = None,
) -> Path:
    skill_dir = _write_skill(root, name=name, description=description)
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.joinpath(f"{script_name}.py").write_text(
        "\n".join(
            [
                '"""Run helper script."""',
                "",
                'if __name__ == "__main__":',
                "    print('{\"ok\": true}')",
            ]
        ),
        encoding="utf-8",
    )
    if tool_allowlist:
        skill_dir.joinpath("skill.json").write_text(
            json.dumps({"tool_allowlist": list(tool_allowlist)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return skill_dir


class _FakeHermesClient:
    def __init__(self, responses: list[dict] | None = None) -> None:
        self.responses = deque(responses or [])
        self.requests: list[tuple[str, str, dict | None]] = []

    async def request_json(self, method: str, path: str, *, json_payload: dict | None = None) -> dict:
        self.requests.append((method, path, json_payload))
        if self.responses:
            return self.responses.popleft()
        return {"ok": True, "runtime": "hermes"}


class _FakeRuntime:
    def __init__(self) -> None:
        self.start_state = None

    async def start_turn(self, state):
        self.start_state = state
        return GraphTurnOutcome(
            turn_id=str(state.get("turn_id") or ""),
            state=state,
            thought_summary="",
            approval_request=None,
            final_messages=list(state.get("prompt_messages") or []),
            trace=[],
        )

    def delete_turn(self, _turn_id: str) -> None:
        return None


class _FakeBridge:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = list(tools)

    def list_registered_tools(self) -> list[Tool]:
        return list(self._tools)

    def list_tools(self) -> list[dict]:
        return [tool.to_llm_schema() for tool in self._tools]

    def call_tool(self, tool_name: str, arguments: dict) -> object:
        for tool in self._tools:
            if tool.name == tool_name:
                return tool.call(arguments)
        raise KeyError(tool_name)


class SkillsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        backend_app.SESSION_STORE.clear()
        backend_app.PENDING_CHAT_TURNS.clear()
        backend_app._reset_agent_graph_runtime()
        self.topic_tmp = TEST_TMP_ROOT / f"topic_store_{uuid4().hex}"
        self.topic_tmp.mkdir(parents=True, exist_ok=True)
        backend_app._CHAT_TOPIC_STORE = TopicStore(self.topic_tmp / "chat_topics")
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()
        backend_app.SESSION_STORE.clear()
        backend_app.PENDING_CHAT_TURNS.clear()
        backend_app._reset_agent_graph_runtime()
        shutil.rmtree(self.topic_tmp, ignore_errors=True)

    def test_explicit_capability_inventory_scope_accepts_visibility_phrases(self) -> None:
        samples = {
            "能不能看到mcp": "mcp",
            "现在有什么mcp工具": "mcp",
            "能不能识别mcp": "mcp",
            "当前有什么技能": "skill",
            "帮我发现一下工具": "both",
            "看一下这个报错": "",
            "现在有什么安排": "",
        }

        for text, expected in samples.items():
            with self.subTest(text=text):
                self.assertEqual(backend_app._explicit_capability_inventory_scope(text), expected)

    def test_list_skills_endpoint_returns_skills_and_default_active_ids(self) -> None:
        fake_client = _FakeHermesClient(
            [
                {
                    "ok": True,
                    "runtime": "hermes",
                    "default_active_ids": ["repo-guide"],
                    "skills": [
                        {
                            "id": "repo-guide",
                            "aliases": [],
                            "storage_scope": "Hermes",
                        }
                    ],
                    "storage": {"runtime": "Hermes", "legacy": False},
                }
            ]
        )
        with mock.patch.object(backend_app, "_get_hermes_client", return_value=fake_client):
            resp = self.client.get("/api/skills")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["runtime"], "hermes")
        self.assertEqual(payload["storage"]["runtime"], "Hermes")
        self.assertFalse(payload["storage"]["legacy"])
        self.assertEqual(payload["default_active_ids"], ["repo-guide"])
        self.assertEqual(payload["skills"][0]["id"], "repo-guide")
        self.assertEqual(payload["skills"][0]["storage_scope"], "Hermes")
        self.assertEqual(fake_client.requests, [("GET", "/api/skills", None)])

    def test_list_skills_endpoint_canonicalizes_directory_alias_defaults(self) -> None:
        fake_client = _FakeHermesClient(
            [
                {
                    "ok": True,
                    "runtime": "hermes",
                    "default_active_ids": ["pptx"],
                    "skills": [
                        {
                            "id": "pptx",
                            "aliases": ["atr-pptx"],
                            "storage_scope": "Hermes",
                        }
                    ],
                }
            ]
        )
        with mock.patch.object(backend_app, "_get_hermes_client", return_value=fake_client):
            resp = self.client.get("/api/skills")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["default_active_ids"], ["pptx"])
        self.assertEqual(payload["skills"][0]["id"], "pptx")
        self.assertIn("atr-pptx", payload["skills"][0]["aliases"])
        self.assertEqual(payload["skills"][0]["storage_scope"], "Hermes")

    def test_normalize_settings_config_defaults_browser_skill_when_not_explicit(self) -> None:
        normalized = backend_app._normalize_settings_config({"chat": {"skills": {"enabled": True}}})
        self.assertEqual(normalized["chat"]["skills"]["default_active_ids"], ["browser-automation"])

    def test_normalize_settings_config_keeps_explicit_empty_default_skill_list(self) -> None:
        normalized = backend_app._normalize_settings_config(
            {"chat": {"skills": {"enabled": True, "default_active_ids": []}}}
        )
        self.assertEqual(normalized["chat"]["skills"]["default_active_ids"], [])

    def test_list_skills_endpoint_includes_builtin_browser_automation_default(self) -> None:
        fake_client = _FakeHermesClient(
            [
                {
                    "ok": True,
                    "runtime": "hermes",
                    "default_active_ids": ["browser-automation"],
                    "skills": [
                        {
                            "id": "browser-automation",
                            "name": "Browser Automation",
                            "description": "Windows or macOS browser automation helper",
                            "default_active": True,
                            "tool_allowlist": ["playwright.", "playwright_mcp."],
                            "script_count": 2,
                            "scripts": [
                                {"name": "resolve_mcp_recipe"},
                                {"name": "compact_browser_result"},
                            ],
                        }
                    ],
                }
            ]
        )
        with mock.patch.object(backend_app, "_get_hermes_client", return_value=fake_client):
            resp = self.client.get("/api/skills")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["default_active_ids"], ["browser-automation"])
        browser_skill = next(item for item in payload["skills"] if item["id"] == "browser-automation")
        self.assertTrue(browser_skill["default_active"])
        self.assertEqual(browser_skill["name"], "Browser Automation")
        self.assertEqual(browser_skill["tool_allowlist"], ["playwright.", "playwright_mcp."])
        self.assertEqual(browser_skill["script_count"], 2)
        self.assertEqual(
            [item["name"] for item in browser_skill["scripts"]],
            ["resolve_mcp_recipe", "compact_browser_result"],
        )
        self.assertIn("Windows or macOS", browser_skill["description"])

    def test_build_skill_capability_catalog_includes_browser_skill_cross_platform_excerpt(self) -> None:
        manager = SkillManager(backend_app.ROOT_DIR)
        runtime = backend_app.SkillRuntime(manager)
        available_tools = [
            Tool(
                name="playwright.browser_navigate",
                description="Navigate a page",
                input_schema={"type": "object", "properties": {}},
                invoke=lambda _args: {"ok": True},
            ),
            Tool(
                name="playwright_mcp.browser_click",
                description="Click an element",
                input_schema={"type": "object", "properties": {}},
                invoke=lambda _args: {"ok": True},
            ),
            Tool(
                name="read_file",
                description="Read a file",
                input_schema={"type": "object", "properties": {}},
                invoke=lambda _args: {"ok": True},
            ),
        ]
        with mock.patch.object(
            backend_app,
            "_get_skill_manager",
            side_effect=lambda force_reload=False: manager,
        ), mock.patch.object(
            backend_app,
            "_get_skill_runtime",
            side_effect=lambda force_reload=False: runtime,
        ), mock.patch.object(
            backend_app,
            "_list_non_skill_tools",
            return_value=available_tools,
        ):
            catalog = backend_app._build_skill_capability_catalog()

        browser_skill = next(item for item in catalog if item["skill_id"] == "browser-automation")
        self.assertEqual(
            browser_skill["tool_names"],
            ["playwright.browser_navigate", "playwright_mcp.browser_click"],
        )
        self.assertNotIn("skill.browser-automation.resolve_mcp_recipe", browser_skill["tool_names"])
        self.assertIn("Windows or macOS", browser_skill["description"])
        self.assertIn("open_page", browser_skill["prompt_excerpt"])
        self.assertIn("open_and_type", browser_skill["prompt_excerpt"])
        self.assertIn("keep user-provided file", browser_skill["prompt_excerpt"].lower())
        self.assertNotIn("resolve_mcp_recipe", browser_skill["prompt_excerpt"])

    def test_browser_automation_skill_prompt_keeps_direct_mcp_rule_and_optional_helpers(self) -> None:
        manager = SkillManager(backend_app.ROOT_DIR)
        record = manager.get_skill("browser-automation")

        self.assertIsNotNone(record)
        prompt_body = str(record.prompt_body)
        self.assertIn("Prefer direct MCP calls for simple single-step actions.", prompt_body)
        self.assertIn("Use `skill.browser-automation.compact_browser_result` only when browser output is too long", prompt_body)

    def test_import_local_and_delete_skill_endpoints(self) -> None:
        with _workspace_tempdir() as root:
            source_dir = _write_skill(root / "source-skill", name="Imported Skill")
            fake_client = _FakeHermesClient(
                [
                    {"ok": True, "runtime": "hermes", "skill": {"id": "imported-skill"}},
                    {"ok": True, "runtime": "hermes", "deleted": "imported-skill"},
                ]
            )
            with mock.patch.object(backend_app, "_get_hermes_client", return_value=fake_client):
                resp = self.client.post("/api/skills/import-local", json={"path": str(source_dir)})
                self.assertEqual(resp.status_code, 200)
                imported = resp.json()
                self.assertEqual(imported["skill"]["id"], "imported-skill")

                delete_resp = self.client.post("/api/skills/delete", json={"skill_id": "imported-skill"})

        self.assertEqual(delete_resp.status_code, 200)
        self.assertEqual(
            fake_client.requests,
            [
                (
                    "POST",
                    "/api/skills/import-local",
                    {"path": str(source_dir), "directory": "", "name": ""},
                ),
                (
                    "POST",
                    "/api/skills/delete",
                    {"skill_id": "imported-skill", "id": "", "name": ""},
                ),
            ],
        )

    def test_chat_stream_injects_skill_prompt_and_active_skill_ids(self) -> None:
        runtime = _FakeRuntime()

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "done"}

        resolved = SimpleNamespace(
            skill_ids=("repo-guide",),
            prompt_text="Skill injected prompt",
            tool_allowlist=(),
            resource_tools=(),
            adapter_tools=(),
            script_tools=(),
        )
        with mock.patch.object(backend_app, "_resolve_request_skills", return_value=resolved), mock.patch.object(
            backend_app,
            "_get_agent_graph_runtime",
            return_value=runtime,
        ), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "stream_final_reply", fake_stream_final_reply):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "hello",
                    "model": "demo",
                    "expression_mode": False,
                    "react_enabled": True,
                    "system_prompt": "Base prompt",
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(runtime.start_state)
        self.assertEqual(runtime.start_state["decision_messages"][0]["role"], "system")
        self.assertIn("Skill injected prompt", runtime.start_state["decision_messages"][0]["content"])
        prompt_messages = runtime.start_state["prompt_messages"]
        self.assertEqual(prompt_messages[0]["content"], "Base prompt")
        self.assertNotIn("Skill injected prompt", prompt_messages[0]["content"])
        self.assertEqual(runtime.start_state["active_skill_ids"], ["repo-guide"])
        self.assertIn('"active_skill_ids": ["repo-guide"]', resp.text)

    def test_chat_stream_react_mode_sanitizes_hidden_tool_names_from_skill_prompt(self) -> None:
        runtime = _FakeRuntime()

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "done"}

        resolved = SimpleNamespace(
            skill_ids=("daily-hotspots",),
            prompt_text=(
                "[Skill: Daily Hotspots]\n"
                "Follow this workflow exactly:\n\n"
                "1. Collect current trend data with `tavily-mcp.*` when possible.\n"
                "2. If extraction fails, fall back to `playwright.*`.\n"
                "3. Save the final report with `skill.daily-hotspots.save_report`.\n\n"
                "Dependencies:\n\n"
                "- Allowed MCP tools: `bilibili-search.*`, `tavily-mcp.*`, `playwright.*`\n"
                "- Local script tool: `skill.daily-hotspots.save_report`\n"
            ),
            tool_allowlist=(),
            resource_tools=(),
            adapter_tools=(),
            script_tools=(),
        )
        config = {
            "chat": {
                "skills": {"enabled": True, "default_active_ids": ["daily-hotspots"]},
                "topic_history": {"enabled": True, "summary_interval_assistant_turns": 10},
                "tooling": {"enabled": True, "max_tool_calls_per_turn": 6},
            }
        }

        with mock.patch.object(backend_app, "_load_full_config", return_value=config), mock.patch.object(
            backend_app,
            "_resolve_request_skills",
            return_value=resolved,
        ), mock.patch.object(
            backend_app,
            "_get_agent_graph_runtime",
            return_value=runtime,
        ), mock.patch.object(
            backend_app,
            "_get_mcp_bridge_for_tooling",
            return_value=mock.Mock(),
        ), mock.patch.object(
            backend_app,
            "stream_final_reply",
            fake_stream_final_reply,
        ):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "use the skill to collect the current trend data",
                    "model": "demo",
                    "expression_mode": False,
                    "react_enabled": True,
                    "chat_mode": "react",
                    "router_enabled": False,
                    "system_prompt": "Base prompt",
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(runtime.start_state)
        state = runtime.start_state
        self.assertIn("Use only capabilities exposed in the current runtime phase.", state["skill_prompt_text"])
        self.assertIn("Collect current trend data", state["skill_prompt_text"])
        for text in (
            state["skill_prompt_text"],
            state["decision_messages"][0]["content"],
        ):
            self.assertNotIn("playwright", text)
            self.assertNotIn("tavily-mcp", text)
            self.assertNotIn("bilibili-search", text)
            self.assertNotIn("skill.daily-hotspots.save_report", text)
            self.assertNotIn("Dependencies:", text)
        self.assertIn("`discovered MCP tool`", state["skill_prompt_text"])
        self.assertIn("`skill-local tool`", state["skill_prompt_text"])
        self.assertEqual(state["prompt_messages"][0]["content"], "Base prompt")

    def test_chat_stream_loads_full_config_once_and_logs_perf(self) -> None:
        runtime = _FakeRuntime()

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "done"}

        resolved = SimpleNamespace(
            skill_ids=(),
            prompt_text="",
            tool_allowlist=(),
            resource_tools=(),
            adapter_tools=(),
            script_tools=(),
        )
        load_calls: list[int] = []

        def fake_load_full_config():
            load_calls.append(1)
            return {
                "chat": {
                    "skills": {"enabled": True, "default_active_ids": []},
                    "topic_history": {"enabled": True, "summary_interval_assistant_turns": 10},
                    "tooling": {"enabled": True, "max_tool_calls_per_turn": 6},
                }
            }

        with mock.patch.object(backend_app, "_load_full_config", side_effect=fake_load_full_config), mock.patch.object(
            backend_app,
            "_resolve_request_skills",
            return_value=resolved,
        ) as resolve_mock, mock.patch.object(
            backend_app,
            "_get_agent_graph_runtime",
            return_value=runtime,
        ), mock.patch.object(
            backend_app,
            "_get_mcp_bridge_for_tooling",
            return_value=mock.Mock(),
        ), mock.patch.object(
            backend_app,
            "stream_final_reply",
            fake_stream_final_reply,
        ), self.assertLogs(backend_app.LOGGER.name, level="INFO") as logs:
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "hello",
                    "model": "demo",
                    "expression_mode": False,
                    "react_enabled": True,
                    "router_enabled": False,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(load_calls), 1)
        self.assertEqual(resolve_mock.call_count, 1)
        self.assertTrue(
            any(
                '"event": "chat_stream_perf"' in line and '"load_config"' in line and '"graph_start"' in line
                for line in logs.output
            )
        )

    def test_chat_stream_chat_mode_ignores_skills_and_reports_mode(self) -> None:
        runtime = _FakeRuntime()

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "done"}

        fake_bridge = _FakeBridge(
            [
                Tool(
                    name="tavily-mcp.search",
                    description="Search the web",
                    input_schema={"type": "object", "properties": {}},
                    invoke=lambda _args: {"ok": True},
                ),
                Tool(
                    name="playwright_mcp.browser_navigate",
                    description="Navigate",
                    input_schema={"type": "object", "properties": {}},
                    invoke=lambda _args: {"ok": True},
                ),
            ]
        )

        with mock.patch.object(
            backend_app,
            "_get_agent_graph_runtime",
            return_value=runtime,
        ), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge), mock.patch.object(
            backend_app,
            "_get_mcp_bridge_for_tooling",
            return_value=fake_bridge,
        ), mock.patch.object(
            backend_app, "stream_final_reply", fake_stream_final_reply
        ):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "hello",
                    "model": "demo",
                    "expression_mode": False,
                    "chat_mode": "chat",
                    "skill_ids": ["repo-guide"],
                    "system_prompt": "Base prompt",
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(runtime.start_state)
        self.assertEqual(runtime.start_state["chat_mode"], "chat")
        self.assertEqual(runtime.start_state["active_skill_ids"], [])
        self.assertEqual(runtime.start_state["skill_prompt_text"], "")
        prompt_messages = runtime.start_state["prompt_messages"]
        self.assertEqual(prompt_messages[0]["content"], "Base prompt")
        self.assertIn('"chat_mode": "chat"', resp.text)

    def test_chat_stream_chat_mode_without_tavily_allows_direct_answer(self) -> None:
        runtime = _FakeRuntime()

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "done"}

        fake_bridge = _FakeBridge(
            [
                Tool(
                    name="playwright_mcp.browser_navigate",
                    description="Navigate",
                    input_schema={"type": "object", "properties": {}},
                    invoke=lambda _args: {"ok": True},
                )
            ]
        )
        route = SimpleNamespace(
            route_kind="direct_answer",
            thought_summary="No search needed",
            skill_ids=[],
            tool_candidates=[],
            tool_call=None,
            search_needed=False,
            search_query="",
        )

        with mock.patch.object(backend_app, "_get_agent_graph_runtime", return_value=runtime), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge), mock.patch.object(
            backend_app,
            "_get_mcp_bridge_for_tooling",
            return_value=fake_bridge,
        ), mock.patch.object(
            backend_app, "stream_final_reply", fake_stream_final_reply
        ), mock.patch.object(backend_app, "classify_route", new=mock.AsyncMock(return_value=route)):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "hello",
                    "model": "demo",
                    "expression_mode": False,
                    "chat_mode": "chat",
                    "router_enabled": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(runtime.start_state["route_kind"], "direct_answer")
        self.assertEqual(runtime.start_state["route_search_query"], "")
        self.assertIn('"chat_mode": "chat"', resp.text)

    def test_chat_stream_chat_mode_without_tavily_downgrades_search_request(self) -> None:
        runtime = _FakeRuntime()

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "done"}

        fake_bridge = _FakeBridge(
            [
                Tool(
                    name="playwright_mcp.browser_navigate",
                    description="Navigate",
                    input_schema={"type": "object", "properties": {}},
                    invoke=lambda _args: {"ok": True},
                )
            ]
        )
        route = SimpleNamespace(
            route_kind="simple_tool_task",
            thought_summary="Need live search",
            skill_ids=[],
            tool_candidates=[],
            tool_call=None,
            search_needed=True,
            search_query="latest mcp docs",
        )

        with mock.patch.object(backend_app, "_get_agent_graph_runtime", return_value=runtime), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge), mock.patch.object(
            backend_app,
            "_get_mcp_bridge_for_tooling",
            return_value=fake_bridge,
        ), mock.patch.object(
            backend_app, "stream_final_reply", fake_stream_final_reply
        ), mock.patch.object(backend_app, "classify_route", new=mock.AsyncMock(return_value=route)):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "hello",
                    "model": "demo",
                    "expression_mode": False,
                    "chat_mode": "chat",
                    "router_enabled": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(runtime.start_state["route_kind"], "direct_answer")
        self.assertFalse(runtime.start_state["route_search_needed"])
        self.assertEqual(runtime.start_state["route_search_query"], "")
        fallback_prompts = [
            str(item.get("content") or "")
            for item in (runtime.start_state.get("prompt_messages") or [])
            if str(item.get("role") or "") == "system"
        ]
        self.assertFalse(any("Live web search is unavailable" in item for item in fallback_prompts))

    def test_chat_stream_skill_mode_requires_active_skill(self) -> None:
        with mock.patch.object(
            backend_app,
            "_load_full_config",
            return_value={"chat": {"skills": {"enabled": True, "default_active_ids": []}}},
        ):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "hello",
                    "model": "demo",
                    "expression_mode": False,
                    "chat_mode": "skill",
                    "skill_ids": [],
                },
            )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Skill", resp.json()["detail"])

    def test_build_runtime_tool_bridge_skill_mode_exposes_only_skill_tools(self) -> None:
        invocations: list[dict] = []
        skill_tool = Tool(
            name="skill.repo-guide.suggest_tests",
            description="Suggest tests",
            input_schema={"type": "object", "properties": {}},
            invoke=lambda arguments: invocations.append(arguments) or {"ok": True},
        )
        resolved = SimpleNamespace(
            skill_ids=("repo-guide",),
            prompt_text="Skill prompt",
            tool_allowlist=(),
            resource_tools=(),
            adapter_tools=(),
            script_tools=(skill_tool,),
        )

        with mock.patch.object(backend_app, "_resolve_request_skills", return_value=resolved):
            bridge = backend_app._build_runtime_tool_bridge(
                {
                    "chat_mode": "skill",
                    "active_skill_ids": ["repo-guide"],
                }
            )

        tool_names = [tool.name for tool in bridge.list_registered_tools()]
        self.assertEqual(tool_names, ["skill.repo-guide.suggest_tests"])
        result = bridge.call_tool("skill.repo-guide.suggest_tests", {"topic": "chat modes"})
        self.assertTrue(result.ok)
        self.assertEqual(invocations, [{"topic": "chat modes"}])

    def test_build_runtime_tool_bridge_skill_mode_exposes_allowlisted_mcp_tools(self) -> None:
        skill_tool = Tool(
            name="skill.repo-guide.suggest_tests",
            description="Suggest tests",
            input_schema={"type": "object", "properties": {}},
            invoke=lambda arguments: {"ok": True, "arguments": arguments},
        )
        resolved = SimpleNamespace(
            skill_ids=("repo-guide",),
            prompt_text="Skill prompt",
            tool_allowlist=("tavily-mcp.",),
            resource_tools=(),
            adapter_tools=(),
            script_tools=(skill_tool,),
        )
        fake_bridge = _FakeBridge(
            [
                Tool(
                    name="tavily-mcp.search",
                    description="Search the web",
                    input_schema={"type": "object", "properties": {}},
                    invoke=lambda _args: {"ok": True},
                ),
                Tool(
                    name="read_file",
                    description="Read a file",
                    input_schema={"type": "object", "properties": {}},
                    invoke=lambda _args: {"ok": True},
                ),
            ]
        )

        with mock.patch.object(backend_app, "_resolve_request_skills", return_value=resolved), mock.patch.object(
            backend_app,
            "_get_mcp_bridge",
            return_value=fake_bridge,
        ):
            bridge = backend_app._build_runtime_tool_bridge(
                {
                    "chat_mode": "skill",
                    "active_skill_ids": ["repo-guide"],
                }
            )

        tool_names = [tool.name for tool in bridge.list_registered_tools()]
        self.assertIn("skill.repo-guide.suggest_tests", tool_names)
        self.assertIn("tavily-mcp.search", tool_names)
        self.assertNotIn("read_file", tool_names)

    def test_chat_stream_react_skill_route_starts_in_skill_execution(self) -> None:
        runtime = _FakeRuntime()

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "done"}

        resolved = SimpleNamespace(
            skill_ids=("daily-hotspots",),
            prompt_text="Skill prompt",
            tool_allowlist=("tavily-mcp.",),
            resource_tools=(),
            adapter_tools=(),
            script_tools=(),
        )
        route = SimpleNamespace(
            route_kind="skill_task",
            thought_summary="Use the skill",
            skill_ids=["daily-hotspots"],
            tool_candidates=[],
            tool_call=None,
            search_needed=False,
            search_query="",
        )

        with mock.patch.object(backend_app, "_resolve_request_skills", return_value=resolved), mock.patch.object(
            backend_app,
            "_get_agent_graph_runtime",
            return_value=runtime,
        ), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(
            backend_app,
            "stream_final_reply",
            fake_stream_final_reply,
        ), mock.patch.object(
            backend_app,
            "classify_route",
            new=mock.AsyncMock(return_value=route),
        ):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "collect today's hotspots and save them",
                    "model": "demo",
                    "system_prompt": "ROLEPLAY_PROMPT",
                    "expression_mode": False,
                    "chat_mode": "react",
                    "router_enabled": True,
                },
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(runtime.start_state["execution_phase"], backend_app.PHASE_SKILL_SELECTION)
        self.assertEqual(runtime.start_state["selected_skill_ids"], [])
        self.assertEqual(runtime.start_state["decision_messages"][0]["role"], "system")
        self.assertIn("Skill prompt", runtime.start_state["decision_messages"][0]["content"])
        self.assertNotIn("ROLEPLAY_PROMPT", runtime.start_state["decision_messages"][0]["content"])
        self.assertIn("ROLEPLAY_PROMPT", runtime.start_state["prompt_messages"][0]["content"])
        self.assertNotIn("Skill prompt", runtime.start_state["prompt_messages"][0]["content"])

    def test_build_execution_plan_for_state_uses_planner_config(self) -> None:
        captured: dict[str, object] = {}

        class _FakePlan:
            def to_dict(self) -> dict[str, object]:
                return {
                    "plan_id": "plan-1",
                    "plan_summary": "Use the planner model.",
                    "reason": "Planner config should win.",
                    "steps": [],
                }

        async def fake_build_execution_plan(**kwargs):
            captured.update(kwargs)
            return _FakePlan()

        state = {
            "tooling_config": {},
            "model": "main-model",
            "llm_provider": "main-provider",
            "api_base_url": "main-url",
            "api_key": "main-key",
            "settings_config": {
                "chat": {
                    "router_llm_provider": "planner-provider",
                    "router_api_base_url": "planner-url",
                    "router_api_key": "planner-key",
                    "router_model": "planner-model",
                }
            },
            "decision_messages": [{"role": "user", "content": "hello"}],
        }
        searcher_result = {"mode": "task_types", "matched_tool_names": []}

        with mock.patch.object(backend_app, "build_execution_plan", side_effect=fake_build_execution_plan):
            plan = asyncio.run(backend_app._build_execution_plan_for_state(state, searcher_result))

        self.assertEqual(plan["plan_id"], "plan-1")
        self.assertEqual(captured["model"], "main-model")
        self.assertEqual(captured["llm_provider"], "openai-codex")
        self.assertEqual(captured["api_base_url"], "")
        self.assertEqual(captured["api_key"], "")

    def test_chat_stream_react_explicit_skill_request_forces_skill_task(self) -> None:
        runtime = _FakeRuntime()

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "done"}

        skill_record = SimpleNamespace(
            skill_id="daily-hotspots",
            name="Daily Hotspots",
            display_name="Daily Hotspots",
            aliases=("hotspots", "daily-hotspots"),
        )
        resolved = SimpleNamespace(
            skill_ids=("daily-hotspots",),
            skills=(skill_record,),
            prompt_text="Skill prompt",
            tool_allowlist=("tavily-mcp.",),
            resource_tools=(),
            adapter_tools=(),
            script_tools=(),
        )
        route = SimpleNamespace(
            route_kind="direct_answer",
            thought_summary="I can answer directly.",
            skill_ids=[],
            tool_candidates=[],
            tool_call=None,
            search_needed=False,
            search_query="",
        )

        with mock.patch.object(backend_app, "_resolve_request_skills", return_value=resolved), mock.patch.object(
            backend_app,
            "_get_agent_graph_runtime",
            return_value=runtime,
        ), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(
            backend_app,
            "_get_mcp_bridge_for_tooling",
            return_value=mock.Mock(),
        ), mock.patch.object(
            backend_app,
            "stream_final_reply",
            fake_stream_final_reply,
        ), mock.patch.object(
            backend_app,
            "classify_route",
            new=mock.AsyncMock(return_value=route),
        ):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "use the skill route to finish the task",
                    "model": "demo",
                    "system_prompt": "ROLEPLAY_PROMPT",
                    "expression_mode": False,
                    "chat_mode": "react",
                    "router_enabled": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(runtime.start_state["route_kind"], "skill_task")
        self.assertEqual(runtime.start_state["execution_phase"], backend_app.PHASE_SKILL_EXECUTION)
        self.assertEqual(runtime.start_state["selected_skill_ids"], ["daily-hotspots"])

    def test_chat_stream_allows_reasoning_steps_above_previous_cap(self) -> None:
        runtime = _FakeRuntime()

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "done"}

        with mock.patch.object(
            backend_app,
            "_get_agent_graph_runtime",
            return_value=runtime,
        ), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(
            backend_app,
            "stream_final_reply",
            fake_stream_final_reply,
        ):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "hello",
                    "model": "demo",
                    "expression_mode": False,
                    "chat_mode": "react",
                    "router_enabled": False,
                    "max_reasoning_steps": 25,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(runtime.start_state["max_reasoning_steps"], 25)

    def test_chat_stream_router_disabled_skips_classifier(self) -> None:
        runtime = _FakeRuntime()

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "done"}

        with mock.patch.object(
            backend_app,
            "_get_agent_graph_runtime",
            return_value=runtime,
        ), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(
            backend_app,
            "stream_final_reply",
            fake_stream_final_reply,
        ), mock.patch.object(backend_app, "classify_route", new_callable=mock.AsyncMock) as classify_mock:
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "hello",
                    "model": "demo",
                    "expression_mode": False,
                    "router_enabled": False,
                },
            )

        self.assertEqual(resp.status_code, 200)
        classify_mock.assert_not_awaited()
        self.assertFalse(runtime.start_state["router_used"])

    def test_chat_stream_router_chat_mode_builds_search_tool_call(self) -> None:
        runtime = _FakeRuntime()

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "done"}

        fake_bridge = _FakeBridge(
            [
                Tool(
                    name="tavily-mcp.search",
                    description="Search the web",
                    input_schema={"type": "object", "properties": {}},
                    invoke=lambda _args: {"ok": True},
                )
            ]
        )
        route = SimpleNamespace(
            route_kind="simple_tool_task",
            thought_summary="Need search",
            skill_ids=[],
            tool_candidates=[],
            tool_call=None,
            search_needed=True,
            search_query="langgraph router",
        )

        with mock.patch.object(backend_app, "_get_agent_graph_runtime", return_value=runtime), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(
            backend_app,
            "_get_mcp_bridge",
            return_value=fake_bridge,
        ), mock.patch.object(
            backend_app,
            "_get_mcp_bridge_for_tooling",
            return_value=fake_bridge,
        ), mock.patch.object(
            backend_app,
            "stream_final_reply",
            fake_stream_final_reply,
        ), mock.patch.object(backend_app, "classify_route", new=mock.AsyncMock(return_value=route)):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "search it",
                    "model": "demo",
                    "expression_mode": False,
                    "chat_mode": "chat",
                    "router_enabled": True,
                },
        )

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(runtime.start_state["router_used"])
        self.assertEqual(runtime.start_state["route_kind"], "direct_answer")
        self.assertEqual(runtime.start_state["route_search_query"], "")
        self.assertEqual(runtime.start_state["route_tool_call"], {})
        self.assertIn('"router_used": false', resp.text.lower())

    def test_chat_stream_router_skill_mode_requires_valid_skill_result(self) -> None:
        route = SimpleNamespace(
            route_kind="skill_task",
            thought_summary="Use a skill",
            skill_ids=[],
            tool_candidates=[],
            tool_call=None,
            search_needed=False,
            search_query="",
        )

        with mock.patch.object(
            backend_app,
            "_load_full_config",
            return_value={"chat": {"skills": {"enabled": True, "default_active_ids": []}}},
        ), mock.patch.object(
            backend_app,
            "classify_route",
            new=mock.AsyncMock(return_value=route),
        ):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "use skills",
                    "model": "demo",
                    "expression_mode": False,
                    "chat_mode": "skill",
                    "router_enabled": True,
                },
            )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Skill", resp.json()["detail"])

    def test_build_runtime_tool_bridge_react_skill_selection_exposes_agent_loop_and_default_skill_tools(self) -> None:
        with _workspace_tempdir() as root:
            _write_script_skill(root / "skills" / "builtin" / "repo-guide", name="Repo Guide", description="Repo helper")
            _write_script_skill(
                root / "Hermes" / "skills" / "imported" / "daily-hotspots",
                name="Daily Hotspots",
                description="Collect daily hotspots",
            )
            manager = SkillManager(root)
            fake_bridge = _FakeBridge(
                [
                    Tool(
                        name="read_file",
                        description="Read a file",
                        input_schema={"type": "object", "properties": {}},
                        invoke=lambda _args: {"ok": True},
                    )
                ]
            )
            settings = {"chat": {"skills": {"enabled": True, "default_active_ids": ["repo-guide"]}}}
            with mock.patch.object(backend_app, "_get_skill_manager", side_effect=lambda force_reload=False: manager), mock.patch.object(
                backend_app,
                "_load_settings_config",
                return_value=settings,
            ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge):
                bridge = backend_app._build_runtime_tool_bridge(
                    {
                        "chat_mode": "react",
                        "execution_phase": backend_app.PHASE_SKILL_SELECTION,
                        "active_skill_ids": ["repo-guide"],
                        "selection_origin": "none",
                        "selected_skill_ids": [],
                        "selected_tool_names": [],
                        "planner_excluded_skill_ids": [],
                        "planner_excluded_tool_names": [],
                        "planner_retry_used": False,
                    }
                )
                tool_names = [tool.name for tool in bridge.list_registered_tools()]
                agent_loop_result = bridge.call_tool("system.agent_loop", {"task": "general fallback"})

        self.assertIn("skill.repo-guide.run_task", tool_names)
        self.assertIn("system.agent_loop", tool_names)
        self.assertNotIn("system.capability_search", tool_names)
        self.assertNotIn("read_file", tool_names)
        self.assertTrue(agent_loop_result.ok)
        self.assertIn("agent_loop", agent_loop_result.content)

    def test_build_runtime_tool_bridge_planner_selected_skill_exposes_skill_tools_and_allowlisted_mcp(self) -> None:
        with _workspace_tempdir() as root:
            _write_script_skill(
                root / "Hermes" / "skills" / "imported" / "daily-hotspots",
                name="Daily Hotspots",
                description="Collect daily hotspots",
                tool_allowlist=["read_file"],
            )
            manager = SkillManager(root)
            fake_bridge = _FakeBridge(
                [
                    Tool(
                        name="read_file",
                        description="Read a file",
                        input_schema={"type": "object", "properties": {}},
                        invoke=lambda _args: {"ok": True},
                    ),
                    Tool(
                        name="browser.navigate",
                        description="Navigate a page",
                        input_schema={"type": "object", "properties": {}},
                        invoke=lambda _args: {"ok": True},
                    ),
                ]
            )
            with mock.patch.object(backend_app, "_get_skill_manager", side_effect=lambda force_reload=False: manager), mock.patch.object(
                backend_app,
                "_get_mcp_bridge",
                return_value=fake_bridge,
            ):
                bridge = backend_app._build_runtime_tool_bridge(
                    {
                        "chat_mode": "react",
                        "execution_phase": backend_app.PHASE_SKILL_EXECUTION,
                        "active_skill_ids": [],
                        "selection_origin": "planner",
                        "selected_skill_ids": ["daily-hotspots"],
                        "selected_tool_names": [],
                        "planner_excluded_skill_ids": [],
                        "planner_excluded_tool_names": [],
                        "planner_retry_used": False,
                    }
                )
                tool_names = [tool.name for tool in bridge.list_registered_tools()]

        self.assertIn("skill.daily-hotspots.run_task", tool_names)
        self.assertIn("read_file", tool_names)
        self.assertNotIn("system.capability_search", tool_names)
        self.assertNotIn("browser.navigate", tool_names)

    def test_build_runtime_tool_bridge_agent_loop_initial_and_planner_selected_mcp_handoff(self) -> None:
        fake_bridge = _FakeBridge(
            [
                Tool(
                    name="read_file",
                    description="Read a file",
                    input_schema={"type": "object", "properties": {}},
                    invoke=lambda _args: {"ok": True},
                ),
                Tool(
                    name="browser.navigate",
                    description="Navigate a page",
                    input_schema={"type": "object", "properties": {}},
                    invoke=lambda _args: {"ok": True},
                ),
            ]
        )
        with mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge):
            search_bridge = backend_app._build_runtime_tool_bridge(
                {
                    "chat_mode": "react",
                    "execution_phase": backend_app.PHASE_AGENT_LOOP,
                    "active_skill_ids": [],
                    "user_text": "open the page",
                    "selection_origin": "none",
                    "selected_skill_ids": [],
                    "selected_tool_names": [],
                    "planner_excluded_skill_ids": [],
                    "planner_excluded_tool_names": [],
                    "planner_retry_used": False,
                }
            )
            initial_names = [tool.name for tool in search_bridge.list_registered_tools()]
            discovered_bridge = backend_app._build_runtime_tool_bridge(
                {
                    "chat_mode": "react",
                    "execution_phase": backend_app.PHASE_AGENT_LOOP,
                    "active_skill_ids": [],
                    "user_text": "open the page",
                    "selection_origin": "planner",
                    "selected_skill_ids": [],
                    "selected_tool_names": ["read_file"],
                    "planner_excluded_skill_ids": [],
                    "planner_excluded_tool_names": [],
                    "planner_retry_used": False,
                }
            )
            discovered_names = [tool.name for tool in discovered_bridge.list_registered_tools()]

        self.assertEqual(initial_names, ["read_file", "browser.navigate"])
        self.assertNotIn("system.capability_search", discovered_names)
        self.assertIn("read_file", discovered_names)
        self.assertNotIn("browser.navigate", discovered_names)

    def test_build_runtime_tool_bridge_explicit_inventory_request_still_exposes_runtime_tools(self) -> None:
        fake_bridge = _FakeBridge(
            [
                Tool(
                    name="playwright.browser_navigate",
                    description="Navigate a page",
                    input_schema={"type": "object", "properties": {}},
                    invoke=lambda _args: {"ok": True},
                )
            ]
        )
        with mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge):
            bridge = backend_app._build_runtime_tool_bridge(
                {
                    "chat_mode": "react",
                    "execution_phase": backend_app.PHASE_AGENT_LOOP,
                    "active_skill_ids": [],
                    "user_text": "閹兼粍婀佸▽鈩冩箒閸欘垳鏁ら惃?mcp",
                    "selection_origin": "none",
                    "selected_skill_ids": [],
                    "selected_tool_names": [],
                    "planner_excluded_skill_ids": [],
                    "planner_excluded_tool_names": [],
                    "planner_retry_used": False,
                }
            )
            tool_names = [tool.name for tool in bridge.list_registered_tools()]

        self.assertEqual(tool_names, ["playwright.browser_navigate"])

    def test_build_runtime_tool_bridge_no_capability_search_tool_is_exposed(self) -> None:
        fake_bridge = _FakeBridge(
            [
                Tool(
                    name="playwright.browser_navigate",
                    description="Navigate a page",
                    input_schema={"type": "object", "properties": {}},
                    invoke=lambda _args: {"ok": True},
                )
            ]
        )
        with mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge):
            bridge = backend_app._build_runtime_tool_bridge(
                {
                    "chat_mode": "react",
                    "execution_phase": backend_app.PHASE_AGENT_LOOP,
                    "active_skill_ids": [],
                    "user_text": "閹兼粍婀佸▽鈩冩箒閸欘垳鏁ら惃?mcp",
                    "selection_origin": "none",
                    "selected_skill_ids": [],
                    "selected_tool_names": [],
                    "planner_excluded_skill_ids": [],
                    "planner_excluded_tool_names": [],
                    "planner_retry_used": False,
                }
            )
            tool_names = [tool.name for tool in bridge.list_registered_tools()]

        self.assertEqual(tool_names, ["playwright.browser_navigate"])

    def test_plan_capabilities_for_state_only_exposes_default_skills_and_falls_back_to_mcp(self) -> None:
        captured: dict[str, object] = {}

        async def fake_plan_capabilities(**kwargs):
            captured.update(kwargs)
            return CapabilityPlan(
                selection_kind="mcp",
                skill_ids=[],
                tool_names=["read_file"],
                query="fallback to mcp",
                thought_summary="Use MCP",
                reason="No new skill remains.",
            )

        state = {
            "chat_mode": "react",
            "execution_phase": backend_app.PHASE_AGENT_LOOP,
            "active_skill_ids": ["repo-guide"],
            "selected_skill_ids": ["daily-hotspots"],
            "planner_excluded_skill_ids": [],
            "planner_excluded_tool_names": [],
            "decision_messages": [{"role": "user", "content": "find another way"}],
            "tooling_config": {"enabled": True, "max_tool_calls_per_turn": 6},
            "settings_config": {"chat": {"skills": {"enabled": True, "default_active_ids": ["repo-guide"]}}},
            "llm_provider": "ollama",
            "api_base_url": "",
            "api_key": "",
            "model": "demo",
        }

        with mock.patch.object(
            backend_app,
            "_get_skill_manager",
            return_value=SimpleNamespace(canonicalize_skill_ids=lambda ids: list(dict.fromkeys(ids))),
        ), mock.patch.object(
            backend_app,
            "_build_skill_capability_catalog",
            return_value=[
                {"skill_id": "repo-guide", "display_name": "Repo Guide"},
                {"skill_id": "daily-hotspots", "display_name": "Daily Hotspots"},
            ],
        ), mock.patch.object(
            backend_app,
            "_build_mcp_capability_catalog",
            return_value=[{"name": "read_file", "description": "Read a file", "source": "mcp"}],
        ), mock.patch.object(
            backend_app,
            "_resolve_capability_planner_config",
            return_value={"model": "demo", "llm_provider": "ollama", "api_base_url": "", "api_key": ""},
        ), mock.patch.object(
            backend_app,
            "plan_capabilities",
            side_effect=fake_plan_capabilities,
        ):
            payload = asyncio.run(backend_app._plan_capabilities_for_state(state, {"task": "fallback to mcp"}))

        self.assertEqual(captured["exclude_skill_ids"], [])
        self.assertEqual(captured["skill_catalog"], [{"skill_id": "repo-guide", "display_name": "Repo Guide"}])
        self.assertEqual(captured["tool_catalog"], [{"name": "read_file", "description": "Read a file", "source": "mcp"}])
        self.assertEqual(payload["selection_kind"], "mcp")
        self.assertEqual(payload["tool_names"], ["read_file"])

    def test_plan_capabilities_for_state_only_exposes_default_enabled_skills_to_planner(self) -> None:
        captured: dict[str, object] = {}

        async def fake_plan_capabilities(**kwargs):
            captured.update(kwargs)
            return CapabilityPlan(
                selection_kind="skill",
                skill_ids=["calendar-skill"],
                tool_names=[],
                query="pick another default skill",
                thought_summary="Use another default skill",
                reason="Only checked skills are planner-visible.",
            )

        state = {
            "chat_mode": "react",
            "execution_phase": backend_app.PHASE_AGENT_LOOP,
            "active_skill_ids": ["repo-guide"],
            "selected_skill_ids": [],
            "planner_excluded_skill_ids": [],
            "planner_excluded_tool_names": [],
            "decision_messages": [{"role": "user", "content": "pick another default skill"}],
            "tooling_config": {"enabled": True, "max_tool_calls_per_turn": 6},
            "settings_config": {"chat": {"skills": {"enabled": True, "default_active_ids": ["repo-guide", "calendar-skill"]}}},
            "llm_provider": "ollama",
            "api_base_url": "",
            "api_key": "",
            "model": "demo",
        }

        with mock.patch.object(
            backend_app,
            "_get_skill_manager",
            return_value=SimpleNamespace(canonicalize_skill_ids=lambda ids: list(dict.fromkeys(ids))),
        ), mock.patch.object(
            backend_app,
            "_build_skill_capability_catalog",
            return_value=[
                {"skill_id": "repo-guide", "display_name": "Repo Guide"},
                {"skill_id": "calendar-skill", "display_name": "Calendar Skill"},
                {"skill_id": "hidden-skill", "display_name": "Hidden Skill"},
            ],
        ), mock.patch.object(
            backend_app,
            "_build_mcp_capability_catalog",
            return_value=[{"name": "read_file", "description": "Read a file", "source": "mcp"}],
        ), mock.patch.object(
            backend_app,
            "_resolve_capability_planner_config",
            return_value={"model": "demo", "llm_provider": "ollama", "api_base_url": "", "api_key": ""},
        ), mock.patch.object(
            backend_app,
            "plan_capabilities",
            side_effect=fake_plan_capabilities,
        ):
            payload = asyncio.run(backend_app._plan_capabilities_for_state(state, {"task": "pick another default skill"}))

        self.assertEqual(captured["exclude_skill_ids"], [])
        self.assertEqual(
            captured["skill_catalog"],
            [
                {"skill_id": "repo-guide", "display_name": "Repo Guide"},
                {"skill_id": "calendar-skill", "display_name": "Calendar Skill"},
            ],
        )
        self.assertEqual(payload["selection_kind"], "skill")
        self.assertEqual(payload["skill_ids"], ["calendar-skill"])

    def test_plan_capabilities_for_state_explicit_skill_inventory_lists_only_default_enabled_skills(self) -> None:
        state = {
            "chat_mode": "react",
            "execution_phase": backend_app.PHASE_AGENT_LOOP,
            "active_skill_ids": ["repo-guide"],
            "selected_skill_ids": [],
            "planner_excluded_skill_ids": [],
            "planner_excluded_tool_names": [],
            "decision_messages": [{"role": "user", "content": "閹兼粈绔存稉瀣箛閸︺劍婀侀崫顏冪昂 skill"}],
            "tooling_config": {"enabled": True, "max_tool_calls_per_turn": 6},
            "settings_config": {"chat": {"skills": {"enabled": True, "default_active_ids": ["repo-guide", "calendar-skill"]}}},
            "llm_provider": "ollama",
            "api_base_url": "",
            "api_key": "",
            "model": "demo",
        }

        with mock.patch.object(
            backend_app,
            "_get_skill_manager",
            return_value=SimpleNamespace(canonicalize_skill_ids=lambda ids: list(dict.fromkeys(ids))),
        ), mock.patch.object(
            backend_app,
            "_build_skill_capability_catalog",
            return_value=[
                {"skill_id": "repo-guide", "display_name": "Repo Guide"},
                {"skill_id": "calendar-skill", "display_name": "Calendar Skill"},
                {"skill_id": "hidden-skill", "display_name": "Hidden Skill"},
            ],
        ), mock.patch.object(
            backend_app,
            "_build_mcp_capability_catalog",
            return_value=[{"name": "read_file", "description": "Read a file", "source": "mcp"}],
        ), mock.patch.object(
            backend_app,
            "plan_capabilities",
            side_effect=AssertionError("inventory mode should not call planner"),
        ):
            payload = asyncio.run(backend_app._plan_capabilities_for_state(state, {"task": "list available skills"}))

        self.assertEqual(payload["mode"], "inventory")
        self.assertEqual(payload["inventory_scope"], "skill")
        self.assertEqual(payload["inventory_skill_ids"], ["repo-guide", "calendar-skill"])
        self.assertEqual(payload["inventory_tool_names"], [])
        self.assertNotIn("hidden-skill", payload["inventory_skill_ids"])

    def test_plan_capabilities_for_state_explicit_mcp_inventory_returns_deterministic_empty_result(self) -> None:
        state = {
            "chat_mode": "react",
            "execution_phase": backend_app.PHASE_AGENT_LOOP,
            "active_skill_ids": [],
            "selected_skill_ids": [],
            "planner_excluded_skill_ids": [],
            "planner_excluded_tool_names": [],
            "decision_messages": [{"role": "user", "content": "list available mcp tools"}],
            "tooling_config": {"enabled": True, "max_tool_calls_per_turn": 6},
            "settings_config": {"chat": {"skills": {"enabled": True, "default_active_ids": ["repo-guide"]}}},
            "llm_provider": "ollama",
            "api_base_url": "",
            "api_key": "",
            "model": "demo",
        }

        with mock.patch.object(
            backend_app,
            "_get_skill_manager",
            return_value=SimpleNamespace(canonicalize_skill_ids=lambda ids: list(dict.fromkeys(ids))),
        ), mock.patch.object(
            backend_app,
            "_build_skill_capability_catalog",
            return_value=[{"skill_id": "repo-guide", "display_name": "Repo Guide"}],
        ), mock.patch.object(
            backend_app,
            "_build_mcp_capability_catalog",
            return_value=[],
        ), mock.patch.object(
            backend_app,
            "plan_capabilities",
            side_effect=AssertionError("inventory mode should not call planner"),
        ):
            payload = asyncio.run(backend_app._plan_capabilities_for_state(state, {"task": "list available mcp tools"}))

        self.assertEqual(payload["mode"], "inventory")
        self.assertEqual(payload["inventory_scope"], "mcp")
        self.assertEqual(payload["inventory_skill_ids"], [])
        self.assertEqual(payload["inventory_tool_names"], [])
        self.assertIn("did not find any available non-skill MCP tools", payload["inventory_summary"])


_LEGACY_LOCAL_CHAT_STREAM_TESTS = [
    name
    for name in dir(SkillsApiTests)
    if name.startswith("test_chat_stream_")
]
for _name in _LEGACY_LOCAL_CHAT_STREAM_TESTS:
    setattr(
        SkillsApiTests,
        _name,
        unittest.skip("legacy local AgentGraph chat stream path is now proxied by Hermes")(
            getattr(SkillsApiTests, _name)
        ),
    )


if __name__ == "__main__":
    unittest.main()
