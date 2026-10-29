"""Finding the tailnet address.

There is no authentication layer in this app. The tailnet *is* the boundary, by
explicit decision — so the one thing that must never happen is the server
quietly listening on every interface because it could not find the tailnet.
This machine has a public address on `enx00e04c344dc0`; a fallback to
`0.0.0.0` would put a music library with no login on the open internet.

So there is no fallback. No tailnet address means the server refuses to start.
"""

import ipaddress
import shutil
import subprocess

# Tailscale hands out IPv4 from the CGNAT range and IPv6 from its own ULA
# prefix. Both belong to the tailnet and nothing else routes to either.
TAILNET_RANGE = ipaddress.ip_network("100.64.0.0/10")
TAILNET_RANGE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


class NoTailnetAddress(RuntimeError):
    pass


def _from_tailscale_cli() -> list[str]:
    """Ask Tailscale directly. Authoritative when it is installed and up."""
    binary = shutil.which("tailscale")
    if not binary:
        return []
    try:
        output = subprocess.run(
            [binary, "ip"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if output.returncode != 0:
        return []
    return [
        line.strip()
        for line in output.stdout.splitlines()
        if line.strip() and _in_tailnet(line.strip())
    ]


def _from_interfaces() -> list[str]:
    """Fall back to reading the interface list, for a machine where the CLI is
    absent but the interface is up."""
    binary = shutil.which("ip")
    if not binary:
        return []
    try:
        output = subprocess.run(
            [binary, "-o", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in output.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        address = fields[3].split("/")[0]
        if _in_tailnet(address):
            found.append(address)
    return found


def _in_tailnet(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return parsed in TAILNET_RANGE or parsed in TAILNET_RANGE_V6


def tailnet_addresses() -> list[str]:
    """Every tailnet address this machine holds, IPv4 first.

    Both families are bound, because MagicDNS answers with an A *and* an AAAA
    record. A browser resolving `ianshomeserver` may well try the v6 address
    first, and if nothing is listening there it stalls until Happy Eyeballs
    gives up and retries over v4. Binding both removes the stall.
    """
    found = _from_tailscale_cli() or _from_interfaces()
    # Deduplicate, keep IPv4 first: it is the address a human types.
    ordered = sorted(dict.fromkeys(found), key=lambda a: ":" in a)
    return ordered


def tailnet_address() -> str | None:
    """The primary (IPv4) tailnet address, or None."""
    addresses = tailnet_addresses()
    return addresses[0] if addresses else None


def require_tailnet_addresses() -> list[str]:
    """Every tailnet address, or a refusal to start.

    Deliberately loud and deliberately fatal.
    """
    addresses = tailnet_addresses()
    if not addresses:
        raise NoTailnetAddress(
            "No Tailscale address found, so there is nothing safe to bind to.\n"
            "This server has no authentication -- the tailnet is its only "
            "boundary -- so it will not fall back to 0.0.0.0.\n"
            "Start Tailscale (`sudo tailscale up`) and try again, or set "
            "MUSE_HOST explicitly if you know what you are doing."
        )
    return addresses


def require_tailnet_address() -> str:
    """The primary tailnet address, or a refusal to start."""
    return require_tailnet_addresses()[0]
