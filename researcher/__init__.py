"""Researcher — asynchronous multi-source research assistant.

This package is the software-engineering layer built around the supplied,
immutable ``ai`` package. Importing it has no side effects: no configuration is
read, no network or database connection is opened, and no logging is configured.
All resource creation is explicit and happens in :mod:`researcher.bootstrap`.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
