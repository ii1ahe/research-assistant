"""The guard that keeps the suite offline, tested rather than trusted.

A safety net that quietly stops working is worse than none, because it is
believed. The ``no_internet`` fixture in ``conftest.py`` is autouse and
invisible, so this module is where its two claims are written down: off-machine
traffic fails, and loopback still works.

The second matters as much as the first. ``test_storage.py`` runs real
PostgreSQL integration tests over loopback, so a guard that blocked everything
would have to be disabled — and a guard that is routinely disabled catches
nothing.
"""

from __future__ import annotations

import socket

import pytest

from tests import conftest


def test_reaching_an_external_host_is_refused() -> None:
    """The failure is raised before any packet leaves, so nothing is contacted."""
    probe = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="must run offline"):
            probe.connect(("example.com", 80))
    finally:
        probe.close()


def test_the_refusal_names_the_address_it_would_have_used() -> None:
    """The message has to say which line to go and fix."""
    probe = socket.socket()
    try:
        with pytest.raises(RuntimeError) as caught:
            probe.connect(("93.184.216.34", 443))
    finally:
        probe.close()

    assert "93.184.216.34" in str(caught.value)
    assert "respx" in str(caught.value)


def test_loopback_is_allowed() -> None:
    """The database integration tests need it, and it survives a pulled cable."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    client = socket.socket()
    try:
        client.connect(server.getsockname())
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize(
    "address",
    # "" is not an address at all — it is what a bind to every interface looks
    # like, and it stays on the permitted list rather than being parsed.
    ["127.0.0.1", "::1", "10.1.2.3", "172.18.0.2", "192.168.1.5", "169.254.1.1", ""],
)
def test_addresses_that_work_with_the_cable_pulled_are_permitted(address: str) -> None:
    """Loopback *and* the private ranges — the rule, not a list of three strings.

    ``172.18.0.2`` is the one that matters: it is where Docker Compose puts the
    database, and a guard that refused it made the PostgreSQL integration tests
    error inside the container while passing on the host, which is the shape of
    bug the guard was supposed to prevent rather than produce.
    """
    assert conftest._is_reachable_offline(address)


@pytest.mark.parametrize(
    "address",
    ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111", "example.com"],
)
def test_addresses_that_need_the_internet_are_still_refused(address: str) -> None:
    """The guarantee is unchanged: no test reaches the internet.

    A hostname is refused for the same reason as a public address — it is not
    something this machine can resolve and reach by itself, and the guard should
    not be the thing that finds out.
    """
    assert not conftest._is_reachable_offline(address)
