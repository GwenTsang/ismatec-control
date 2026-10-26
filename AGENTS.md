# Working on this repository

This is a Linux/Python standard-library controller for an Uno-connected ISMATEC
DIN pump. Start with README.md, then docs/agents.md for operating semantics and
docs/hardware.md for the exact validated wiring and firmware upload procedure.

- Run offline checks with `python3 -m unittest discover -s tests -v`.
- Never treat commanded state as measured motor motion. There is no feedback.
- Opening/resetting/flashing the Uno requires pump power OFF. Starting a service
  is an explicit hardware setup action; clients and MCP must not open USB.
- Before enabling motion, obtain operator confirmation of local ON and actual
  standstill in commanded STOP. Do not require confirmation again for each
  bounded pulse already authorized in the ongoing session.
- Preserve firmware guards, single-use ARM, bounded pulses and direction on STOP.
  Never automatically retry, queue, or replay motion after an uncertain result.
- Keep runtime dependencies limited to the Python standard library. Build tools
  for firmware and test-only tools are separate from runtime requirements.
- Put setup and protocol detail in docs/, keeping README short and operational.
