"""Checks log levels, stderr output, and separation from the supplied package logger."""

from __future__ import annotations

import logging
from collections.abc import Callable

import pytest

from researcher.bootstrap import configure_logging
from researcher.config import Settings

pytestmark = pytest.mark.usefixtures("restore_researcher_logger")

SettingsFactory = Callable[..., Settings]


def test_diagnostics_go_to_stderr_and_never_stdout(
    make_settings: SettingsFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """stdout carries the answer, so a log line there would corrupt a redirect.

    ``researcher ask "…" > answer.md`` has to produce a file worth keeping.
    """
    configure_logging(make_settings(log_level="INFO"))

    logging.getLogger("researcher.something").info("a diagnostic")

    captured = capsys.readouterr()
    assert "a diagnostic" in captured.err
    assert captured.out == ""


def test_the_configured_level_is_applied(make_settings: SettingsFactory) -> None:
    configure_logging(make_settings(log_level="DEBUG"))

    assert logging.getLogger("researcher").level == logging.DEBUG


def test_a_quiet_level_actually_suppresses(make_settings: SettingsFactory) -> None:
    """Setting the level is only meaningful if it filters."""
    configure_logging(make_settings(log_level="WARNING"))

    assert not logging.getLogger("researcher").isEnabledFor(logging.INFO)
    assert logging.getLogger("researcher").isEnabledFor(logging.WARNING)


def test_logging_is_scoped_to_our_own_namespace(make_settings: SettingsFactory) -> None:
    """Turning on DEBUG for us must not turn it on for httpx and asyncpg."""
    root = logging.getLogger()
    before = list(root.handlers)

    configure_logging(make_settings(log_level="DEBUG"))

    assert root.handlers == before


def test_configuring_logging_twice_does_not_double_every_line(
    make_settings: SettingsFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """One record, one line, however many times setup runs."""
    logger = logging.getLogger("researcher")

    configure_logging(make_settings())
    configure_logging(make_settings())

    assert len(logger.handlers) == 1
    assert logger.propagate is False

    logger.info("a diagnostic")
    assert capsys.readouterr().err.count("a diagnostic") == 1
