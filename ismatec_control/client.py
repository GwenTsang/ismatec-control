"""One local JSON request per connection; never automatically retry commands."""
import json
import os
from pathlib import Path
import socket

from .errors import PumpError

MAX_MESSAGE = 65536


def default_socket():
    base = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return Path(base) / ("ismatec-control-" + str(os.getuid())) / "pump.sock"


def request(path, method, params=None):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(str(path))
            message = {"method": method, "params": params if params is not None else {}}
            connection.sendall(json.dumps(message).encode("utf-8") + b"\n")
            with connection.makefile("rb") as stream:
                raw = stream.readline(MAX_MESSAGE + 1)
            if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                raise PumpError("service_protocol", "Incomplete or oversized service response; outcome unknown.")
            response = json.loads(raw)
            if not isinstance(response, dict) or type(response.get("ok")) is not bool:
                raise PumpError("service_protocol", "Invalid service response; outcome unknown.")
            return response
    except OSError as exc:
        raise PumpError("service_unavailable", "Cannot reach the pump service. If a command was sent, its outcome "
                        "is unknown; do not retry motion automatically. Use status/stop or check locally.", cause=str(exc)) from exc
    except (ValueError, UnicodeError) as exc:
        raise PumpError("service_protocol", "Invalid service response; outcome unknown.") from exc
