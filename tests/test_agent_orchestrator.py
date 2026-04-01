from __future__ import annotations

import json
import unittest

from backend.agent_orchestrator import (
    _normalize_tool_candidates,
    build_inventory_result_message,
    build_execution_plan,
    plan_capabilities,
    search_capabilities,
)


class _FakePlannerAdapter:
    def __init__(self, responder):
        self._responder = responder
        self.calls: list[dict[str, object]] = []

    async def chat_once(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self._responder(**kwargs)


class CapabilityPlannerTests(unittest.IsolatedAsyncioTestCase):
    def test_normalize_tool_candidates_accepts_unique_leaf_alias(self) -> None:
        resolved = _normalize_tool_candidates(
            ["browser_navigate"],
            {"playwright_mcp.browser_navigate", "read_file"},
        )

        self.assertEqual(resolved, ["playwright_mcp.browser_navigate"])

    def test_normalize_tool_candidates_keeps_ambiguous_leaf_alias_unresolved(self) -> None:
        resolved = _normalize_tool_candidates(
            ["browser_navigate"],
            {"playwright_mcp.browser_navigate", "other.browser_navigate"},
        )

        self.assertEqual(resolved, [])

    async def test_plan_capabilities_prefers_skill_selection(self) -> None:
        adapter = _FakePlannerAdapter(
            lambda **_kwargs: {
                "content": json.dumps(
                    {
                        "selection_kind": "skill",
                        "skill_ids": ["calendar-skill"],
                        "tool_names": [],
                        "query": "schedule the meeting",
                        "thought_summary": "Use the skill workflow",
                        "reason": "The installed skill already covers this task.",
                    },
                    ensure_ascii=False,
                )
            }
        )

        result = await plan_capabilities(
            messages=[{"role": "user", "content": "schedule the meeting"}],
            model="demo",
            skill_catalog=[
                {
                    "skill_id": "calendar-skill",
                    "display_name": "Calendar Skill",
                    "description": "Schedules meetings",
                    "tool_names": ["skill.calendar-skill.run_task"],
                    "prompt_summary": "Meeting workflow",
                }
            ],
            tool_catalog=[
                {"name": "browser.navigate", "description": "Navigate a page", "source": "mcp"}
            ],
            provider_adapter=adapter,
        )

        self.assertEqual(result.selection_kind, "skill")
        self.assertEqual(result.skill_ids, ["calendar-skill"])
        self.assertEqual(result.tool_names, [])
        self.assertEqual(result.query, "schedule the meeting")

    async def test_plan_capabilities_falls_back_to_mcp(self) -> None:
        adapter = _FakePlannerAdapter(
            lambda **_kwargs: {
                "content": json.dumps(
                    {
                        "selection_kind": "mcp",
                        "skill_ids": [],
                        "tool_names": ["browser.navigate"],
                        "query": "open the page",
                        "thought_summary": "Use the concrete MCP tool",
                        "reason": "No skill matches the request.",
                    },
                    ensure_ascii=False,
                )
            }
        )

        result = await plan_capabilities(
            messages=[{"role": "user", "content": "open the page"}],
            model="demo",
            skill_catalog=[],
            tool_catalog=[
                {"name": "browser.navigate", "description": "Navigate a page", "source": "mcp"}
            ],
            provider_adapter=adapter,
        )

        self.assertEqual(result.selection_kind, "mcp")
        self.assertEqual(result.skill_ids, [])
        self.assertEqual(result.tool_names, ["browser.navigate"])

    async def test_plan_capabilities_respects_exclusions(self) -> None:
        def responder(**kwargs):
            prompt = str((kwargs.get("messages") or [{}])[0].get("content") or "")
            if "skip-skill" in prompt and "read_file" in prompt:
                payload = {
                    "selection_kind": "mcp",
                    "skill_ids": [],
                    "tool_names": ["browser.navigate"],
                    "query": "fallback option",
                    "thought_summary": "Use the fallback MCP tool",
                    "reason": "The excluded skill and tool should not be reused.",
                }
            else:
                payload = {
                    "selection_kind": "skill",
                    "skill_ids": ["skip-skill"],
                    "tool_names": [],
                    "query": "fallback option",
                    "thought_summary": "Use the excluded skill",
                    "reason": "This should not happen if exclusions were passed.",
                }
            return {"content": json.dumps(payload, ensure_ascii=False)}

        adapter = _FakePlannerAdapter(responder)

        result = await plan_capabilities(
            messages=[{"role": "user", "content": "fallback option"}],
            model="demo",
            skill_catalog=[
                {
                    "skill_id": "skip-skill",
                    "display_name": "Skip Skill",
                    "description": "Should be excluded",
                    "tool_names": ["skill.skip-skill.run_task"],
                    "prompt_summary": "Excluded workflow",
                }
            ],
            tool_catalog=[
                {"name": "read_file", "description": "Read a file", "source": "mcp"},
                {"name": "browser.navigate", "description": "Navigate a page", "source": "mcp"},
            ],
            exclude_skill_ids=["skip-skill"],
            exclude_tool_names=["read_file"],
            provider_adapter=adapter,
        )

        self.assertEqual(result.selection_kind, "mcp")
        self.assertEqual(result.tool_names, ["browser.navigate"])
        prompt = str(adapter.calls[0]["messages"][0]["content"])
        self.assertIn("skip-skill", prompt)
        self.assertIn("read_file", prompt)

    async def test_plan_capabilities_malformed_json_returns_none(self) -> None:
        adapter = _FakePlannerAdapter(lambda **_kwargs: {"content": "definitely not json"})

        result = await plan_capabilities(
            messages=[{"role": "user", "content": "do anything"}],
            model="demo",
            skill_catalog=[
                {
                    "skill_id": "repo-guide",
                    "display_name": "Repo Guide",
                    "description": "Reads repos",
                    "tool_names": ["skill.repo-guide.run_task"],
                    "prompt_summary": "Repo workflow",
                }
            ],
            tool_catalog=[
                {"name": "read_file", "description": "Read a file", "source": "mcp"}
            ],
            provider_adapter=adapter,
        )

        self.assertEqual(result.selection_kind, "none")
        self.assertEqual(result.skill_ids, [])
        self.assertEqual(result.tool_names, [])
        self.assertEqual(result.debug.get("parse_status"), "json_invalid")
        self.assertEqual(result.debug.get("visible_skill_count"), 1)
        self.assertEqual(result.debug.get("visible_tool_count"), 1)
        self.assertIn("definitely not json", str(result.debug.get("raw_content_excerpt") or ""))

    async def test_plan_capabilities_invalid_tool_names_record_debug_status(self) -> None:
        adapter = _FakePlannerAdapter(
            lambda **_kwargs: {
                "content": json.dumps(
                    {
                        "selection_kind": "mcp",
                        "skill_ids": [],
                        "tool_names": ["browser_navigate"],
                        "query": "open the page",
                        "thought_summary": "Use browser navigate",
                        "reason": "A browser tool should be enough.",
                    },
                    ensure_ascii=False,
                )
            }
        )

        result = await plan_capabilities(
            messages=[{"role": "user", "content": "open the page"}],
            model="demo",
            skill_catalog=[],
            tool_catalog=[
                {"name": "playwright_mcp.browser_navigate", "description": "Navigate a page", "source": "mcp"}
            ],
            provider_adapter=adapter,
        )

        self.assertEqual(result.selection_kind, "mcp")
        self.assertEqual(result.tool_names, ["playwright_mcp.browser_navigate"])
        self.assertEqual(result.debug.get("parse_status"), "json_ok")

        adapter = _FakePlannerAdapter(
            lambda **_kwargs: {
                "content": json.dumps(
                    {
                        "selection_kind": "mcp",
                        "skill_ids": [],
                        "tool_names": ["totally_missing_tool"],
                        "query": "open the page",
                        "thought_summary": "Use a missing tool",
                        "reason": "This should be rejected.",
                    },
                    ensure_ascii=False,
                )
            }
        )

        result = await plan_capabilities(
            messages=[{"role": "user", "content": "open the page"}],
            model="demo",
            skill_catalog=[],
            tool_catalog=[
                {"name": "playwright_mcp.browser_navigate", "description": "Navigate a page", "source": "mcp"}
            ],
            provider_adapter=adapter,
        )

        self.assertEqual(result.selection_kind, "none")
        self.assertEqual(result.debug.get("parse_status"), "invalid_tool_names")
        self.assertEqual(result.debug.get("visible_tool_count"), 1)
        self.assertIn("totally_missing_tool", json.dumps(result.debug, ensure_ascii=False))

    async def test_plan_capabilities_explicit_none_records_debug_status(self) -> None:
        adapter = _FakePlannerAdapter(
            lambda **_kwargs: {
                "content": json.dumps(
                    {
                        "selection_kind": "none",
                        "skill_ids": [],
                        "tool_names": [],
                        "query": "open the page",
                        "thought_summary": "No bundle selected",
                        "reason": "Nothing suitable remained.",
                    },
                    ensure_ascii=False,
                )
            }
        )

        result = await plan_capabilities(
            messages=[{"role": "user", "content": "open the page"}],
            model="demo",
            skill_catalog=[
                {
                    "skill_id": "calendar-skill",
                    "display_name": "Calendar Skill",
                    "description": "Schedules meetings",
                    "tool_names": ["skill.calendar.run_task"],
                    "prompt_summary": "Meeting workflow",
                }
            ],
            tool_catalog=[
                {"name": "browser.navigate", "description": "Navigate a page", "source": "mcp"}
            ],
            provider_adapter=adapter,
        )

        self.assertEqual(result.selection_kind, "none")
        self.assertEqual(result.debug.get("parse_status"), "planner_selected_none")

    async def test_search_capabilities_prefers_default_enabled_skill_before_task_types(self) -> None:
        adapter = _FakePlannerAdapter(
            lambda **_kwargs: {
                "content": json.dumps(
                    {
                        "mode": "skill",
                        "skill_id": "calendar-skill",
                        "task_types": [],
                        "matched_tool_names": [],
                        "reason": "The visible skill matches directly.",
                        "thought_summary": "Use the visible skill.",
                    },
                    ensure_ascii=False,
                )
            }
        )

        result = await search_capabilities(
            messages=[{"role": "user", "content": "schedule the meeting"}],
            model="demo",
            skill_catalog=[
                {
                    "skill_id": "calendar-skill",
                    "display_name": "Calendar Skill",
                    "description": "Schedules meetings",
                    "prompt_excerpt": "Meeting workflow",
                }
            ],
            tool_catalog=[{"name": "playwright.browser_navigate", "description": "Navigate", "task_type": "browser_automation"}],
            provider_adapter=adapter,
        )

        self.assertEqual(result.mode, "skill")
        self.assertEqual(result.skill_id, "calendar-skill")

    async def test_search_capabilities_maps_task_types_then_narrows_tools(self) -> None:
        responses = [
            {
                "content": json.dumps(
                    {
                        "mode": "task_types",
                        "skill_id": "",
                        "task_types": ["browser_automation", "web_search"],
                        "matched_tool_names": [],
                        "reason": "Need browser and web search domains.",
                        "thought_summary": "Map to browser and search tools.",
                    },
                    ensure_ascii=False,
                )
            },
            {
                "content": json.dumps(
                    {
                        "mode": "task_types",
                        "task_types": ["browser_automation", "web_search"],
                        "matched_tool_names": ["playwright.browser_navigate", "tavily.search"],
                        "reason": "These tools can start the task.",
                        "thought_summary": "Use the narrowed MCP set.",
                    },
                    ensure_ascii=False,
                )
            },
        ]
        adapter = _FakePlannerAdapter(lambda **_kwargs: responses.pop(0))

        result = await search_capabilities(
            messages=[{"role": "user", "content": "open the page and search"}],
            model="demo",
            skill_catalog=[],
            tool_catalog=[
                {"name": "playwright.browser_navigate", "description": "Navigate", "task_type": "browser_automation"},
                {"name": "tavily.search", "description": "Search", "task_type": "web_search"},
                {"name": "read_file", "description": "Read", "task_type": "file_io"},
            ],
            provider_adapter=adapter,
        )

        self.assertEqual(result.mode, "task_types")
        self.assertEqual(result.task_types, ["browser_automation", "web_search"])
        self.assertEqual(result.matched_tool_names, ["playwright.browser_navigate", "tavily.search"])

    async def test_build_execution_plan_supports_batch_step(self) -> None:
        adapter = _FakePlannerAdapter(
            lambda **_kwargs: {
                "content": json.dumps(
                    {
                        "plan_id": "plan-bili",
                        "plan_summary": "Open the page and type the query.",
                        "reason": "The task requires one combined browser batch.",
                        "steps": [
                            {
                                "step_id": "step-open-search",
                                "title": "Open and type",
                                "goal": "Open the page and type the query in one batch.",
                                "success_criteria": "The page is open and the query is typed.",
                                "call_mode": "batch",
                                "candidate_tool_sets": [["playwright.browser_navigate", "playwright.browser_type"]],
                                "notes": "Keep both calls in one approval batch.",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            }
        )

        result = await build_execution_plan(
            messages=[{"role": "user", "content": "open bilibili and type 脆弱扩"}],
            model="demo",
            searcher_result={
                "mode": "task_types",
                "task_types": ["browser_automation"],
                "matched_tool_names": ["playwright.browser_navigate", "playwright.browser_type"],
            },
            tool_catalog=[
                {"name": "playwright.browser_navigate", "description": "Navigate", "task_type": "browser_automation"},
                {"name": "playwright.browser_type", "description": "Type", "task_type": "browser_automation"},
            ],
            provider_adapter=adapter,
        )

        self.assertEqual(result.plan_id, "plan-bili")
        self.assertEqual(len(result.steps), 1)
        self.assertEqual(result.steps[0].call_mode, "batch")
        self.assertEqual(result.steps[0].candidate_tool_sets, [["playwright.browser_navigate", "playwright.browser_type"]])
        self.assertIn("plan-bili", json.dumps(result.to_dict(), ensure_ascii=False))

    def test_build_inventory_result_message_reports_deterministic_empty_inventory(self) -> None:
        prompt = build_inventory_result_message(
            {
                "inventory_scope": "mcp",
                "inventory_summary": "I checked the current runtime and did not find any available non-skill MCP tools.",
                "inventory_skills": [],
                "inventory_tools": [],
            },
            user_request="搜有没有可用的 mcp",
        )

        self.assertIn("deterministic inventory result", prompt)
        self.assertIn("Runtime-available non-skill MCP tools found: none", prompt)
        self.assertIn("do not continue the broader task automatically", prompt.lower())

    def test_build_inventory_result_message_includes_skill_and_mcp_lists(self) -> None:
        prompt = build_inventory_result_message(
            {
                "inventory_scope": "both",
                "inventory_summary": "I listed the currently available capabilities.",
                "inventory_skills": [
                    {"skill_id": "repo-guide", "display_name": "Repo Guide", "description": "Reads repos"}
                ],
                "inventory_tools": [
                    {"name": "read_file", "description": "Read a file", "source": "mcp"}
                ],
            },
            user_request="有哪些可用工具",
        )

        self.assertIn("Planner-visible skills found", prompt)
        self.assertIn("repo-guide", prompt)
        self.assertIn("Runtime-available non-skill MCP tools found", prompt)
        self.assertIn("read_file", prompt)


if __name__ == "__main__":
    unittest.main()
