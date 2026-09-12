"""Application layer.

Currently one module: :mod:`researcher.core.researcher`, which sequences a
single research operation — validate, retrieve, synthesise, persist, describe —
and returns a :class:`~researcher.models.ResearchResult` that carries the whole
story, including the parts that went wrong.

This package has no import side effects, so importing it never reads settings,
opens a connection or touches the network.
"""

from __future__ import annotations
