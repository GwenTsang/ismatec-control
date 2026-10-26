# Development and verification

## Layout

```text
pump.py                    CLI entry point, runnable from any directory
ismatec_control/           serial owner, local API, CLI and stdio MCP bridge
firmware/pump_test/        validated Uno firmware (ISMATEC_PUMP_TEST_v2)
docs/                     hardware, agent API and MCP instructions
examples/mcp.json          generic MCP host configuration
tests/                    offline PTY/CLI/service/MCP integration tests
```

Python 3.10+ on Linux is sufficient for all host code and tests. There are no
third-party imports, package installation steps, or runtime dependency files.
Run from a clone with `python3 pump.py` or `python3 -m ismatec_control`.
No service is installed at login or started automatically.

## Offline checks

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q ismatec_control tests pump.py
python3 pump.py --help
python3 pump.py ports
```

Tests use Linux pseudoterminals and a test-only Uno peer. They exercise real
termios/exclusive ownership, serial framing and acknowledgements, guard handling,
single-pulse requests, concurrent callers, STOP, disconnects, lost replies,
unexpected resets, JSON socket permissions, CLI subprocesses and MCP subprocesses.
The test peer shortens its stop guard to 80 ms; production timing comes from
the actual firmware's 5000 ms guard and is not replaced or configurable in the
runtime. These tests do not measure physical pump motion or prove firmware
electrical behavior.

Build firmware separately with Arduino CLI and AVR core 1.8.8:

```sh
arduino-cli compile --fqbn arduino:avr:uno --output-dir build/uno firmware/pump_test
```

The sketch is the same `ISMATEC_PUMP_TEST_v2` used in supervised functional
validation on two DIN pumps: run, stop and both directions worked with the
complete plug and specified wiring. The new host interface has been verified
offline; run the first-powered-run procedure in hardware.md on reconnection.
Deceleration depends on pump condition. A software deadline is a relay-command
deadline, not a measurement of exact rotor stop time.

### Optional MCP SDK interoperability check

The bridge has also been tested using the
[official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk),
version 2.3.0. This checks initialization, ping, tool schemas, all six tools,
both directions, pulse completion and tool errors through a real stdio client.
It uses the same PTY peer, never physical hardware. To repeat this extra check,
install the SDK in a disposable test environment:

```sh
python3 -m venv /tmp/ismatec-sdk-check
/tmp/ismatec-sdk-check/bin/python -m pip install mcp==2.3.0
/tmp/ismatec-sdk-check/bin/python tests/check_mcp_sdk.py
```

This optional SDK check needs Python 3.11+ for the test SDK. The service and
bridge are launched with `-S` (site packages disabled) during the check, so the
SDK remains a test-only dependency. The normal 24-test suite remains entirely
standard-library based.

## Design boundaries

One foreground service owns the serial descriptor. Short client/MCP calls use a
local Unix socket, so repeated tool calls do not reopen/reset the Uno. A service
lock protects the socket; flock and TIOCEXCL protect the serial device.

The service verifies the firmware ID, requests STOP and starts disabled. Motion
must be enabled for each service lifetime. Serial faults latch and require a
service restart with pump power off. A missing reply is never interpreted as a
failed-to-start pulse. STOP is attempted on serial faults and graceful shutdown.
The independent firmware pulse timeout bounds an accepted run if a host process
dies. Firmware watchdog reset requests STOP/A; it cannot guarantee safe external
behavior through relay failure, loss of relay power or a board reset transient.

The service does not poll idle hardware or reconnect in the background. Status
always queries the Uno; reset/disconnect detection occurs on the next request.
For logs of TX/RX, run `serve --verbose` and redirect stderr to a file.

No Git remote or public hosting is configured by this project.
