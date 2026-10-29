"""Binding.

This app has no authentication — the tailnet is its only boundary. The machine
it runs on also has a public address. A fallback to 0.0.0.0 would therefore put
the whole library on the open internet, and it would do so silently: the server
would start, the phone would work, and nothing would look wrong.

So this is tested even though almost nothing else about the server is.
"""

import subprocess

import pytest

from muse import net


class FakeRun:
    """Stands in for the tailscale and ip binaries."""

    def __init__(self, outputs: dict[str, str], failing: set[str] = frozenset()):
        self.outputs = outputs
        self.failing = failing

    def __call__(self, argv, **kwargs):
        binary = argv[0].rsplit("/", 1)[-1]
        if binary in self.failing:
            return subprocess.CompletedProcess(argv, 1, "", "not running")
        return subprocess.CompletedProcess(argv, 0, self.outputs.get(binary, ""), "")


IP_OUTPUT_WITH_TAILNET = (
    "1: lo    inet 127.0.0.1/8 scope host lo\n"
    "2: enx0  inet 121.130.180.238/26 scope global enx0\n"
    "3: tailscale0 inet 100.122.149.75/32 scope global tailscale0\n"
)

IP_OUTPUT_WITHOUT_TAILNET = (
    "1: lo    inet 127.0.0.1/8 scope host lo\n"
    "2: enx0  inet 121.130.180.238/26 scope global enx0\n"
    "3: docker0 inet 172.17.0.1/16 scope global docker0\n"
)


@pytest.fixture
def binaries(monkeypatch):
    monkeypatch.setattr(net.shutil, "which", lambda name: f"/usr/bin/{name}")
    return monkeypatch


def test_the_tailscale_cli_is_believed(binaries):
    binaries.setattr(net.subprocess, "run", FakeRun({"tailscale": "100.122.149.75\n"}))
    assert net.tailnet_address() == "100.122.149.75"


def test_falls_back_to_reading_interfaces(binaries):
    binaries.setattr(
        net.subprocess,
        "run",
        FakeRun({"ip": IP_OUTPUT_WITH_TAILNET}, failing={"tailscale"}),
    )
    assert net.tailnet_address() == "100.122.149.75"


def test_both_families_are_returned_with_ipv4_first(binaries):
    """MagicDNS answers with an A and an AAAA. Binding only one family makes a
    browser that picks the other stall until it gives up and retries."""
    binaries.setattr(
        net.subprocess,
        "run",
        FakeRun({"tailscale": "100.122.149.75\nfd7a:115c:a1e0::435:954c\n"}),
    )
    assert net.tailnet_addresses() == [
        "100.122.149.75",
        "fd7a:115c:a1e0::435:954c",
    ]
    # The address a human types is still the one reported first.
    assert net.tailnet_address() == "100.122.149.75"


def test_a_tailnet_ipv6_address_alone_is_enough(binaries):
    binaries.setattr(
        net.subprocess, "run", FakeRun({"tailscale": "fd7a:115c:a1e0::435:954c\n"})
    )
    assert net.require_tailnet_addresses() == ["fd7a:115c:a1e0::435:954c"]


def test_an_unrelated_ipv6_address_is_not_the_tailnet(binaries):
    """A regular ULA or a public v6 address is not a tailnet address, and
    binding one would be a wide bind by another name."""
    binaries.setattr(
        net.subprocess,
        "run",
        FakeRun(
            {"ip": "1: eth0 inet6 fd00:dead:beef::1/64 scope global eth0\n"},
            failing={"tailscale"},
        ),
    )
    assert net.tailnet_addresses() == []


def test_a_public_address_is_never_mistaken_for_the_tailnet(binaries):
    """The whole point. 121.130.180.238 is this machine's real public address."""
    binaries.setattr(
        net.subprocess,
        "run",
        FakeRun({"ip": IP_OUTPUT_WITHOUT_TAILNET}, failing={"tailscale"}),
    )
    assert net.tailnet_address() is None


def test_it_refuses_to_start_without_a_tailnet_address(binaries):
    binaries.setattr(
        net.subprocess,
        "run",
        FakeRun({"ip": IP_OUTPUT_WITHOUT_TAILNET}, failing={"tailscale"}),
    )
    with pytest.raises(net.NoTailnetAddress) as caught:
        net.require_tailnet_address()

    # The refusal has to explain itself, or the next person "fixes" it.
    assert "0.0.0.0" in str(caught.value)


def test_a_stopped_tailscale_does_not_crash_the_lookup(binaries):
    def explodes(argv, **kwargs):
        raise OSError("no such binary")

    binaries.setattr(net.subprocess, "run", explodes)
    assert net.tailnet_address() is None


def test_no_binaries_at_all_means_no_address(monkeypatch):
    monkeypatch.setattr(net.shutil, "which", lambda name: None)
    assert net.tailnet_address() is None


@pytest.mark.parametrize(
    "address,inside",
    [
        ("100.64.0.1", True),
        ("100.122.149.75", True),
        ("100.127.255.254", True),
        ("100.128.0.1", False),   # just outside the range
        ("100.63.255.255", False),
        ("fd7a:115c:a1e0::435:954c", True),
        ("fd7a:115c:a1e1::1", False),   # just outside the /48
        ("fd00:dead:beef::1", False),   # an ordinary ULA
        ("2001:db8::1", False),
        ("121.130.180.238", False),
        ("192.168.1.10", False),
        ("172.17.0.1", False),
        ("127.0.0.1", False),
        ("not-an-address", False),
    ],
)
def test_tailnet_range_boundaries(address, inside):
    assert net._in_tailnet(address) is inside
