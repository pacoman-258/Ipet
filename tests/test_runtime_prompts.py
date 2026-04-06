from __future__ import annotations

import unittest

import backend.app as backend_app
from backend import runtime_prompts
from backend.agent_graph import _continuation_recheck_prompt as graph_continuation_recheck_prompt


class RuntimePromptsTests(unittest.TestCase):
    def test_router_prompt_react_lists_contract_and_internal_planner(self) -> None:
        prompt = runtime_prompts.router_prompt_react(
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "system.agent_loop",
                        "description": "Enter the general loop",
                    },
                }
            ],
            skill_summaries=[{"id": "repo-guide", "name": "Repo Guide", "description": "Inspect repos"}],
        )

        self.assertIn("Allowed route_kind values", prompt)
        self.assertIn("internal planner", prompt)
        self.assertIn("repo-guide", prompt)

    def test_capability_plan_prompt_keeps_skill_first_rule(self) -> None:
        prompt = runtime_prompts.capability_plan_prompt(
            skill_catalog=[
                {
                    "skill_id": "calendar-skill",
                    "display_name": "Calendar Skill",
                    "description": "Schedules meetings",
                    "tool_names": ["skill.calendar.run_task"],
                    "prompt_excerpt": "Meeting workflow",
                }
            ],
            tool_catalog=[{"name": "browser.navigate", "description": "Navigate a page", "source": "mcp"}],
            excluded_skill_ids=["old-skill"],
            excluded_tool_names=["read_file"],
        )

        self.assertIn("Prefer skills first", prompt)
        self.assertIn("selection_kind=mcp", prompt)
        self.assertIn("calendar-skill", prompt)
        self.assertIn("browser.navigate", prompt)
        self.assertIn("old-skill", prompt)
        self.assertIn("read_file", prompt)

    def test_writer_prompt_from_planner_restricts_to_selected_bundle(self) -> None:
        prompt = runtime_prompts.writer_prompt_from_planner(
            {"user_text": "open youtube"},
            {
                "selection_kind": "mcp",
                "tool_names": ["playwright.browser_navigate"],
                "reason": "Need browser control",
                "usage_notes": "Open the home page first.",
            },
            strict=True,
        )

        self.assertIn("Planner-selected MCP tools: playwright.browser_navigate", prompt)
        self.assertIn("Need browser control", prompt)
        self.assertIn("Open the home page first.", prompt)
        self.assertIn("must return needs_tool=true", prompt)
        self.assertIn("Do not reassess whether tools are available", prompt)
        self.assertIn("Return only the next concrete tool step", prompt)

    def test_writer_tool_call_template_prompt_includes_visible_tool_example(self) -> None:
        prompt = runtime_prompts.writer_tool_call_template_prompt(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "playwright_mcp.browser_navigate",
                        "description": "Navigate a page",
                        "parameters": {
                            "type": "object",
                            "properties": {"url": {"type": "string"}},
                            "required": ["url"],
                        },
                    },
                }
            ],
            {
                "selection_kind": "mcp",
                "tool_names": ["playwright_mcp.browser_navigate"],
            },
        )

        self.assertIn("Tool-call JSON template for the writer stage.", prompt)
        self.assertIn("Example for playwright_mcp.browser_navigate:", prompt)
        self.assertIn('"name": "playwright_mcp.browser_navigate"', prompt)
        self.assertIn('"url": "https://example.com"', prompt)

    def test_expression_protocol_prompt_and_visibility_note_are_centralized(self) -> None:
        expr_prompt = runtime_prompts.build_expression_protocol_prompt(["happy", "thinking"])
        self.assertIn(runtime_prompts.EXPR_PROTOCOL_PROMPT, expr_prompt)
        self.assertIn('"happy"', expr_prompt)
        self.assertIn('"thinking"', expr_prompt)

        sanitized = backend_app._sanitize_skill_prompt_for_react_visibility(
            "Follow this workflow.\n`skill.repo-guide.run_task`\n`browser.navigate`"
        )
        self.assertTrue(sanitized.startswith(runtime_prompts.REACT_SKILL_VISIBILITY_NOTE))
        self.assertIn("`skill-local tool`", sanitized)
        self.assertIn("`discovered MCP tool`", sanitized)

    def test_app_and_graph_reuse_shared_continuation_recheck_text(self) -> None:
        expected = runtime_prompts.continuation_recheck_prompt("open the page", 2)
        self.assertEqual(backend_app._continuation_recheck_prompt("open the page", 2), expected)
        self.assertEqual(graph_continuation_recheck_prompt("open the page", 2), expected)

    def test_inventory_and_reflection_prompts_stay_deterministic(self) -> None:
        inventory_prompt = runtime_prompts.build_inventory_result_message(
            {
                "inventory_scope": "mcp",
                "inventory_summary": "I checked the current runtime and did not find any available non-skill MCP tools.",
                "inventory_skills": [],
                "inventory_tools": [],
            },
            user_request="鎼滄湁娌℃湁鍙敤 mcp",
        )
        reflection_prompt = runtime_prompts.tool_reflection_prompt(
            [
                {
                    "name": "read_file",
                    "arguments": {"path": "missing.txt"},
                    "ok": False,
                    "summary": "not found",
                }
            ]
        )

        self.assertIn("Runtime-available non-skill MCP tools found: none", inventory_prompt)
        self.assertIn("Do not continue the broader task automatically", inventory_prompt)
        self.assertIn("Do not repeat an ineffective tool call", reflection_prompt)
        self.assertIn("missing.txt", reflection_prompt)

    def test_planner_none_observation_text_reports_counts_and_reason(self) -> None:
        prompt = runtime_prompts.planner_none_observation_text(
            {
                "selection_kind": "none",
                "reason": "The planner response did not provide tool names from the visible MCP list.",
                "debug": {
                    "parse_status": "invalid_tool_names",
                    "visible_skill_count": 2,
                    "visible_tool_count": 44,
                },
            }
        )

        self.assertIn("Checked 2 default-enabled skill(s) and 44 MCP tool(s).", prompt)
        self.assertIn("outside the visible MCP tool list", prompt)
        self.assertIn("Reason:", prompt)

    def test_searcher_prompt_keeps_browser_skill_cross_platform_excerpt(self) -> None:
        prompt = runtime_prompts.searcher_skill_match_prompt(
            [
                {
                    "skill_id": "browser-automation",
                    "display_name": "Browser Automation",
                    "description": "Open pages, click, type, fill forms, scrape content, upload or download files, and capture browser results on Windows or macOS",
                    "prompt_excerpt": "Common recipes + optional helper tools for browser MCP on Windows or macOS: open_page, open_and_type, click_followup, extract_or_verify. Keep user-provided file paths unchanged.",
                }
            ]
        )

        self.assertIn("browser-automation", prompt)
        self.assertIn("Windows or macOS", prompt)
        self.assertIn("open_page", prompt)
        self.assertIn("open_and_type", prompt)
        self.assertIn("Keep user-provided file paths unchanged", prompt)

    def test_searcher_prompt_browser_recipe_excerpt_stays_recipe_first(self) -> None:
        prompt = runtime_prompts.searcher_skill_match_prompt(
            [
                {
                    "skill_id": "browser-automation",
                    "display_name": "Browser Automation",
                    "description": "Open pages, click, type, fill forms, scrape content, upload or download files, and capture browser results on Windows or macOS",
                    "prompt_excerpt": "Common recipes + optional helper tools for browser MCP on Windows or macOS: open_page, open_and_type, click_followup, extract_or_verify. Keep user-provided file paths unchanged.",
                }
            ]
        )

        self.assertNotIn("resolve_mcp_recipe", prompt)
        self.assertNotIn("compact_browser_result", prompt)
    def test_searcher_and_step_prompts_expose_new_step_loop_contract(self) -> None:
        searcher_prompt = runtime_prompts.searcher_skill_match_prompt(
            [
                {
                    "skill_id": "daily-hotspots",
                    "display_name": "Daily Hotspots",
                    "description": "Collects daily trending topics",
                    "prompt_excerpt": "Use this workflow for daily hot topics.",
                }
            ]
        )
        planner_prompt = runtime_prompts.planner_execution_plan_prompt(
            user_text="open bilibili and type the query",
            searcher_result={
                "mode": "task_types",
                "task_types": ["browser_automation"],
                "matched_tool_names": ["playwright.browser_navigate", "playwright.browser_type"],
            },
            tool_catalog=[
                {"name": "playwright.browser_navigate", "description": "Navigate", "task_type": "browser_automation"},
                {"name": "playwright.browser_type", "description": "Type", "task_type": "browser_automation"},
            ],
        )
        writer_prompt = runtime_prompts.writer_step_prompt(
            user_text="open bilibili and type the query",
            plan={"plan_summary": "Do the browser work."},
            step={
                "title": "Open and type",
                "goal": "Open the page and type the query.",
                "success_criteria": "The page is open and the query is typed.",
                "call_mode": "batch",
                "notes": "Keep both calls in one batch.",
            },
            candidate_tool_set=["playwright.browser_navigate", "playwright.browser_type"],
            step_index=0,
            step_count=1,
            attempt=1,
            strict=True,
        )
        template_prompt = runtime_prompts.writer_step_template_prompt(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "playwright.browser_navigate",
                        "description": "Navigate",
                        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "playwright.browser_type",
                        "description": "Type",
                        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                    },
                },
            ],
            {
                "call_mode": "batch",
            },
            ["playwright.browser_navigate", "playwright.browser_type"],
        )

        self.assertIn('"mode":"skill|task_types|inventory"', searcher_prompt)
        self.assertIn('"steps"', planner_prompt)
        self.assertIn('call_mode":"single|batch', planner_prompt)
        self.assertIn("Planner-selected MCP tools: playwright.browser_navigate, playwright.browser_type", writer_prompt)
        self.assertIn("call_mode=batch", writer_prompt)
        self.assertIn("Tool-call JSON template for the writer stage.", template_prompt)
        self.assertIn('"tool_calls"', template_prompt)


if __name__ == "__main__":
    unittest.main()
