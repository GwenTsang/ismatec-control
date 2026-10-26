"""115200-baud Linux serial transport using only the standard library."""
import fcntl
import os
import select
import termios
import time
import tty

from .errors import PumpError


class SerialPort:
    def __init__(self, path):
        self.fd = None
        self.exclusive = False
        self.pending = b""
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            self.exclusive = True
            tty.setraw(self.fd)
            settings = termios.tcgetattr(self.fd)
            settings[4] = settings[5] = termios.B115200
            settings[2] |= termios.CLOCAL | termios.CREAD
            settings[2] &= ~(termios.CRTSCTS | termios.HUPCL)
            termios.tcsetattr(self.fd, termios.TCSANOW, settings)
        except OSError:
            # Never send commands if ownership/configuration failed.
            self.close()
            raise

    def close(self):
        if self.fd is not None:
            try:
                if self.exclusive:
                    try:
                        fcntl.ioctl(self.fd, termios.TIOCNXCL)
                    except OSError:
                        # A physically unplugged device may reject the ioctl.
                        # Closing the descriptor still releases our ownership.
                        pass
            finally:
                os.close(self.fd)
                self.fd = None

    def send(self, command):
        data = (command + "\n").encode("ascii")
        deadline = time.monotonic() + 1
        while data:
            if not select.select([], [self.fd], [], max(0, deadline - time.monotonic()))[1]:
                raise PumpError("serial_timeout", "Serial write timed out; outcome unknown.")
            count = os.write(self.fd, data)
            if count <= 0:
                raise PumpError("disconnected", "Serial device disconnected.")
            data = data[count:]

    def readline(self, deadline):
        while True:
            if b"\n" in self.pending:
                raw, self.pending = self.pending.split(b"\n", 1)
                return raw.decode("ascii").rstrip("\r")
            if len(self.pending) > 4096:
                raise PumpError("serial_protocol", "Oversized serial response.")
            timeout = deadline - time.monotonic()
            if timeout <= 0 or not select.select([self.fd], [], [], timeout)[0]:
                raise PumpError("serial_timeout", "No serial acknowledgement; outcome unknown.")
            data = os.read(self.fd, 4096)
            if not data:
                raise PumpError("disconnected", "Serial device disconnected.")
            self.pending += data

    def discard_boot_output(self):
        self.pending = b""
        termios.tcflush(self.fd, termios.TCIFLUSH)

    def drain(self):
        """Collect pending complete lines without waiting for future input."""
        for _ in range(32):
            if not select.select([self.fd], [], [], 0)[0]:
                break
            data = os.read(self.fd, 4096)
            if not data:
                raise PumpError("disconnected", "Serial device disconnected.")
            self.pending += data
            if len(self.pending) > 16384:
                raise PumpError("serial_protocol", "Excessive unsolicited serial output.")
        lines = []
        while b"\n" in self.pending:
            raw, self.pending = self.pending.split(b"\n", 1)
            lines.append(raw.decode("ascii").rstrip("\r"))
        return lines
