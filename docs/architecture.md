# Architecture Decision Record

Decisions that shape the `researcher` application. Each entry records the
context, the decision, its consequences and the alternatives that were rejected.
Written during Phase 1 and updated as later phases land.

Related documents: [`../TOPIC.md`](../TOPIC.md) (topic contract),
`SOFTWARE_PROJECT.pdf` (authoritative brief), `docs/COMMON_PITFALLS.md` (course
guidance).

---

## ADR-001 — Flat package layout

**Context.** The application must ship a `researcher` package that is runnable as
`python -m researcher`, alongside the supplied `ai` package, which is immutable
and must remain at its original location. The course's `Dockerfile.template`
builds by copying `requirements.txt`, installing it, copying the source tree and
running `python -m <package>` from `/app` — with no `pip install .` step. The
grading harness diffs `provided-topic-folder/ai/` against `<your-repo>/ai/`, so
the repository root must contain `ai/` directly.

**Decision.** Use a flat layout: `ai/` and `researcher/` sit side by side at the
repository root, with `pyproject.toml` discovering both.

```toml
[tool.setuptools.packages.find]
include = ["researcher*", "ai*"]
```

**Consequences.**

- The stock Dockerfile works unchanged: with `WORKDIR /app`, both packages are
  importable because the working directory is on `sys.path`.
- `python -m researcher` and the `researcher` console script both resolve.
- The immutable `ai/` package is never moved, copied or wrapped in a shim, so
  the grading diff stays clean.
- Editable installs (`pip install -e .`) work for both packages with no path
  configuration.
- Cost: the package-name namespace at the repository root is shared with the
  supplied package. Acceptable — `ai` is a fixed, known name.

**Alternatives rejected.**

- *`src/researcher/` (the layout proposed in `ARCHITECTURE_BLUEPRINT.md`).*
  Rejected because `ai/` cannot move to `src/` without breaking the grading
  diff, so `src/researcher/` and root-level `ai/` would require either an
  explicit `packages`/`package-dir` mapping (brittle as subpackages are added)
  or a `pip install .` step plus `PYTHONPATH=/app/src` in the container. The
  stock Dockerfile does neither, so a `src/` layout trades a real Dockerfile
  build failure for no gain.
- *Mirroring `ai/` into `src/`.* Rejected — duplicating an immutable input
  invites the two copies drifting.
- *Flat modules directly under a `src/`-on-`sys.path` layout, as sketched in
  `TOPIC.md`.* Rejected — that layout is not installable as a package.

---

## ADR-002 — PostgreSQL as the persistence baseline

**Context.** The project documentation is internally inconsistent: the
authoritative brief requires *"persistent storage (PostgreSQL or AsyncPG
minimum)"*, while `docs/TIMELINE.md`, `README.md` and the report/README
templates mention SQLite. `TOPIC.md` permits a lightweight cache
(PostgreSQL, filesystem JSON, or in-memory). The brief's rubric is
non-negotiable.

**Decision.** Use **PostgreSQL** accessed through the `asyncpg` driver for both
the source-result cache and the research-session store. Treat SQLite mentions in
templates as generic placeholders and adapt them.

`asyncpg` is a driver, not a database. The brief's phrasing *"PostgreSQL or
AsyncPG"* is read as "PostgreSQL accessed through an appropriate driver".

**Consequences.**

- Requires a running PostgreSQL instance for integration work and for the
  container deployment. Development uses a local instance or the Compose
  service defined in Phase 8.
- The SQLite-flavoured examples in the templates must be rewritten against the
  real schema rather than copied.
- Mocks alone cannot prove SQL or migration correctness, so at least one test
  runs against a real database even though the main suite is offline.

**Alternatives rejected.** *SQLite* — satisfies a template but not the
authoritative brief. *Filesystem JSON* — explicitly allowed by `TOPIC.md` for
the cache but insufficient as the session store.

---

## ADR-003 — Layered modular monolith

**Context.** The brief's grading scope is orchestration, persistence,
validation, resilience, testing and deployment. Microservices, a message broker
and a separate frontend are all optional and none is required by Topic 4.

**Decision.** Build a layered modular monolith with explicit dependency
direction:

```
presentation   cli.py, __main__.py, rendering.py
application    core/researcher.py
services       orchestrator, ai_service, cache, resilience, http_client
domain         models.py, errors.py, validation.py, config.py
storage        interfaces.py -> postgres.py, cache_store.py, session_repository.py
```

`bootstrap.py` is the only module allowed to know the whole graph. Models and
interfaces must not import bootstrap, CLI, or concrete repositories, so the
supplied package's acyclic property is preserved.

