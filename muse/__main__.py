"""`python -m muse` — the single command that starts the player.

Binds the tailnet addresses and nothing else. See `muse/net.py` for why there
is no fallback.
"""

import logging
import socket
import sys

import uvicorn

from .api import create_app
from .config import Config
from .net import NoTailnetAddress, require_tailnet_addresses

log = logging.getLogger("muse")


def _listener(address: str, port: int) -> socket.socket:
    """A listening socket bound to exactly one address."""
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family is socket.AF_INET6:
        # Without this the v6 socket would also accept v4 on every interface,
        # which is exactly the wide bind this app refuses to do.
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
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
    try:
        addresses = [config.host] if config.host else require_tailnet_addresses()
    except NoTailnetAddress as error:
        print(f"\n{error}\n", file=sys.stderr)
        return 1

    sockets = []
    for address in addresses:
        try:
            sockets.append(_listener(address, config.port))
        except OSError as error:
            # One family failing is survivable as long as the other listens —
            # a machine with IPv6 disabled should still serve the phone.
            log.warning("Could not bind %s:%d (%s)", address, config.port, error)
    if not sockets:
        print(
            f"\nNothing could be bound on port {config.port}. "
            "Is another copy already running?\n",
            file=sys.stderr,
        )
        return 1

    for address in addresses:
        host = f"[{address}]" if ":" in address else address
        log.info("Listening on http://%s:%d", host, config.port)

    server = uvicorn.Server(
        uvicorn.Config(create_app(config), log_level="info")
    )
    server.run(sockets=sockets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
