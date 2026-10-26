# ISMATEC DIN pump control

Control an ISMATEC IPS8 DIN-5 pump with an Arduino Uno and two relay contacts.
Agents can use a JSON CLI or six MCP tools. Runs are bounded to **250–10,000 ms**;
the Uno requests stop at the deadline even if the client disconnects.

**Runtime:** Linux and Python 3.10+, standard library only. No pip install,
Python SDK, network service, or API key is needed. The Uno must have the included
firmware and the [specified wiring](docs/hardware.md). Use a **fully assembled
DIN plug**: its body actuates the pump's internal remote selector.

## Start

Run from this directory (or use the absolute path to `pump.py`):

```sh
python3 pump.py ports
# Pump power OFF. Connect the Uno USB and the complete DIN plug.
python3 pump.py serve --port /dev/ttyACM0
```

Keep this service running in its terminal. Prefer a stable `/dev/serial/by-id/...`
path when available. Opening USB can reset the Uno; start or restart the service
with pump power **off**. The service starts in STOP, with motion disabled.

In another terminal:

```sh
python3 pump.py status
# Power the closed pump, set a low local speed, and press its local ON button.
# Confirm the rotor remains stopped, then enable this session:
python3 pump.py enable
python3 pump.py run --duration-ms 10000 --wait
python3 pump.py stop
```

Before restarting or changing direction, observe standstill and wait until
`status` reports `guard_remaining_ms: 0` (5 seconds after commanded stop).

```sh
python3 pump.py direction B
# Wait for guard_remaining_ms: 0 again, then:
python3 pump.py run --duration-ms 10000 --wait
python3 pump.py disable
```

Speed is set on the pump. A/B select opposite directions; they do not specify
clockwise/counterclockwise. Status describes **commanded state**, not measured
rotor motion. STOP selects zero speed; it does not disconnect mains power.

All client commands print one JSON object and exit nonzero on error. `run`
returns immediately unless `--wait` is supplied. Do not retry a motion command
automatically after a communication error. See the [agent guide](docs/agents.md)
for error handling and the [MCP setup](docs/mcp.md) for tool integration.

## Setup and checks

- [Wiring and one-time firmware upload](docs/hardware.md)
- [Agent workflow and local API](docs/agents.md)
- [MCP tools and configuration](docs/mcp.md)
- [Development and verification](docs/development.md)

```sh
python3 -m unittest discover -s tests -v
```

Tests use a simulated serial device; no pump or Arduino is required.
