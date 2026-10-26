# MCP access

The stdio bridge exposes tools through the existing service. It has no Python
dependencies and does not open USB, start the service, enable control, or move
the pump on initialization. Start the service as described in the README first.

For clients with an `mcpServers` JSON configuration, adapt
[examples/mcp.json](../examples/mcp.json):

```json
{
  "mcpServers": {
    "ismatec": {
      "command": "python3",
      "args": ["/absolute/path/to/ismatec-control/pump.py", "mcp"]
    }
  }
}
```

Replace the absolute path. The bridge can run from any working directory.
If using a custom socket, args must be
`["/absolute/path/to/pump.py", "--socket", "/path/to/private/pump.sock", "mcp"]`.
The host and service must run as the same OS user and use the same socket path.

| Tool | Arguments | Effect |
| --- | --- | --- |
| `pump_status` | `{}` | Read firmware command state and service enabled flag. |
| `pump_enable` | `{}` | Enable the session after operator readiness. |
| `pump_disable` | `{}` | STOP and disable. |
| `pump_stop` | `{}` | STOP, preserving direction. |
| `pump_run` | `{"duration_ms":10000}` | Start one pulse; returns immediately. |
| `pump_set_direction` | `{"direction":"B"}` | Select direction after observed stop and guard. |

Use `pump_status` to poll commanded pulse completion. Consult the
[agent guide](agents.md) for guards, physical observations, and uncertain outcomes.
Closing the bridge does not close the service or cancel an already-started
pulse; it still expires on the Uno. Use `pump_stop` or `pump_disable` explicitly.

The bridge implements the tools subset of MCP over newline-delimited stdio,
negotiating versions `2025-11-25` and `2025-06-18`. It supports initialize,
initialized notification, ping, tools/list and tools/call. Tool execution errors
set `isError: true`; results include text JSON and `structuredContent`. Stdout
contains only JSON-RPC messages. No HTTP transport, resources, prompts, tasks,
or server-initiated requests are advertised.

Protocol references: [stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
[lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle),
[tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
