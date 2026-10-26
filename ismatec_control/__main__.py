"""CLI entry point: JSON output, nonzero status on failures."""
import argparse
import glob
import json
import logging
import sys
import time

from . import __version__
from .client import default_socket, request
from .errors import PumpError


def parser():
    result = argparse.ArgumentParser(description="Control an ISMATEC DIN pump through a persistent Uno service.")
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--socket", default=str(default_socket()), help="local service socket (same for all clients)")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("ports", help="list serial paths without opening devices")
    service = commands.add_parser("serve", help="start with pump power OFF; keep running during use")
    service.add_argument("--port", required=True, help="explicit USB serial path; prefer /dev/serial/by-id/...")
    service.add_argument("--verbose", action="store_true", help="log serial commands and acknowledgements to stderr")
    commands.add_parser("status", help="read commanded state; there is no rotor feedback")
    commands.add_parser("enable", help="enable motion after the operator confirms local ON and standstill")
    commands.add_parser("disable", help="stop and disable motion")
    commands.add_parser("stop", help="stop now, preserving direction")
    direction = commands.add_parser("direction", help="select A/B after observed stop and the 5-second guard")
    direction.add_argument("direction", choices=("A", "B"))
    run = commands.add_parser("run", help="run once for 250..10000 ms; the Uno stops independently")
    run.add_argument("--duration-ms", type=int, required=True)
    run.add_argument("--wait", action="store_true", help="wait for commanded OFF; Ctrl-C requests STOP")
    commands.add_parser("mcp", help="stdio MCP bridge to the existing service; never opens USB")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "ports":
            ports = sorted(set(glob.glob("/dev/serial/by-id/*") + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")))
            response = {"ok": True, "result": {"ports": ports}}
        elif args.command == "serve":
            from .service import serve
            logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, stream=sys.stderr,
                                format="%(asctime)s %(levelname)s %(message)s")
            serve(args.port, args.socket)
            return 0
        elif args.command == "mcp":
            from .mcp import serve_stdio
            serve_stdio(args.socket)
            return 0
        else:
            params = {}
            if args.command == "run":
                params = {"duration_ms": args.duration_ms}
            elif args.command == "direction":
                params = {"direction": args.direction}
            try:
                response = request(args.socket, args.command, params)
                if args.command == "run" and args.wait and response["ok"]:
                    deadline = time.monotonic() + args.duration_ms / 1000 + 5
                    while True:
                        response = request(args.socket, "status")
                        if not response["ok"] or response["result"]["commanded_run"] == "OFF":
                            break
                        if time.monotonic() >= deadline:
                            request(args.socket, "stop")
                            raise PumpError("wait_timeout", "Pulse completion was not confirmed; STOP requested.")
                        time.sleep(0.1)
            except KeyboardInterrupt:
                if args.command == "run" and args.wait:
                    response = request(args.socket, "stop")
                    print(json.dumps(response), flush=True)
                return 130
        print(json.dumps(response), flush=True)
        return 0 if response["ok"] else 1
    except (PumpError, OSError) as exc:
        error = exc.as_dict() if isinstance(exc, PumpError) else {"code": "io_error", "message": str(exc)}
        print(json.dumps({"ok": False, "error": error}), flush=True)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
