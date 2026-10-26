# Agent guide

Read [hardware.md](hardware.md) when preparing a new installation. Normal control
requires only the running local service and either CLI or MCP. No firmware upload
is needed between sessions if the supplied firmware is already installed.

## Session workflow

1. With the pump unpowered, start `serve --port ...`. Only this process opens USB.
   Start/restart it only while pump power is off; never reopen USB as a recovery
   attempt on a powered pump.
2. Read `status`. The service verifies firmware, requests STOP, and starts disabled.
3. Have the operator power the closed pump, choose local speed, press local ON,
   and confirm the rotor is stopped. Wait for `guard_remaining_ms` to reach zero.
4. Call `enable` once. It records readiness for this service session and causes
   no motor start. Do not call it solely because the serial state says OFF.
5. Call `run` with an integer duration in milliseconds, 250 through 10000.
   Each call performs one ARM and one PULSE. The response is an acknowledgement
   of the **command**, not proof the rotor ran or delivered fluid.
6. Use `status` to see the remaining commanded pulse and `stop` to interrupt it.
   Before another pulse or direction change, observe standstill and wait for
   `guard_remaining_ms: 0`. A direction change starts a fresh 5-second guard.
7. Call `disable` at the end. It requests STOP and disables further motion.
   Stop the service with Ctrl-C/SIGTERM when finished, and cut pump power before
   changing DIN or USB connections.

The firmware supports bounded runs only. There is no continuous-run command,
automatic pulse queue, speed-setting API, flow calibration, or delivery-volume
measurement. A/B are opposite direction labels. Confirm their physical meaning
for the installed tubing before using them in a procedure.

## CLI

`python3 /absolute/path/to/pump.py` works from any directory. Run `--help` for
commands. Every service client prints one JSON line. Exit codes: 0 success,
1 API/I/O error, 2 CLI syntax error, 130 interrupted. `--socket PATH` is a global
option and must come **before** the command; use the same value for all clients.
CLI usage errors follow argparse's usual stderr format.

```sh
python3 pump.py status
python3 pump.py enable
python3 pump.py run --duration-ms 3000 --wait
python3 pump.py stop
python3 pump.py direction B
python3 pump.py disable
```

Commands above illustrate the API; allow the documented guard between actions.
`run` without `--wait` returns as soon as the start is acknowledged. `--wait`
polls until commanded OFF; Ctrl-C requests STOP. Closing a normal CLI process or
an MCP bridge leaves the service running. An already-started pulse still expires
on the Uno. Graceful service shutdown requests STOP; SIGKILL cannot do so.

Example status:

```json
{"ok":true,"result":{"commanded_run":"OFF","direction":"A","armed":false,"guard_remaining_ms":0,"pulse_remaining_ms":0,"enabled":false,"firmware":"ISMATEC_PUMP_TEST_v2","motion_feedback":false}}
```

`commanded_run`, direction, guard and pulse times are read from firmware.
`enabled` belongs to the service. `motion_feedback: false` states a hardware
limitation; never translate OFF into a claim of observed standstill.

## Errors and recovery

Errors have the form `{"ok":false,"error":{"code":"...","message":"..."}}`.

| Code | Next action |
| --- | --- |
| `disabled` | Establish operator readiness, then enable. |
| `stop_guard` | Wait `retry_after_ms`, read status, confirm standstill, then act. |
| `running` | Let the pulse finish or stop it; do not reverse a moving rotor. |
| `invalid_arguments` | Correct the arguments; nothing was sent to the Uno. |
| `firmware_rejected` | Firmware refused the command and requests STOP/disarm. Read status. |
| `firmware_mismatch` | Power off; flash the included firmware before starting again. |
| `connection_fault` | Stop locally if needed, power off, and restart the service. |
| `service_unavailable` / `service_protocol` | The outcome may be unknown. Check locally or use status/stop if reachable. |
| `service_busy` / device busy | Use the existing service or close the other serial owner while the pump is unpowered. |

A timeout after a start may mean the pulse **did** start but its acknowledgement
was lost. Do not retry run automatically. The service attempts STOP and latches
serial faults; it will not reconnect, re-enable, or replay commands. Firmware
deadlines still operate if the Uno remains powered and functional.

## Local JSON API

The CLI and MCP bridge share a user-private Unix socket. Its default location is
`$XDG_RUNTIME_DIR/ismatec-control-<uid>/pump.sock`, falling back to
`/tmp/ismatec-control-<uid>/pump.sock`. The directory is mode 700 and socket 600.
This grants control to processes running as the same OS user; there is no TCP
listener or network authentication. Run one controlling agent per pump to avoid
conflicting workflows. The service serializes requests and accepts concurrent
STOP requests between short serial transactions.

Each connection sends one UTF-8 JSON line and receives one JSON line (maximum
64 KiB). No request is automatically retried.

| Method | Params |
| --- | --- |
| `status`, `enable`, `disable`, `stop` | `{}` |
| `run` | `{"duration_ms":10000}` |
| `direction` | `{"direction":"A"}` or `{"direction":"B"}` |

Python example, from the repository root:

```python
from ismatec_control.client import default_socket, request

response = request(default_socket(), "status")
print(response)
```

Wire request: `{"method":"run","params":{"duration_ms":1000}}`.
Success response: `{"ok":true,"result":{...state...}}`.
There is no raw serial-command endpoint and no host API to bypass ARM or guards.
