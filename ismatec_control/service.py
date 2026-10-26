"""A persistent serial owner exposed through a user-private Unix socket."""
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import socketserver
import stat
import threading

from .client import MAX_MESSAGE
from .controller import Controller
from .errors import PumpError

LOG = logging.getLogger(__name__)


def reject_constant(value):
    raise ValueError("Invalid JSON constant: " + value)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(3)
        try:
            raw = self.rfile.readline(MAX_MESSAGE + 1)
            if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                raise PumpError("invalid_request", "Expected one bounded JSON line.")
            message = json.loads(raw, parse_constant=reject_constant)
            if (not isinstance(message, dict) or set(message) - {"method", "params"}
                    or not isinstance(message.get("method"), str)):
                raise PumpError("invalid_request", "Expected method and optional params.")
            result = self.server.controller.call(message["method"], message.get("params", {}))
            response = {"ok": True, "result": result}
        except PumpError as exc:
            response = {"ok": False, "error": exc.as_dict()}
        except (ValueError, UnicodeError, OSError) as exc:
            response = {"ok": False, "error": {"code": "invalid_request", "message": str(exc)}}
        try:
            self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")
        except OSError:
            # Losing a client does not extend or repeat its pulse.
            LOG.warning("Client disconnected before receiving the command result.")


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = False
    block_on_close = True


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PumpError("socket_permissions", "Socket directory must be owned by you, not a symlink, and mode 700.",
                        directory=str(path))


def serve(port, socket_path):
    path = Path(socket_path).absolute()
    private_directory(path.parent)
    # Lock before unlinking a stale socket or opening USB. A second service must
    # not disturb the first, even when both use the same socket path.
    lock_fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    controller = server = None
    bound = False
    stopping = threading.Event()
    previous = {}
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PumpError("service_busy", "A service already owns this socket.") from exc
        if path.exists() or path.is_symlink():
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise PumpError("socket_path", "Refusing to replace a non-socket file.")
            path.unlink()
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: stopping.set())
        controller = Controller(port)
        server = Server(str(path), Handler)
        bound = True
        os.chmod(path, 0o600)
        server.controller = controller
        server.timeout = 0.2
        print(json.dumps({"ok": True, "event": "ready", "socket": str(path),
                          "port": str(port), "enabled": False}), flush=True)
        while not stopping.is_set():
            server.handle_request()
    finally:
        if server:
            server.server_close()  # finish bounded requests before closing serial
        if controller:
            controller.close()
        if bound:
            path.unlink(missing_ok=True)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        os.close(lock_fd)
