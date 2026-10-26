"""Test-only PTY peer. Models serial behavior, not physical pump mechanics.

The guard is shortened to 80 ms for host tests. Real firmware timing is verified
separately by compiling it and by the recorded supervised hardware validation.
"""
import errno
import os
import pty
import select
import threading
import time


class FakeUno:
    def __init__(self):
        self.master, self.anchor = pty.openpty()
        self.path = os.ttyname(self.anchor)
        self.commands = []
        self.running = False
        self.direction = "A"
        self.armed = False
        self.stopped_at = time.monotonic() - 1
        self.deadline = 0
        self.drop_pulse_ack = False
        self.identity = "ID ISMATEC_PUMP_TEST_v2 max_pulse_ms=10000 stop_guard_ms=5000 watchdog_ms=1000"
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def emit(self, text):
        os.write(self.master, (text + "\r\n").encode("ascii"))

    def stop(self):
        if self.running:
            self.stopped_at = time.monotonic()
        self.running = self.armed = False

    def guard(self):
        return max(0, int((self.stopped_at + 0.08 - time.monotonic()) * 1000))

    def execute(self, command):
        self.commands.append(command)
        if command == "ID":
            self.emit(self.identity)
        elif command in ("STOP", "OFF"):
            self.stop()
            self.emit("OK STOP; direction preserved")
        elif command == "STATUS":
            remaining = max(0, int((self.deadline - time.monotonic()) * 1000)) if self.running else 0
            self.emit(f"STATE commanded_run={'ON' if self.running else 'OFF'} direction={self.direction} "
                      f"armed={int(self.armed)} guard_remaining_ms={self.guard()} pulse_remaining_ms={remaining}")
        elif command == "ARM":
            if self.running or self.guard():
                self.stop()
                self.emit("ERR stopped/disarmed: wait stopped at least 5s")
            else:
                self.armed = True
                self.emit("OK ARMED one pulse within 10s")
        elif command.startswith("DIR "):
            if self.running or self.guard():
                self.stop()
                self.emit("ERR stopped/disarmed: direction interlock")
            else:
                self.direction = command[4:]
                self.armed = False
                self.stopped_at = time.monotonic()
                self.emit("OK direction set; stopped/disarmed; observe pump before rearming")
        elif command.startswith("PULSE "):
            if not self.armed or self.running or self.guard():
                self.stop()
                self.emit("ERR stopped/disarmed: pulse requires fresh ARM")
            else:
                duration = int(command.split()[1])
                self.armed = False
                self.running = True
                self.deadline = time.monotonic() + duration / 1000
                if not self.drop_pulse_ack:
                    self.emit(f"OK PULSE ms={duration}")

    def loop(self):
        pending = b""
        while not self.done.is_set():
            try:
                if self.running and time.monotonic() >= self.deadline:
                    self.stop()
                    self.emit("AUTO_STOP pulse complete; direction preserved")
                if select.select([self.master], [], [], 0.005)[0]:
                    data = os.read(self.master, 4096)
                    pending += data
                    while b"\n" in pending:
                        raw, pending = pending.split(b"\n", 1)
                        self.execute(raw.decode("ascii").rstrip("\r"))
            except OSError as exc:
                if exc.errno not in (errno.EIO, errno.EBADF):
                    raise
                return

    def close(self):
        self.done.set()
        self.thread.join(timeout=1)
        os.close(self.master)
        os.close(self.anchor)
