from __future__ import annotations

import argparse
import codecs
import json
import os
import secrets
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

from blunder.shell.server import ShellRuntime, make_handler


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the authenticated Blunder vNext local core service.")
    parser.add_argument("--root", required=True, help="Authorized repository root")
    parser.add_argument("--static-root", required=False, help="Recovery shell static root")
    parser.add_argument("--host", default="127.0.0.1", help="Loopback bind host")
    parser.add_argument("--port", type=int, default=0, help="Loopback bind port; zero allocates an ephemeral port")
    parser.add_argument("--token-stdin", action="store_true", help="Read the session token from one stdin line")
    return parser.parse_args(arguments)


def parse_session_token_line(payload: bytes) -> str:
    framed_payload = payload.removeprefix(codecs.BOM_UTF8).rstrip(b"\r\n")
    try:
        token = framed_payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("Blunder core session token from stdin must contain only ASCII characters.") from error
    if len(token) < 32 or len(token) > 256:
        raise ValueError("Blunder core session token from stdin must be 32 to 256 ASCII characters.")
    return token


def session_token(token_stdin: bool) -> str:
    if token_stdin:
        return parse_session_token_line(sys.stdin.buffer.readline())
    return secrets.token_urlsafe(48)


def main() -> int:
    arguments = parse_arguments(sys.argv[1:])
    root = Path(arguments.root).resolve()
    if not root.is_dir():
        raise ValueError(f"Blunder core repository root does not exist: {root}")
    static_root = Path(arguments.static_root).resolve() if arguments.static_root else root / "blunder" / "shell" / "static"
    token = session_token(bool(arguments.token_stdin))
    runtime = ShellRuntime(root, static_root, token)
    server = ThreadingHTTPServer((str(arguments.host), int(arguments.port)), make_handler(runtime))
    ready = {
        "schema": "obtuse.blunder.core-ready.v1",
        "status": "READY",
        "host": str(arguments.host),
        "port": server.server_port,
        "pid": os.getpid(),
        "launcherPid": os.getppid(),
    }
    print(json.dumps(ready, separators=(",", ":")), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
