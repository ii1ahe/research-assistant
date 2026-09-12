"""Service layer: the application's outbound edges.

``ai_service`` is the only module allowed to import the supplied ``ai``
package, and ``resilience`` holds the retry and deadline policy every outbound
call is run under. ``http_client`` builds the one HTTP client those calls share.

Two modules sit above that boundary without going through it.
:mod:`~researcher.services.orchestrator` schedules the source fetches under a
concurrency bound and turns whatever happens into typed outcomes (ADR-005), and
:mod:`~researcher.services.cache` sits between it and
:class:`~researcher.services.ai_service.AIService`, making a storage fault cost
a fetch rather than a request.

This package intentionally has no import side effects, so importing it never
reads settings, opens a connection or constructs a provider.
"""

from __future__ import annotations
