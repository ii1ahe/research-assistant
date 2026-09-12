"""Persistence layer.

``interfaces.py`` defines the contracts the application depends on. The
implementations land in Phase 3: a PostgreSQL-backed one used by the
application, and an in-memory one used by the offline test suite (ADR-004).

This package intentionally has no import side effects, so importing it never
opens a connection, reads settings or touches the filesystem.
"""

from __future__ import annotations
