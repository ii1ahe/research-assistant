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
