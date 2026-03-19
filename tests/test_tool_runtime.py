from __future__ import annotations

import unittest

from backend.tool_runtime import Tool, ToolRegistry


class ToolRuntimeTests(unittest.TestCase):
    def test_registry_registers_invokes_and_exports_schemas(self) -> None:
        registry = ToolRegistry()
        registry.register(
            Tool(
                name="demo.echo",
                description="Echo text",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                invoke=lambda arguments: {"echo": arguments.get("text", "")},
                source="demo",
            )
        )

        result = registry.invoke("demo.echo", {"text": "hello"})
        self.assertTrue(result.ok)
        self.assertEqual(result.structured_data, {"echo": "hello"})

        schemas = registry.to_llm_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["function"]["name"], "demo.echo")
        self.assertEqual(schemas[0]["function"]["parameters"]["required"], ["text"])

    def test_registry_rejects_duplicate_names(self) -> None:
        registry = ToolRegistry()
        registry.register(
            Tool(
                name="dup.tool",
                description="First",
                input_schema={"type": "object", "properties": {}},
                invoke=lambda _arguments: {"ok": True},
            )
        )

        with self.assertRaises(ValueError):
            registry.register(
                Tool(
                    name="dup.tool",
                    description="Second",
                    input_schema={"type": "object", "properties": {}},
                    invoke=lambda _arguments: {"ok": True},
                )
            )


if __name__ == "__main__":
    unittest.main()
