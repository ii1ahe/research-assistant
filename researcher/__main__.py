"""Entry point for ``python -m researcher``.

Kept deliberately thin: argument parsing and exit-status mapping live in
:mod:`researcher.cli`.
"""

from __future__ import annotations

from researcher.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
