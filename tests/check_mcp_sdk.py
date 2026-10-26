"""Optional interoperability check with the official MCP Python SDK.

Not part of unittest discovery. Requires test-only mcp==2.3.0; production code
and the normal test suite require only the Python standard library.
"""
import asyncio
from importlib.metadata import version
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters, stdio_client

from fake_uno import FakeUno

SCRIPT = str(Path(__file__).resolve().parents[1] / "pump.py")


async def exercise(socket_path):
    parameters = StdioServerParameters(
        command=sys.executable, args=["-S", SCRIPT, "--socket", socket_path, "mcp"], cwd="/tmp"
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=5) as session:
            initialized = await session.initialize()
            assert initialized.protocol_version == "2025-11-25"
            listing = await session.list_tools()
            assert len(listing.tools) == 6
            await session.send_ping()
            state = await session.call_tool("pump_status")
            assert not state.is_error and not state.structured_content["result"]["enabled"]
            disabled_run = await session.call_tool("pump_run", {"duration_ms": 250})
            assert disabled_run.is_error
            assert not (await session.call_tool("pump_enable")).is_error
            for direction in ("B", "A"):
                assert not (await session.call_tool("pump_set_direction", {"direction": direction})).is_error
                await asyncio.sleep(0.1)  # test peer uses an 80 ms guard
                pulse = await session.call_tool("pump_run", {"duration_ms": 250})
                assert not pulse.is_error
                assert pulse.structured_content["result"]["commanded_run"] == "ON"
                await asyncio.sleep(0.4)
                stopped = await session.call_tool("pump_status")
                assert stopped.structured_content["result"]["commanded_run"] == "OFF"
                assert stopped.structured_content["result"]["direction"] == direction
            assert not (await session.call_tool("pump_stop")).is_error
            assert not (await session.call_tool("pump_disable")).is_error


def main():
    uno = FakeUno()
    try:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as log:
            path = str(Path(directory) / "pump.sock")
            process = subprocess.Popen([sys.executable, "-S", SCRIPT, "--socket", path, "serve", "--port", uno.path],
                                       stdout=subprocess.PIPE, stderr=log, text=True)
            try:
                assert select.select([process.stdout], [], [], 6)[0], "Service startup timeout"
                assert json.loads(process.stdout.readline())["event"] == "ready"
                asyncio.run(exercise(path))
                assert uno.commands.count("ID") == 1, "Clients must not reopen USB"
                print(f"PASS: official MCP SDK {version('mcp')}: initialize, ping, all six tools, both directions, "
                      "pulse completion and error results over stdio + PTY service.")
            finally:
                process.terminate()
                process.wait(timeout=6)
                process.stdout.close()
    finally:
        uno.close()


if __name__ == "__main__":
    main()
