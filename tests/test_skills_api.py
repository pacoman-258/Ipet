from __future__ import annotations

import unittest
from contextlib import contextmanager
from pathlib import Path
import shutil
from uuid import uuid4
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from fastapi.testclient import TestClient

import backend.app as backend_app
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


def _write_script_skill(root: Path, *, name: str, description: str, script_name: str = "run_task") -> Path:
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
    return skill_dir


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

    def test_list_skills_endpoint_returns_skills_and_default_active_ids(self) -> None:
        with _workspace_tempdir() as root:
            _write_skill(root / "skills" / "builtin" / "repo-guide")
            manager = SkillManager(root)
            settings = {"chat": {"skills": {"enabled": True, "default_active_ids": ["repo-guide"]}}}
            with mock.patch.object(
                backend_app,
                "_get_skill_manager",
                side_effect=lambda force_reload=False: manager,
            ), mock.patch.object(backend_app, "_load_settings_config", return_value=settings):
                resp = self.client.get("/api/skills")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["default_active_ids"], ["repo-guide"])
        self.assertEqual(payload["skills"][0]["id"], "repo-guide")

    def test_list_skills_endpoint_canonicalizes_directory_alias_defaults(self) -> None:
        with _workspace_tempdir() as root:
            _write_skill(root / "third_party_skills" / "atr-pptx", name="pptx", description="PPTX helper")
            manager = SkillManager(root)
            settings = {"chat": {"skills": {"enabled": True, "default_active_ids": ["atr-pptx"]}}}
            with mock.patch.object(
                backend_app,
                "_get_skill_manager",
                side_effect=lambda force_reload=False: manager,
            ), mock.patch.object(backend_app, "_load_settings_config", return_value=settings):
                resp = self.client.get("/api/skills")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["default_active_ids"], ["pptx"])
        self.assertEqual(payload["skills"][0]["id"], "pptx")
        self.assertIn("atr-pptx", payload["skills"][0]["aliases"])

    def test_import_local_and_delete_skill_endpoints(self) -> None:
        with _workspace_tempdir() as root:
            source_dir = _write_skill(root / "source-skill", name="Imported Skill")
            manager = SkillManager(root)
            saved_configs: list[dict] = []
            settings = {"chat": {"skills": {"enabled": True, "default_active_ids": ["imported-skill"]}}}
            with mock.patch.object(
                backend_app,
                "_get_skill_manager",
                side_effect=lambda force_reload=False: manager,
            ), mock.patch.object(backend_app, "_load_settings_config", return_value=settings), mock.patch.object(
                backend_app,
                "_save_full_config",
                side_effect=lambda config: saved_configs.append(config),
            ):
                resp = self.client.post("/api/skills/import-local", json={"path": str(source_dir)})
                self.assertEqual(resp.status_code, 200)
                imported = resp.json()
                self.assertEqual(imported["skill"]["id"], "imported-skill")

                delete_resp = self.client.post("/api/skills/delete", json={"skill_id": "imported-skill"})

        self.assertEqual(delete_resp.status_code, 200)
        self.assertTrue(saved_configs)
        self.assertEqual(saved_configs[-1]["chat"]["skills"]["default_active_ids"], [])

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
        prompt_messages = runtime.start_state["prompt_messages"]
        self.assertIn("Skill injected prompt", prompt_messages[0]["content"])
        self.assertEqual(runtime.start_state["active_skill_ids"], ["repo-guide"])
        self.assertIn('"active_skill_ids": ["repo-guide"]', resp.text)

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

    def test_chat_stream_chat_mode_requires_tavily_tools(self) -> None:
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

        with mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "hello",
                    "model": "demo",
                    "expression_mode": False,
                    "chat_mode": "chat",
                },
            )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("tavily-mcp", resp.json()["detail"])

    def test_chat_stream_skill_mode_requires_active_skill(self) -> None:
        with mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={"chat": {"skills": {"enabled": True, "default_active_ids": []}}},
        ), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
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
        self.assertEqual(runtime.start_state["execution_phase"], backend_app.PHASE_SKILL_EXECUTION)
        self.assertEqual(runtime.start_state["selected_skill_ids"], ["daily-hotspots"])
        self.assertEqual(runtime.start_state["decision_messages"][0]["role"], "system")
        self.assertIn("Skill prompt", runtime.start_state["decision_messages"][0]["content"])
        self.assertNotIn("ROLEPLAY_PROMPT", runtime.start_state["decision_messages"][0]["content"])
        self.assertIn("ROLEPLAY_PROMPT", runtime.start_state["prompt_messages"][0]["content"])
        self.assertIn("Skill prompt", runtime.start_state["prompt_messages"][0]["content"])

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
        self.assertTrue(runtime.start_state["router_used"])
        self.assertEqual(runtime.start_state["route_kind"], "simple_tool_task")
        self.assertEqual(runtime.start_state["route_search_query"], "langgraph router")
        self.assertEqual(runtime.start_state["route_tool_call"]["name"], "tavily-mcp.search")
        self.assertIn('"router_used": true', resp.text.lower())

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
            "_load_settings_config",
            return_value={"chat": {"skills": {"enabled": True, "default_active_ids": []}}},
        ), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
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

    def test_build_runtime_tool_bridge_react_skill_selection_exposes_system_search_and_default_skill_tools(self) -> None:
        with _workspace_tempdir() as root:
            _write_script_skill(root / "skills" / "builtin" / "repo-guide", name="Repo Guide", description="Repo helper")
            _write_script_skill(root / "third_party_skills" / "daily-hotspots", name="Daily Hotspots", description="Collect daily hotspots")
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
                        "discovered_skill_ids": [],
                        "discovered_tool_names": [],
                        "selected_skill_ids": [],
                    }
                )
                tool_names = [tool.name for tool in bridge.list_registered_tools()]
                search_result = bridge.call_tool("system.skill_search", {"query": "hotspots"})

        self.assertIn("skill.repo-guide.run_task", tool_names)
        self.assertIn("system.skill_search", tool_names)
        self.assertIn("system.agent_loop", tool_names)
        self.assertNotIn("read_file", tool_names)
        self.assertTrue(search_result.ok)
        self.assertIn("daily-hotspots", search_result.content)
        self.assertNotIn("repo-guide", search_result.content)

    def test_build_runtime_tool_bridge_agent_loop_only_exposes_discovered_tools_after_search(self) -> None:
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
                    "discovered_skill_ids": [],
                    "discovered_tool_names": [],
                    "selected_skill_ids": [],
                }
            )
            initial_names = [tool.name for tool in search_bridge.list_registered_tools()]
            search_result = search_bridge.call_tool("system.tool_search", {"query": "read"})
            discovered_bridge = backend_app._build_runtime_tool_bridge(
                {
                    "chat_mode": "react",
                    "execution_phase": backend_app.PHASE_AGENT_LOOP,
                    "active_skill_ids": [],
                    "discovered_skill_ids": [],
                    "discovered_tool_names": ["read_file"],
                    "selected_skill_ids": [],
                }
            )
            discovered_names = [tool.name for tool in discovered_bridge.list_registered_tools()]

        self.assertEqual(initial_names, ["system.tool_search"])
        self.assertTrue(search_result.ok)
        self.assertIn("read_file", search_result.content)
        self.assertIn("system.tool_search", discovered_names)
        self.assertIn("read_file", discovered_names)
        self.assertNotIn("browser.navigate", discovered_names)


if __name__ == "__main__":
    unittest.main()
