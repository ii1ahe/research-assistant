"""Groups database and stored-data errors and converts them into app storage errors."""

from __future__ import annotations

import asyncio

import asyncpg
from pydantic import ValidationError

from researcher.errors import StorageError

__all__ = ["DRIVER_ERRORS", "READ_ERRORS", "translate"]

# Database, pool, connection, and timeout errors handled by storage.
DRIVER_ERRORS: tuple[type[BaseException], ...] = (
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    ConnectionError,
    OSError,
    TimeoutError,
    asyncio.TimeoutError,
)

# Also handle stored JSON that fails model validation.
READ_ERRORS: tuple[type[BaseException], ...] = (*DRIVER_ERRORS, ValidationError)


def translate(exc: BaseException, action: str) -> StorageError:
    """Build a :class:`StorageError` describing a failed storage operation.

    Args:
        exc: The driver or validation exception being translated.
        action: Short description of the operation, phrased to complete
            "… failed" — for example ``"reading a cached result"``.

    Returns:
        A :class:`StorageError` carrying no driver detail.
    """
    return StorageError(f"{action} failed", source="storage")
