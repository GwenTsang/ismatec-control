# Hardware setup

This wiring was functionally validated on two old IPS8 pumps with DIN-5 sockets.
It is specific to this interface; it is not a pinout for DB15 pumps or every
ISMATEC model. The controller uses relay contacts for run/stop and direction.
It does not generate an analog speed command.

## Required hardware

- Arduino Uno R3 / ATmega328P compatible board, USB connection to Linux.
- ELEGOO-style 5 V relay board with active-low inputs and isolated dry contacts.
- Fully assembled DIN-5 male 180° plug and insulated wiring.
- Pump with functioning local controls and its original internal protection fitted.

Disconnect pump mains before wiring, inserting/removing DIN, uploading firmware,
or restarting the USB service. Keep the pump closed during powered operation.

## Uno to relay board

| Uno | Relay board |
| --- | --- |
| 5V | VCC (JD-VCC jumper fitted on the validated board) |
| GND | GND |
| D2 | IN1 / K1 direction |
| D3 | IN2 / K2 run/stop |

Both inputs are **active LOW**. K3/K4, if present, are unused. Pump signals go
only to dry contacts: **do not connect pump signal common to Uno GND or 5V**.

## DIN contact positions

Define A–E on the **pump's female socket**, looking straight into its holes,
key/notch at the top. A, B, C, D, E run left to right **along the five-contact
arc**, not as a horizontal row.

Looking at the male plug's mating pins with its key at the top, the matching
positions appear in the reverse order: **E, D, C, B, A**. Trace each mating
contact to its solder terminal; do not copy that order onto the solder side.

| Pump socket position | Function in this setup | Connect to |
| --- | --- | --- |
| A | Speed input | K2 COM |
| B | Local speed setpoint | K2 NO |
| C | Signal common | K2 NC **and** K1 NO |
| D | Direction input | K1 COM |
| E | Reference, unused | Leave isolated |

K1 NC stays unused. Isolate the plug's metal shell from all five signals.
Assemble the complete plug with strain relief and insulated terminations.
The body must operate the mechanical selector behind the pump socket;
the contact insert alone is insufficient on the validated pumps.

| Commanded state | Closed connections |
| --- | --- |
| STOP, direction A | A–C |
| RUN, direction A | A–B |
| STOP, direction B | A–C–D |
| RUN, direction B | A–B and separately C–D |

Check these four states with a multimeter while the DIN is **disconnected from
the pump**. Never measure resistance on a powered circuit. With the relays
unpowered, A–C should be closed and D isolated. The active checks require the
flashed Uno and the service: enable, select direction, run a pulse, and disable.

## Flash the Uno once

Install [Arduino CLI](https://arduino.github.io/arduino-cli/latest/installation/)
and its AVR core. These are build/upload tools only; Python runtime needs neither.
The supplied sketch uses firmware identity `ISMATEC_PUMP_TEST_v2` and was verified
with Arduino AVR Boards 1.8.8 and Arduino CLI 1.4.1.

From the repository root, with the pump unpowered and the service stopped:

```sh
arduino-cli core update-index
arduino-cli core install arduino:avr@1.8.8
arduino-cli compile --fqbn arduino:avr:uno --output-dir build/uno firmware/pump_test
arduino-cli upload --fqbn arduino:avr:uno --port /dev/ttyACM0 --input-dir build/uno
```

Choose the actual Uno port; `python3 pump.py ports` only lists paths and never
opens a device. On Linux the user must have serial access, commonly membership
in `dialout`. Close other serial applications before starting the service.

The firmware starts in STOP/A, disarmed. It requires a single-use ARM before
each pulse, limits pulses to 10 seconds, preserves direction on stop, enforces
a 5-second stop/direction guard, and enables a roughly 1-second watchdog.
The Python service verifies the exact identity before exposing control.

## First powered run

1. Pump unpowered, DIN fully assembled and inserted; Uno connected.
2. Start the service and read `status`. Wait for `guard_remaining_ms: 0`.
3. Power the closed pump, choose low local speed, press local ON.
4. Observe complete standstill in commanded STOP. If it moves, cut pump power
   and check the setup before enabling control.
5. Enable, run a supervised pulse, and observe actual run and complete stop.
6. At standstill and after the guard, test the other direction.

The firmware has no rotor, contact, speed or flow feedback. Its watchdog and
timeouts are software protections, not an emergency power disconnect. Have the
pump's local power control available. Keep the relay board and USB powered while
using remote control; behavior during reset, power loss or relay failure is not
a guaranteed physical stop. Never hot-plug DIN on a powered pump.
