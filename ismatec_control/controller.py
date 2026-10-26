"""Serialized, acknowledged commands. Firmware remains responsible for deadlines."""
import logging
import re
import threading
import time

from .errors import PumpError
from .serial_port import SerialPort

FIRMWARE_ID = "ID ISMATEC_PUMP_TEST_v2 max_pulse_ms=10000 stop_guard_ms=5000 watchdog_ms=1000"
STATE = re.compile(
    r"STATE commanded_run=(ON|OFF) direction=([AB]) armed=([01]) "
    r"guard_remaining_ms=(\d+) pulse_remaining_ms=(\d+)$"
)
LOG = logging.getLogger(__name__)


class Controller:
    def __init__(self, port, boot_delay=2.2):
        self.lock = threading.Lock()
        self.enabled = False
        self.fault = None
        self.serial = SerialPort(port)
        try:
            # Opening an Uno may reset it. Start the service with pump power OFF.
            time.sleep(boot_delay)
            self.serial.discard_boot_output()
            identity = self._exchange("ID", "ID ")
            if identity != FIRMWARE_ID:
                raise PumpError("firmware_mismatch", "Flash the supplied pump_test firmware.", received=identity)
            self._exchange("STOP", "OK STOP; direction preserved")
            self._state()
        except Exception:
            self.close()
            raise

    def _event(self, line):
        LOG.info("RX %s", line)
        if line.startswith("READY "):
            raise PumpError("board_reset", "Uno reset unexpectedly. Power off the pump and restart the service.")

    def _exchange(self, command, expected):
        for line in self.serial.drain():
            self._event(line)
        LOG.info("TX %s", command)
        self.serial.send(command)
        deadline = time.monotonic() + 1.5
        while True:
            line = self.serial.readline(deadline)
            self._event(line)
            if line.startswith("ERR "):
                raise PumpError("firmware_rejected", line)
            if line.startswith(expected):
                return line

    def _state(self):
        line = self._exchange("STATUS", "STATE ")
        match = STATE.fullmatch(line)
        if not match:
            raise PumpError("serial_protocol", "Unrecognized firmware state.", received=line)
        run, direction, armed, guard, remaining = match.groups()
        guard, remaining = int(guard), int(remaining)
        if guard > 5000 or remaining > 10000 or (run == "OFF" and remaining != 0):
            raise PumpError("serial_protocol", "Invalid firmware state values.", received=line)
        return {
            "commanded_run": run, "direction": direction, "armed": armed == "1",
            "guard_remaining_ms": guard, "pulse_remaining_ms": remaining,
            "enabled": self.enabled, "firmware": "ISMATEC_PUMP_TEST_v2",
            "motion_feedback": False,
        }

    @staticmethod
    def _require_stopped(state):
        if state["commanded_run"] != "OFF":
            raise PumpError("running", "Stop the pump before this operation.")
        if state["guard_remaining_ms"]:
            raise PumpError("stop_guard", "Wait for the stop guard, then retry.",
                            retry_after_ms=state["guard_remaining_ms"])

    def call(self, method, params):
        with self.lock:
            if self.fault:
                raise PumpError("connection_fault", "Power off the pump and restart the service; no automatic reconnection.",
                                cause=self.fault)
            try:
                return self._call(method, params)
            except (OSError, UnicodeError, PumpError) as exc:
                if isinstance(exc, PumpError) and exc.code in {
                    "invalid_arguments", "unknown_method", "disabled", "running", "stop_guard", "firmware_rejected"
                }:
                    raise
                self.enabled = False
                self.fault = str(exc)
                # A lost acknowledgement must never cause automatic motion retries.
                try:
                    self.serial.send("STOP")
                except (OSError, PumpError):
                    pass
                raise PumpError("connection_fault", "Serial control failed; stop was attempted but is unconfirmed. "
                                "The firmware pulse timeout still applies if the board is functioning.", cause=self.fault) from exc

    def _call(self, method, params):
        expected = {"status": set(), "enable": set(), "disable": set(), "stop": set(),
                    "run": {"duration_ms"}, "direction": {"direction"}}
        if method not in expected:
            raise PumpError("unknown_method", "Unknown pump method.")
        if not isinstance(params, dict) or set(params) != expected[method]:
            raise PumpError("invalid_arguments", "Unexpected or missing arguments.")
        if method == "run" and (type(params["duration_ms"]) is not int or not 250 <= params["duration_ms"] <= 10000):
            raise PumpError("invalid_arguments", "duration_ms must be an integer from 250 to 10000.")
        if method == "direction" and params["direction"] not in ("A", "B"):
            raise PumpError("invalid_arguments", "direction must be A or B.")
        if method in ("stop", "disable"):
            if method == "disable":
                self.enabled = False
            self._exchange("STOP", "OK STOP; direction preserved")
            return self._state()
        state = self._state()
        if method == "status":
            return state
        if method == "enable":
            self._require_stopped(state)
            self.enabled = True
            return {**state, "enabled": True}
        if not self.enabled:
            raise PumpError("disabled", "Enable control after the operator confirms local ON and a stopped rotor.")
        self._require_stopped(state)
        if method == "direction":
            if state["direction"] != params["direction"]:
                self._exchange("DIR " + params["direction"], "OK direction set;")
        elif method == "run":
            self._exchange("ARM", "OK ARMED one pulse within 10s")
            self._exchange("PULSE " + str(params["duration_ms"]), "OK PULSE ms=" + str(params["duration_ms"]))
        return self._state()

    def close(self):
        with self.lock:
            self.enabled = False
            try:
                if self.serial.fd is not None:
                    self._exchange("STOP", "OK STOP; direction preserved")
            except (OSError, UnicodeError, PumpError):
                LOG.warning("Shutdown STOP not acknowledged; check the pump locally.")
            finally:
                self.serial.close()
