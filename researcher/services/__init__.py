"""Service layer: the application's outbound edges.

``ai_service`` is the only module allowed to import the supplied ``ai``
package, and ``resilience`` holds the retry and deadline policy every outbound
call is run under. ``http_client`` builds the one HTTP client those calls share.

Later phases add the orchestrator, which schedules the source fetches under a
concurrency bound (ADR-005), and the cache, which sits between the orchestrator
and :class:`~researcher.services.ai_service.AIService`.

This package intentionally has no import side effects, so importing it never
reads settings, opens a connection or constructs a provider.
"""

from __future__ import annotations
