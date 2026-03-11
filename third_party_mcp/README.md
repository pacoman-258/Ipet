# third_party_mcp

Place third-party MCP servers here for phase-2 local deployment.

Supported v1 manifest shape:

```json
{
  "name": "example_mcp",
  "version": "0.1.0",
  "enabled": true,
  "transport": "stdio",
  "runtime": "python",
  "entry": {
    "command": "python",
    "args": ["-m", "example_mcp.server"]
  },
  "install": {
    "type": "python_uv",
    "requirements": "requirements.txt"
  },
  "description": "Example third-party MCP manifest template."
}
```

Compatibility note:

- If a third-party project only provides `mcp-server.json`, the backend will auto-convert it to this `manifest.json` format during registration/install.
