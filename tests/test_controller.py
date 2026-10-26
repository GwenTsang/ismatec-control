import os
import threading
import time
import unittest

from fake_uno import FakeUno
from ismatec_control.controller import Controller
from ismatec_control.errors import PumpError
from ismatec_control.serial_port import SerialPort


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.uno = FakeUno()
        self.controller = Controller(self.uno.path, boot_delay=0)

    def tearDown(self):
        self.controller.close()
        self.uno.close()

    def call(self, method, **params):
        return self.controller.call(method, params)

    def assert_error(self, code, method, **params):
        with self.assertRaises(PumpError) as error:
            self.call(method, **params)
        self.assertEqual(error.exception.code, code)

    def test_startup_is_verified_stopped_and_disabled(self):
        self.assertEqual(self.uno.commands[:3], ["ID", "STOP", "STATUS"])
        state = self.call("status")
        self.assertEqual(state["commanded_run"], "OFF")
        self.assertFalse(state["enabled"])
        self.assertFalse(state["motion_feedback"])
        self.assert_error("disabled", "run", duration_ms=250)
        self.assertNotIn("ARM", self.uno.commands)

    def test_pulse_auto_stop_guard_and_direction_preservation(self):
        self.call("enable")
        state = self.call("direction", direction="B")
        self.assertGreater(state["guard_remaining_ms"], 0)
        self.assert_error("stop_guard", "run", duration_ms=250)
        time.sleep(0.1)
        self.assertEqual(self.call("run", duration_ms=250)["commanded_run"], "ON")
        time.sleep(0.3)
        state = self.call("status")
        self.assertEqual((state["commanded_run"], state["direction"]), ("OFF", "B"))
        self.assertGreater(state["guard_remaining_ms"], 0)
        self.assertEqual(self.call("stop")["direction"], "B")

    def test_stop_preempts_pulse_and_disable_blocks_motion(self):
        self.call("enable")
        self.call("run", duration_ms=10000)
        self.assertEqual(self.call("stop")["commanded_run"], "OFF")
        self.assertFalse(self.call("disable")["enabled"])
        self.assert_error("disabled", "direction", direction="B")

    def test_running_interlock_does_not_restart_or_reverse(self):
        self.call("enable")
        self.call("run", duration_ms=10000)
        self.assert_error("running", "run", duration_ms=10000)
        self.assert_error("running", "direction", direction="B")
        self.assertEqual(self.uno.commands.count("PULSE 10000"), 1)
        self.assertNotIn("DIR B", self.uno.commands)

    def test_invalid_arguments_never_reach_serial(self):
        before = self.uno.commands[:]
        for duration in (True, False, 1.5, "250", None, -1, 249, 10001, 10**20):
            self.assert_error("invalid_arguments", "run", duration_ms=duration)
        self.assert_error("invalid_arguments", "run")
        self.assert_error("invalid_arguments", "stop", unexpected=True)
        self.assert_error("invalid_arguments", "direction", direction="C")
        self.assert_error("unknown_method", "raw")
        self.assertEqual(self.uno.commands, before)

    def test_concurrent_runs_only_start_once(self):
        self.call("enable")
        outcomes = []
        def attempt():
            try:
                outcomes.append(self.call("run", duration_ms=10000)["commanded_run"])
            except PumpError as exc:
                outcomes.append(exc.code)
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
        self.assertCountEqual(outcomes, ["ON", "running"])
        self.assertEqual(self.uno.commands.count("PULSE 10000"), 1)

    def test_lost_start_acknowledgement_latches_fault_without_retry(self):
        self.call("enable")
        self.uno.drop_pulse_ack = True
        self.assert_error("connection_fault", "run", duration_ms=250)
        time.sleep(0.03)
        self.assertFalse(self.uno.running)
        self.assertEqual(self.uno.commands.count("PULSE 250"), 1)
        self.assert_error("connection_fault", "enable")
        self.assert_error("connection_fault", "run", duration_ms=250)
        self.assertFalse(self.controller.enabled)

    def test_unexpected_reset_invalidates_enable(self):
        self.call("enable")
        self.uno.emit("READY ISMATEC_PUMP_TEST_v2 STOP direction=A DISARMED")
        self.assert_error("connection_fault", "status")
        self.assertFalse(self.controller.enabled)

    def test_wrong_firmware_cannot_open_controller(self):
        other = FakeUno()
        other.identity = "ID RELAY_BENCH_v1"
        try:
            with self.assertRaises(PumpError) as error:
                Controller(other.path, boot_delay=0)
            self.assertEqual(error.exception.code, "firmware_mismatch")
            self.assertNotIn("ARM", other.commands)
        finally:
            other.close()

    def test_failed_second_owner_does_not_send_stop(self):
        self.call("enable")
        self.call("run", duration_ms=10000)
        before = self.uno.commands[:]
        with self.assertRaises(OSError):
            SerialPort(self.uno.path)
        self.assertEqual(self.uno.commands, before)
        self.assertTrue(self.uno.running)

    def test_serial_eof_is_a_fault_not_a_cached_state(self):
        self.uno.done.set()
        self.uno.thread.join(1)
        os.close(self.uno.master)
        # Keep cleanup's fd valid without reconnecting the controller.
        self.uno.master = os.open("/dev/null", os.O_RDONLY)
        self.assert_error("connection_fault", "status")
        with self.assertLogs("ismatec_control.controller", level="WARNING") as messages:
            self.controller.close()
        self.assertIn("STOP not acknowledged", messages.output[0])

    def test_close_requests_stop(self):
        self.call("enable")
        self.call("run", duration_ms=10000)
        self.controller.close()
        self.assertFalse(self.uno.running)
        self.assertEqual(self.uno.commands[-1], "STOP")