**Consequences.** Dependencies are injected rather than imported at call sites;
`ResearchService` receives an orchestrator, an AI service and a repository, which
makes it directly testable with doubles. Storage, AI resilience and CLI
rendering can be developed independently once the contracts in Phase 2 freeze.

**Alternatives rejected.** *Microservices* and *a message broker* — add
deployment surface and failure modes with no requirement behind them.

---

## ADR-004 — Storage behind interfaces, in-memory implementation for tests

**Context.** The brief requires tests that pass offline, with all network access
mocked, while also requiring real PostgreSQL persistence. A test suite that
needs a live database cannot satisfy the offline requirement.

**Decision.** Define `CacheStore` and `SessionRepository` as typed asynchronous
protocols in `storage/interfaces.py`. Provide two implementations: the
PostgreSQL one used by the application, and an in-memory one used by the test
suite.

**Consequences.**

- The required offline coverage target is reachable without a database.
- Business logic is exercised against the same interfaces as production, so the
  in-memory store cannot mask a logic error — only a SQL error.
- A small number of integration tests still run against real PostgreSQL to
  prove the SQL, migrations and transaction boundaries.

**Alternatives rejected.** *Mocking the repository per test with
`unittest.mock`* — couples tests to call sequences and hides interface drift.
*Skipping the database in tests entirely* — leaves the persistence layer
unverified.

---

## ADR-005 — Bounded concurrency with per-source deadlines and partial results

**Context.** Sources are independent. One slow or failing provider must not
delay or fail the whole request, and unbounded fan-out triggers provider rate
limits (`COMMON_PITFALLS.md` #2).

**Decision.** An orchestrator schedules the selected source tasks under an
`asyncio.Semaphore` bound and gathers them with `return_exceptions=True`. The
deadline is applied *inside* each task, by `AIService.fetch_source`, which owns
the per-source budget for the fetch and its retries; the orchestrator adds no
second timer, and in particular none wraps the gather, which would let one slow
source consume the others' time. Every source produces a typed `SourceOutcome`
recorded as success, empty, timeout or error. Retrieval returns a
`RetrievalResult` carrying both the successes and the failures. If no usable
sources remain, synthesis is skipped and an explicit "no sources available"
result is returned. Missing-source notes are reported to the user and are never
rendered as citations.

Cache reads and writes bracket that deadline rather than sitting inside it — the
lookup runs before `fetch_source` is called and the store after it returns — so
they are bounded where they actually happen, at the database: the PostgreSQL
pool gives every statement a `command_timeout`. A slow database therefore costs
one bounded cache operation and then a live fetch, and the cache degrades to a
miss on the timeout exactly as it does on any other storage fault.

**Consequences.** A single research request degrades gracefully and reports
which sources were unavailable and why. Partial success is a first-class outcome
and is disclosed in the CLI output and exit status. Retrieval's real ceiling is
one bounded cache operation plus one full per-source budget, not the per-source
budget alone; the README states it that way.

**Alternatives rejected.** *Plain `asyncio.gather`* — one failing source aborts
the request. *Unbounded fan-out* — invites HTTP 429s from providers.

---

## ADR-006 — No HTTP API

**Context.** The brief recommends an HTTP API in general but does not require one
for Topic 4, whose required interface is a CLI plus a scripted demo.

**Decision.** Ship the CLI and the demo only. No FastAPI application, no
endpoints, no `uvicorn`.

**Consequences.** Less surface to build, test, document and containerise. If an
HTTP interface is added later it must call the same `ResearchService` as the
CLI, and `/health` must not trigger paid AI calls.

**Alternatives rejected.** *Adding FastAPI* — unrequired scope that competes
with the required resilience, testing and containerisation work.

---

## Resolved documentation conflicts

| Conflict | Resolution |
|---|---|
| Brief requires PostgreSQL; timeline and templates mention SQLite | PostgreSQL, per ADR-002 |
| `TOPIC.md` permits a filesystem or in-memory cache; brief requires a database | PostgreSQL cache, per ADR-002 |
| Release date stated as May 11 in one place, May 13 in another | Treated as historical scheduling metadata; the authoritative due date is May 23, 2026 |
| Templates show HTTP servers, embedding and image pipelines | Out of scope for Topic 4, per ADR-006 |
| Docs claim hallucinated citation numbers are "dropped" | `ai.synthesize` drops them from `citations`, not from the answer prose. Wrapper-level validation is added in Phase 2 |
| Report template suggests a generic workspace | Adapted to the real schema and layout |

## Open questions

- The version of Python pinned in the Docker image: development and verification
  run on **3.14.7**, so the image should pin the same minor version.
- Whether to expose research history through a CLI command (the session store is
  written regardless, but retrieval of past sessions is optional).
- Retention policy for cached source results beyond TTL expiry.
