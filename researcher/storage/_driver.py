"""Driver-error translation shared by the PostgreSQL storage adapters.

Private to :mod:`researcher.storage`. The tuple below is defined once rather
than repeated in each adapter, because three copies would drift the first time
one of them learned about a new failure mode and the others did not — and the
symptom of that drift is a raw ``asyncpg`` exception escaping into the
application, which is precisely what the interface contract forbids.

Messages produced here are deliberately generic. ``StorageError`` is shown to
users and written to logs, while the underlying driver message may quote a row
and a DSN may carry a password, so the original is attached as ``__cause__``
for a debugger but never folded into the text.
"""

from __future__ import annotations

import asyncio

import asyncpg
from pydantic import ValidationError

from researcher.errors import StorageError

__all__ = ["DRIVER_ERRORS", "READ_ERRORS", "translate"]

#: Everything the driver can raise. ``asyncpg.PostgresError`` covers server-side
#: failures (syntax, constraints, permissions); ``InterfaceError`` covers pool
#: misuse; the remainder are connection and timeout failures that arrive as
#: ordinary OS errors.
DRIVER_ERRORS: tuple[type[BaseException], ...] = (
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    ConnectionError,
    OSError,
    TimeoutError,
    asyncio.TimeoutError,
)

#: Adds malformed-payload failures to the driver set. A row written by an older
#: schema, or edited by hand, parses as JSON but fails model validation; that is
#: a storage fault, not a caller error.
READ_ERRORS: tuple[type[BaseException], ...] = (*DRIVER_ERRORS, ValidationError)


def translate(exc: BaseException, action: str) -> StorageError:
    """Build a :class:`StorageError` describing a failed storage operation.

    Returns rather than raises, so callers write ``raise translate(...) from exc``
    and the original stays attached as ``__cause__``.

    Args:
        exc: The driver or validation exception being translated.
        action: Short description of the operation, phrased to complete
            "… failed" — for example ``"reading a cached result"``.

    Returns:
        A :class:`StorageError` carrying no driver detail.
    """
    return StorageError(f"{action} failed", source="storage")
