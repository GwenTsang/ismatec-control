"""Small stdio MCP bridge, no SDK dependency and no direct serial access."""
import json
import sys

from . import __version__
from .client import MAX_MESSAGE, request
from .errors import PumpError

VERSIONS = ("2025-11-25", "2025-06-18")


def reject_constant(value):
    raise ValueError("Invalid JSON constant: " + value)


def tool(name, description, properties=None, required=None, read_only=False, idempotent=False):
    return {
        "name": "pump_" + name, "description": description,
        "inputSchema": {"type": "object", "properties": properties or {},
                        "required": required or [], "additionalProperties": False},
        "annotations": {"readOnlyHint": read_only, "destructiveHint": not read_only,
                        "idempotentHint": idempotent, "openWorldHint": False},
    }


TOOLS = [
    tool("status", "Read commanded state, enabled flag and guard_remaining_ms. No physical motion feedback.",
         read_only=True, idempotent=True),
    tool("enable", "Enable this service session only after the operator has powered the closed pump, "
         "pressed local ON and confirmed a stopped rotor in commanded STOP. Does not start the pump.", idempotent=True),
    tool("disable", "Request STOP and disable subsequent motion until enabled again. Preserves direction.", idempotent=True),
    tool("stop", "Request immediate STOP, preserving direction. Rotor deceleration may take time.", idempotent=True),
    tool("run", "Start one bounded pulse, then the Uno automatically requests STOP. Returns immediately. "
         "Requires enabled control, an observed stopped rotor, and guard_remaining_ms=0. "
         "Do not retry automatically after a communication error. Speed uses the pump's local setting.",
         {"duration_ms": {"type": "integer", "minimum": 250, "maximum": 10000}}, ["duration_ms"]),
    tool("set_direction", "Select A or B only after observed standstill and guard_remaining_ms=0. "
         "Changing direction starts another 5-second guard. A/B are wiring labels, not calibrated CW/CCW.",
         {"direction": {"type": "string", "enum": ["A", "B"]}}, ["direction"], idempotent=True),
]


class ProtocolError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


class Bridge:
    def __init__(self, socket_path):
        self.socket_path = socket_path
        self.initialized = False
        self.negotiated = False

    def dispatch(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            raise ProtocolError(-32600, "Invalid JSON-RPC request")
        method = message["method"]
        # Notifications must never perform motion, even if mislabelled tools/call.
        if "id" not in message:
            if method == "notifications/initialized" and self.negotiated:
                self.initialized = True
            return None
        if type(message["id"]) not in (int, str):
            raise ProtocolError(-32600, "Request id must be a string or integer")
        params = message.get("params", {})
        if not isinstance(params, dict):
            raise ProtocolError(-32602, "params must be an object")
        if method == "initialize":
            if self.negotiated:
                raise ProtocolError(-32600, "Already initialized")
            if (not isinstance(params.get("protocolVersion"), str)
                    or not isinstance(params.get("capabilities"), dict)
                    or not isinstance(params.get("clientInfo"), dict)):
                raise ProtocolError(-32602, "initialize requires protocolVersion, capabilities and clientInfo")
            self.negotiated = True
            version = params["protocolVersion"] if params["protocolVersion"] in VERSIONS else VERSIONS[0]
            return {
                "protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "ismatec-control", "version": __version__},
                "instructions": "Uses an already-running local pump service. Never opens or resets USB. "
                    "Obtain operator readiness before pump_enable. State is commanded, not measured. "
                    "Pulses last at most 10 seconds; do not automatically retry motion. "
                    "Wait for guard_remaining_ms=0 and observed standstill before run or direction changes.",
            }
        if method == "ping":
            return {}
        if not self.initialized:
            raise ProtocolError(-32000, "Initialize and send notifications/initialized first")
        if method == "tools/list":
            return {"tools": TOOLS}
        if method != "tools/call":
            raise ProtocolError(-32601, "Method not found")
        name = params.get("name")
        definition = next((item for item in TOOLS if item["name"] == name), None)
        if definition is None:
            raise ProtocolError(-32602, "Unknown tool")
        arguments = params.get("arguments", {})
        schema = definition["inputSchema"]
        expected = set(schema["properties"])
        try:
            if not isinstance(arguments, dict) or set(arguments) != expected:
                raise PumpError("invalid_arguments", "Unexpected or missing tool arguments.")
            if name == "pump_run" and (type(arguments["duration_ms"]) is not int
                                       or not 250 <= arguments["duration_ms"] <= 10000):
                raise PumpError("invalid_arguments", "duration_ms must be an integer from 250 to 10000.")
            if name == "pump_set_direction" and arguments["direction"] not in ("A", "B"):
                raise PumpError("invalid_arguments", "direction must be A or B.")
            method = "direction" if name == "pump_set_direction" else name.removeprefix("pump_")
            response = request(self.socket_path, method, arguments)
        except PumpError as exc:
            response = {"ok": False, "error": exc.as_dict()}
        return {"content": [{"type": "text", "text": json.dumps(response)}],
                "structuredContent": response, "isError": not response["ok"]}


def serve_stdio(socket_path):
    bridge = Bridge(socket_path)
    while True:
        raw = sys.stdin.buffer.readline(MAX_MESSAGE + 1)
        if not raw:
            return
        request_id = None
        try:
            if len(raw) > MAX_MESSAGE:
                # Do not interpret a fragment of an oversized request as a new command.
                while raw and not raw.endswith(b"\n"):
                    raw = sys.stdin.buffer.readline(MAX_MESSAGE + 1)
                raise ProtocolError(-32600, "Message too large")
            try:
                message = json.loads(raw, parse_constant=reject_constant)
            except (ValueError, UnicodeError) as exc:
                raise ProtocolError(-32700, "Invalid JSON") from exc
            if isinstance(message, dict) and type(message.get("id")) in (int, str):
                request_id = message["id"]
            result = bridge.dispatch(message)
            if result is None:
                continue
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except ProtocolError as exc:
            response = {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": exc.code, "message": exc.message}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
