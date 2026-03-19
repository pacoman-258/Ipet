from __future__ import annotations

import io
import unittest
from pathlib import Path

from backend.mcp.stdio_client import StdioMCPClient


class _FakeProc:
    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()

    def poll(self):
        return None


class StdioMCPClientProtocolTests(unittest.TestCase):
    def test_send_uses_content_length_framing(self) -> None:
        client = StdioMCPClient(command=["dummy"], cwd=Path.cwd())
        client.proc = _FakeProc()

        client._send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

        raw = client.proc.stdin.getvalue()
        self.assertIn(b"Content-Length: ", raw)
        self.assertIn(b"\r\n\r\n", raw)
        header, body = raw.split(b"\r\n\r\n", 1)
        self.assertTrue(header.startswith(b"Content-Length: "))
        self.assertEqual(int(header.split(b":", 1)[1].strip()), len(body))
        self.assertIn(b"initialize", body)

    def test_send_uses_jsonline_when_requested(self) -> None:
        client = StdioMCPClient(command=["dummy"], cwd=Path.cwd(), protocol="jsonline")
        client.proc = _FakeProc()

        client._send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

        raw = client.proc.stdin.getvalue()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b"Content-Length:", raw)
        self.assertIn(b"initialize", raw)

    def test_with_stderr_appends_recent_stderr(self) -> None:
        client = StdioMCPClient(command=["dummy"], cwd=Path.cwd())
        client._stderr_chunks = ["line1", "line2"]
        text = client._with_stderr("mcp request timeout: initialize")
        self.assertIn("mcp request timeout: initialize", text)
        self.assertIn("line1", text)
        self.assertIn("line2", text)


if __name__ == "__main__":
    unittest.main()
