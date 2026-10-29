"""`python -m muse` — the single command that starts the player.

Binds 127.0.0.1 and nothing else, on a free port unless one is configured, and
announces it with a `MUSE_LISTENING <port>` line on stdout. Every `/api` request
must carry a per-launch token, so other local processes cannot read the library
(Decision 3).
"""

import dataclasses
import logging
import secrets
import socket
import sys

import uvicorn

from .api import create_app
from .config import Config

log = logging.getLogger("muse")


def _listener(address: str, port: int) -> socket.socket:
    """A listening socket bound to exactly one address."""
    # No SO_REUSEADDR: port 0 never needs it, and on Windows it would let
    # another process bind the same address.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((address, port))
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    config = Config.from_env()
    generated = config.token is None
    config = dataclasses.replace(config, token=config.token or secrets.token_urlsafe(32))

    try:
        sock = _listener("127.0.0.1", config.port)
    except OSError as error:
        print(f"Could not bind 127.0.0.1:{config.port} ({error})", file=sys.stderr)
        return 1

    port = sock.getsockname()[1]
    print(f"MUSE_LISTENING {port}", flush=True)
    if generated:
        log.info("Open http://127.0.0.1:%d/?t=%s", port, config.token)

    # Access lines carry ?t=<token> on every media/art/SSE URL (Decision 3).
    server = uvicorn.Server(uvicorn.Config(create_app(config), log_level="info", access_log=False))
    server.run(sockets=[sock])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
