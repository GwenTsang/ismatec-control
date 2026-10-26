import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fake_uno import FakeUno
from ismatec_control.client import request
from ismatec_control.mcp import Bridge, ProtocolError

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = str(ROOT / "pump.py")


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.socket = str(Path(cls.directory.name) / "pump.sock")
        cls.uno = FakeUno()
        cls.log = tempfile.TemporaryFile()
        cls.process = subprocess.Popen([sys.executable, SCRIPT, "--socket", cls.socket, "serve", "--port", cls.uno.path],
                                       cwd="/tmp", stdout=subprocess.PIPE, stderr=cls.log, text=True)
        if not select.select([cls.process.stdout], [], [], 6)[0]:
            cls.process.kill()
            raise AssertionError("Service did not become ready")
        raw = cls.process.stdout.readline()
        if not raw:
            cls.log.seek(0)
            raise AssertionError(cls.log.read().decode())
        assert json.loads(raw)["event"] == "ready"

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=6)
        cls.process.stdout.close()
        cls.log.close()
        cls.uno.close()
        cls.directory.cleanup()

    def setUp(self):
        request(self.socket, "disable")
        time.sleep(0.1)

    def cli(self, *arguments):
        result = subprocess.run([sys.executable, SCRIPT, "--socket", self.socket, *arguments],
                                cwd="/tmp", capture_output=True, text=True, timeout=6)
        return result, json.loads(result.stdout)

    def test_cli_runs_and_waits_without_reopening_usb(self):
        _, enabled = self.cli("enable")
        self.assertTrue(enabled["ok"])
        result, completed = self.cli("run", "--duration-ms", "250", "--wait")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(completed["result"]["commanded_run"], "OFF")
        self.assertEqual(self.uno.commands.count("ID"), 1)

    def test_cli_machine_readable_failures(self):
        result, failed = self.cli("run", "--duration-ms", "10001")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(failed["error"]["code"], "invalid_arguments")

    def test_cli_interrupt_requests_stop_and_keeps_service_alive(self):
        self.assertTrue(request(self.socket, "enable")["ok"])
        process = subprocess.Popen([sys.executable, SCRIPT, "--socket", self.socket, "run",
                                    "--duration-ms", "10000", "--wait"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 3
            while not self.uno.running and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(self.uno.running)
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=4)
            self.assertEqual(process.returncode, 130, stderr)
            self.assertEqual(json.loads(stdout)["result"]["commanded_run"], "OFF")
            self.assertTrue(request(self.socket, "status")["ok"])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_status_and_stop_remain_available_during_pulse(self):
        request(self.socket, "enable")
        self.assertTrue(request(self.socket, "run", {"duration_ms": 10000})["ok"])
        self.assertEqual(request(self.socket, "status")["result"]["commanded_run"], "ON")
        self.assertEqual(request(self.socket, "stop")["result"]["commanded_run"], "OFF")

    def test_y_graceful_shutdown_stops_pulse_and_removes_socket(self):
        # This uses a separate service so no test ordering is assumed.
        uno = FakeUno()
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as log:
            path = str(Path(directory) / "pump.sock")
            process = subprocess.Popen([sys.executable, SCRIPT, "--socket", path, "serve", "--port", uno.path],
                                       stdout=subprocess.PIPE, stderr=log, text=True)
            try:
                self.assertTrue(select.select([process.stdout], [], [], 6)[0])
                self.assertEqual(json.loads(process.stdout.readline())["event"], "ready")
                request(path, "enable")
                request(path, "run", {"duration_ms": 10000})
                self.assertTrue(uno.running)
                process.terminate()
                self.assertEqual(process.wait(timeout=4), 0)
                self.assertFalse(uno.running)
                self.assertFalse(Path(path).exists())
                self.assertEqual(uno.commands[-1], "STOP")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
                uno.close()

    def test_socket_private_and_duplicate_service_leaves_owner_alive(self):
        self.assertEqual(os.stat(self.socket).st_mode & 0o777, 0o600)
        result, failed = self.cli("serve", "--port", "/not-a-device")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(failed["error"]["code"], "service_busy")
        self.assertTrue(request(self.socket, "status")["ok"])

    def test_bad_rpc_cannot_inject_serial_commands(self):
        for payload in (b"not JSON\n", b"[]\n", b'{"method":[]}\n',
                        b'{"method":"run","params":{"duration_ms":true}}\n',
                        b'{"method":"raw","params":{"value":"ARM\\nPULSE 10000"}}\n'):
            with self.subTest(payload=payload), socket.socket(socket.AF_UNIX) as peer:
                peer.connect(self.socket)
                peer.sendall(payload)
                self.assertFalse(json.loads(peer.recv(65536))["ok"])

    def test_mcp_subprocess_to_real_service(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "pump_enable"}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "pump_run", "arguments": {"duration_ms": 250}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "pump_stop"}},
        ]
        result = subprocess.run([sys.executable, SCRIPT, "--socket", self.socket, "mcp"],
                                input="".join(json.dumps(message) + "\n" for message in messages),
                                capture_output=True, text=True, cwd="/tmp", timeout=6)
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([item["id"] for item in responses], [1, 2, 3, 4, 5])
        self.assertEqual(len(responses[1]["result"]["tools"]), 6)
        self.assertFalse(responses[3]["result"]["isError"])
        state = responses[-1]["result"]["structuredContent"]["result"]
        self.assertEqual(state["commanded_run"], "OFF")
        self.assertTrue(request(self.socket, "status")["ok"])  # MCP exit leaves service running


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Bridge("/not-a-socket")

    def initialize(self, version="2025-11-25"):
        result = self.bridge.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
        self.bridge.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def test_protocol_negotiation_and_schema(self):
        self.assertEqual(self.initialize("2025-06-18")["protocolVersion"], "2025-06-18")
        result = self.bridge.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        for tool in result["tools"]:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
        other = Bridge("unused")
        with self.assertRaises(ProtocolError):
            other.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    @patch("ismatec_control.mcp.request")
    def test_notifications_cannot_execute_tools(self, rpc):
        self.initialize()
        result = self.bridge.dispatch({"jsonrpc": "2.0", "method": "tools/call", "params": {
            "name": "pump_run", "arguments": {"duration_ms": 10000}}})
        self.assertIsNone(result)
        rpc.assert_not_called()

    @patch("ismatec_control.mcp.request")
    def test_invalid_tool_arguments_do_not_reach_service(self, rpc):
        self.initialize()
        for arguments in ({"duration_ms": True}, {"duration_ms": 10001}, {}, {"duration_ms": 250, "extra": 0}):
            result = self.bridge.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "pump_run", "arguments": arguments}})
            self.assertTrue(result["isError"])
        rpc.assert_not_called()

    def test_missing_service_is_tool_error_and_stdout_is_json(self):
        self.initialize()
        result = self.bridge.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "pump_status"}})
        self.assertTrue(result["isError"])
        process = subprocess.run([sys.executable, SCRIPT, "mcp"], input="bad JSON\n[]\n",
                                 capture_output=True, text=True, timeout=3)
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([item["error"]["code"] for item in responses], [-32700, -32600])


if __name__ == "__main__":
    unittest.main()
