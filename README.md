# Researcher — Async Research Assistant

> Ask a research question and get a concise, cited answer. Wikipedia, arXiv and a
> pluggable web-search provider are queried **concurrently**, each under its own
> deadline, and the resulting excerpts are synthesised into one answer with
> numbered references. A slow or failing source degrades the answer instead of
> breaking it.

**Topic:** 4 — Async Research Assistant • **Course:** AI-ENG-110 Software Engineering, AI Academy

**Due:** **May 23, 2026 at 23:59 (UTC+4)**

---

## Project status

This repository is being built in the phases defined in [`docs/architecture.md`](docs/architecture.md).

| Phase | Scope | State |
|---|---|---|
| 1 — Requirements and environment | Packaging, pinned dependencies, ignore files, ADR | **done** |
| 2 — Contracts and configuration | `models.py`, `errors.py`, `config.py`, storage interfaces, validation | **done** |
| 3 — Persistence | Migrations, PostgreSQL pool, cache + session repositories | **done** |
| 4 — Resilient AI boundary | Logging, retry/rate limits, shared HTTP client, `ai_service.py` | **done** |
| 5 — Orchestration | `orchestrator.py`, `cache.py`, `core/researcher.py` | **done** |
| 6 — Vertical slice | `bootstrap.py`, rendering, CLI wiring, demo script | **done** |
| 7 — Verification | Test suites, coverage ≥60%, type check, benchmark | **done** |
| 8 — Container and submission | Dockerfile, report, slides, contribution statement | **done** |

The supplied AI layer (`ai/`), its smoke tests and the offline demo all run
green — see [Testing](#testing).

## Quick start

```bash
# 1. Clone & install
git clone https://github.com/ii1ahe/research-assistant
cd research-assistant
python -m venv .venv && source .venv/bin/activate

# 2. Install the pinned environment
pip install -r requirements-dev.txt    # includes requirements.txt

# 3. Configure
cp .env.example .env                   # then fill in real API keys
# (DO NOT commit .env — it is in .gitignore)

# 4. Run the supplied smoke tests
python -m pytest tests/test_ai_smoke.py -v

# 5. Try the offline demo (no API keys, no network)
python demo_ai.py --offline
```

> **Running pytest.** Use `python -m pytest`, not the bare `pytest` console
> script. `python -m` puts the project root on `sys.path`, so the top-level
> `ai` and `researcher` packages import correctly. `pytest.ini` also sets
> `pythonpath = .` so either invocation works.

## Run with Docker

```bash
# Build, and prove the image without spending provider quota
docker build -t finalproj .
docker run --rm finalproj pytest tests/test_ai_smoke.py

# The demo, against a database that did not exist a minute ago
docker compose run --rm app

# Just the database, for running the SQL tests from the host
docker compose up -d postgres
```

Verified 2026-09-16 on this machine: the image builds on `python:3.14.7-slim`,
the 16 supplied smoke tests and the full 327-test suite pass inside the
container, and the demo answers end to end with its sessions written to the
compose PostgreSQL — `saved session 5b595539-…` in the run log, from a table the
container itself created.

The runtime image carries `pytest` and `tests/`, which reverses what
`requirements-dev.txt` originally said. That was deliberate: the
`Dockerfile.template` verifies an image by running `pytest
tests/test_ai_smoke.py` inside it, and honouring that command costs no
credentials and no network, so the image can be checked on a machine with no
keys. The static-analysis tools (`ruff`, `mypy`) still stay out.

**Two things about the container are not obvious, and both were found by running
it rather than by reading it.**

**`DATABASE_URL` cannot be `localhost` inside the image.** `localhost` in a
container is the container. `docker run --env-file .env finalproj` therefore
fails at startup against the `.env` written for host development — correctly,
and naming the variable, but not usefully. Either point it somewhere reachable:

```bash
docker run --rm --env-file .env --network host finalproj    # Linux, host database
docker run --rm --env-file .env -e DATABASE_URL= finalproj  # no database at all
```

or let `compose.yaml` set it, which is what the one-command path does.

**`migrations/` has to be in the image.** `researcher/storage/postgres.py`
derives the repository root from its own `__file__` and applies the schema at
startup, so the SQL is as much part of the deployment as the code. `.dockerignore`
excluded it until Phase 8. The omission was invisible because the host applies
migrations too — the image built fine and would have died on its first run
against a database, with a fatal `ConfigurationError` rather than a build error.

Compose is also where the suite's offline guarantee needed widening. The guard
in `tests/conftest.py` permitted loopback only, and under Compose the database
is a sibling container at `172.18.0.2` — a private address a machine reaches
with the cable pulled, which is the guard's own stated rule. It now tests the
address rather than matching a list of three strings, and every public address
is still refused.

## Environment variables

Only `LLM_PROVIDER`, `LLM_MODEL` and the matching API key are required to run
against a live provider. Everything else has a working default.

| Variable | Required? | Default | What it controls |
|---|---|---|---|
| `LLM_PROVIDER` | yes | `anthropic` | `anthropic` \| `openai` \| `gemini` (`google` accepted) |
| `LLM_MODEL` | no | provider-specific | Model id; defaults per provider, and must suit `LLM_PROVIDER` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` | one of, yes | — | Credential for the chosen provider |
| `WEB_SEARCH_PROVIDER` | no | `tavily` | `tavily` \| `serper` \| `duckduckgo` |
| `TAVILY_API_KEY` / `SERPER_API_KEY` | for those providers | — | Web-search credential (DuckDuckGo needs none) |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `CACHE_TTL_SECONDS` | no | `86400` | How long a cached source result stays fresh |
| `PER_SOURCE_TIMEOUT_SECONDS` | no | `10` | Per-source deadline during retrieval |
| `SYNTHESIS_TIMEOUT_SECONDS` | no | `30` | Deadline for one synthesis, retries included |
| `MAX_RESULTS_PER_SOURCE` | no | `3` | Results requested from each source |
| `MAX_PARALLEL_SOURCES` | no | `3` | Semaphore bound on concurrent source tasks |
| `MAX_QUESTION_LENGTH` | no | `500` | Longest accepted question, in characters |
| `WIKIPEDIA_SEARCH` | no | `fulltext` | `fulltext` \| `opensearch` — see below |
| `DATABASE_URL` | no | — (unset) | PostgreSQL DSN for the cache and session store |
| `PERSIST_SESSIONS` | no | `true` | Set `false` to run without touching the database |

The full list lives in `.env.example`. **Never commit a real `.env`.**

Leave a value blank to mean "not set" — do not put a comment after a blank
value: `KEY=   # note` is parsed as `KEY` containing the text of the note,
which the application then treats as a real credential. Comments belong on
their own line.

`LLM_MODEL` is a single variable shared by every provider, so it has to change
whenever `LLM_PROVIDER` does. Pairing `LLM_PROVIDER=openai` with a `claude-…`
model id is rejected at startup with exit status 2 instead of failing later
with an opaque provider error.

### Why `WIKIPEDIA_SEARCH` exists

The supplied `ai.sources.fetch_wikipedia` searches with the MediaWiki
`opensearch` API, which prefix-matches the *entire* query against article
titles. Measured against the live API:

| Query | Titles returned |
|---|---|
| `What is photosynthesis and what are its main stages?` | 0 |
| `photosynthesis` | 3 |
| `photosynthesis main stages` | 0 |
| `What is photosynthesis` | 0 |

Only a bare word that happens to begin a title matches. Rewriting the question
does not help, because `opensearch` matches the whole string — every multi-word
query above is a reasonable search and every one returns nothing. Since all five
supplied demo questions are natural-language, the supplied fetcher contributes
nothing to any of them.

`ai/` is immutable, so `researcher/services/wikipedia.py` replaces the **search
step** and nothing else: titles come from the MediaWiki full-text search
(`list=search`), which handles the question as written, and everything after
that is the supplied behaviour — the same summary endpoint, the same `Source`
shape. Setting `WIKIPEDIA_SEARCH=opensearch` restores the supplied fetcher
exactly. The query itself is never rewritten either way; the difference is which
fetcher receives it.

**If you select `gemini`**, set `LLM_MODEL` explicitly. The supplied
`ai/providers/google.py` falls back to `gemini-2.0-flash`, which the API now
rejects with `404 NOT_FOUND` — the model has been retired, and `gemini-2.5-flash`
is refused as well. `researcher/config.py` therefore ships a working gemini
default (`gemini-3.8-flash`) rather than mirroring `ai/`'s retired literal; that
deliberate divergence is documented at `_DEFAULT_LLM_MODELS`. This project runs
on `gemini` with `gemini-3.8-flash`.

`DATABASE_URL` controls both storage concerns (ADR-002): the source cache and
the session store both live in PostgreSQL. With it unset, nothing is cached and
sessions are not stored, but the tool still runs end-to-end and reports
persistence as `skipped` rather than as a failure.

## How to run the demo

```bash
# Supplied AI-layer demo — runs offline with no keys, no network
python demo_ai.py --offline
python demo_ai.py --offline --limit 5

# Application CLI
python -m researcher ask "What is the current state of fusion energy research?"
python -m researcher ask "How does CRISPR-Cas9 work?" --sources wiki,arxiv
python -m researcher ask "..." --no-cache
python -m researcher demo
```

`--no-cache` bypasses the cache in **both** directions — no reads and no writes —
so runs are reproducible.

`demo` synthesises five answers, and the free tier allows twenty per day per
model, so three runs exhaust a model's allowance. Every source is fetched live
and cached, but the LLM call is never cached, which is why re-running `demo` on
the same day costs another five. `LLM_MODEL` selects the bucket — the allowance
is per model, so a spent one can be worked around rather than waited out.

The answer is written to stdout and everything about the run — the per-source
table, the warnings, the timings — to stderr, so a redirect captures the answer
and nothing else:

```bash
python -m researcher ask "What is photosynthesis?" > answer.md
```

Exit statuses are part of the interface, because the intended caller is a script:
**0** for an answer, including a partial one whose missing sources were
disclosed; **1** when no usable answer was produced, or a session configured for
storage was not stored; **2** for bad input or bad configuration — the cases
where retrying unchanged would spend quota to learn nothing.

## Sequential vs concurrent benchmark

```bash
python scripts/bench.py                       # full report, both modes
python scripts/bench.py --markdown            # just the table below
python scripts/bench.py --sources declared    # realistic, not controlled
python scripts/bench.py --no-warmup --pause 0 # polite only on a paid quota
```

The five supplied questions, asked of all three sources, with `max_parallel_sources`
the only setting that differs between the two modes. Caching is off in both, or
the second mode would be answered from what the first one wrote and the script
would be timing PostgreSQL. Measured 2026-09-12; raw data in
`artefacts/bench.json`, full output in `artefacts/bench-report.txt`.

| Workload | N | Sequential (bound=1) | Concurrent (bound=3) | Speedup |
|---|---|---|---|---|
| Retrieval only | 5 questions x 3 sources | 4.8 s | 2.9 s | **1.67x** |
| Per question, retrieval | mean | 0.97 s | 0.58 s | **1.67x** |
| End-to-end | 5 questions | 43.4 s | 43.3 s | **1.00x** |

Per-source mean latency: wikipedia 0.55 s, arxiv 0.30 s, web 0.15 s. No source
failed in either sweep.

**The retrieval speedup is bounded by `sum/max`, not by the source count.** The
ideal is that one question costs the sum of three latencies sequentially and
their maximum concurrently, which for these three sources is `0.996 / 0.546 =
1.83x` — not 3x, because Wikipedia is three times slower than the web search and
the slowest source sets the floor. The measured 1.67x is 92% of what that
latency profile permits; the remainder is scheduling and connection overhead.

**End-to-end the win disappears, and that is the honest headline.** Synthesis
takes about 8 s per question and is sequential in both modes by construction, so
it buries a retrieval stage that costs half a second. The benchmark prints
synthesis as a *control* row for exactly this reason: at 0.96x it confirms the
two sweeps are comparable, which is what makes the 1.00x end-to-end row
believable rather than a measurement artefact. Concurrency in retrieval is
correct and it is real, but on this workload it is not what a user waits for —
shortening the answer would mean attacking synthesis, not the fan-out.

### What the benchmark found

Two defects, both of which the run above is the first to survive. They are
recorded because finding them was the point of the phase.

**Synthesis was governed by the retrieval deadline.** `AIService` ran synthesis
under `per_source_timeout_seconds`, so ten seconds bounded both a search fetch
and an LLM completion. Against the configured model, synthesis took 2.9 s, 3.9 s,
6.1 s and 7.6 s on a quiet machine and then hit the ceiling — roughly one call in
six refused, at a point where the retry policy could not help, because the
deadline wraps the whole call rather than each attempt. `SYNTHESIS_TIMEOUT_SECONDS`
now exists and defaults to 30 s.

**A refused synthesis was reported as a speedup.** When synthesis fails it
returns in about a second instead of eight, so a sweep compared real work against
fast failures and printed **4.65x** for end-to-end. The script now withholds the
synthesis and end-to-end rows unless every question in both sweeps reached an
answer, and exits 1. The retrieval row is unaffected — it is measured before
synthesis runs and stands on its own — which is why the table above survives a
run that could not fill in its last row.

## Testing

```bash
# Supplied contract tests — must keep passing, run during grading
python -m pytest tests/test_ai_smoke.py -v

# Full application suite with coverage
python -m pytest --cov=researcher --cov-report=term-missing
```

- Provided AI smoke tests: **16/16 passing**
- Offline demo: **5/5 questions, exit 0**
- Application suite: **327 tests passing, coverage 96%** (target ≥60%). The
  figure is measured over `researcher/` only, and every module in it is covered;
  the thinnest is `storage/session_repository.py` at 82%, where the uncovered
  lines are `asyncpg` error branches that need a database to fail in a way the
  doubles cannot reproduce.
- Every test runs offline: the `ai` module and the HTTP layer are mocked
  (`respx` for `httpx`). The suite must pass with the network cable pulled.
- Offline is **enforced, not merely intended**: an autouse fixture in
  `conftest.py` refuses any connection off this machine, and permits loopback so
  the PostgreSQL integration tests still run. `tests/test_offline_guard.py`
  tests the guard itself.

## Project layout

```
.
├── ai/                    # PROVIDED — do not modify
├── researcher/            # our application package
│   ├── __init__.py
│   ├── __main__.py        # `python -m researcher`
│   ├── bootstrap.py       # the one module that knows the whole graph
│   ├── cli.py             # argument surface + exit statuses
│   ├── config.py          # validated settings, provider resolution
│   ├── core/
│   │   └── researcher.py  # the use case: retrieve, synthesise, persist
│   ├── errors.py          # failure categories + retryability
│   ├── models.py          # typed data contracts
│   ├── rendering.py       # answer, diagnostics and batch summary
│   ├── services/          # orchestrator, ai_service, cache, resilience
│   ├── storage/           # interfaces (ADR-004) + PostgreSQL and in-memory
│   └── validation.py      # input normalisation + output checks
├── tests/                 # provided smoke tests + our suite
├── data/                  # 5 sample research questions
├── scripts/
│   └── bench.py           # the sequential-vs-concurrent benchmark
├── migrations/
│   └── 001_initial_schema.sql
├── artefacts/             # benchmark output, as submitted
├── docs/
│   ├── architecture.md    # ADRs, module contracts, phase roadmap
│   └── security.md        # security posture, gaps, hardening notes
├── demo_ai.py             # PROVIDED — AI-layer demo
├── Dockerfile             # multi-stage; the image the demo runs from
├── compose.yaml           # the application plus its PostgreSQL
├── report/                # report.tex + compiled report.pdf
├── slides/                # slides.tex + compiled slides.pdf
├── CONTRIBUTION_STATEMENT.md
├── pyproject.toml         # packaging + ruff/mypy config
├── requirements.txt       # pinned runtime
├── requirements-dev.txt   # pinned test + quality tooling
├── .env.example
├── .gitignore
└── README.md
```

All eight phases are merged. The report is 13 pages, the deck is 11 frames,
and every number in both is traceable to `artefacts/bench.json`, a test run,
or the container.

## Architecture in one diagram

The report reproduces this diagram. The full control contract, the ADRs behind
each arrow and the module responsibilities are in
[`docs/architecture.md`](docs/architecture.md).

```
        CLI (`python -m researcher ask`)
                    |
                    v
        core/researcher.ResearchService      <- the use case
           |            |             |
           v            v             v
   orchestrator     ai_service    session repository
   (bounded tasks,  (retries,     (PostgreSQL)
    deadlines)       logging)
           |            |
           v            v
      cache service   ai/  (PROVIDED, unchanged)
           |
           v
      PostgreSQL
```

## Limitations

Consolidated at Phase 7, from what building and measuring the system actually
turned up. The first two bullets are what the benchmark showed; the rest are
properties of the design or of the supplied code. The two *defects* the
benchmark found are not listed here because they are fixed — they are written up
under [What the benchmark found](#what-the-benchmark-found).

None of these is hidden from the caller: anything that can affect a run is
reported in that run's diagnostics, and a degraded answer the user was told
about still exits 0.

- The concurrency win does not reach the user. Retrieval runs 1.67x faster
  concurrently, but synthesis costs about 8 s per question against retrieval's
  0.5 s, so end-to-end the measured figure is 1.00x — the fan-out is correct,
  and on this workload it is not what anyone waits for. Retrieval also cannot
  scale with the source count: a question costs the *slowest* source rather than
  the sum, and with Wikipedia three times slower than the web search that
  ceiling is 1.83x, not 3x.
- Concurrency stops at the question boundary. `MAX_PARALLEL_SOURCES` bounds the
  sources inside one question; `ask` takes a single question and `demo` asks its
  set one at a time, so a batch of N questions costs the sum of N question
  times. That is deliberate — Wikipedia and arXiv are key-free and rate-limited,
  and the brief asks callers to be polite to them — but it is a ceiling on batch
  throughput rather than an accident.
- **A throttle is now waited out, but only after it happens.** Measured
  2026-09-16: when the free tier refused a synthesis it said so precisely —
  `429 RESOURCE_EXHAUSTED`, `To monitor your current usage… Please retry in
  21.9s` — while the policy waited a jittered ≤0.5 s between three attempts,
  so all three landed inside the same throttling window and the call failed in
  under a second. Fixed 2026-09-17: the provider's named delay is parsed at the
  classification boundary — the last place the payload exists, since
  `_translate` deliberately discards it so provider text cannot reach a log or
  a user — and carried on `UpstreamRateLimitError`, so the policy waits the
  larger of its own jittered backoff and the provider's instruction. What
  remains is anticipation: the first call still discovers the quota by being
  refused, because the application keeps no per-provider token bucket of its
  own.
- **The free tier is 20 syntheses per day, per model, and a demo costs 5.** The
  allowance is per model, so a spent day can be worked around by pointing
  `LLM_MODEL` at a model whose bucket is untouched — which is how the Phase 7
  benchmark ran. A grader running the demo three times will exhaust one model.
  This is a property of the free tier, not of the code, but it decides how many
  times the end-to-end path can be demonstrated in a day.
- `ai.synthesize()` is synchronous and calls a blocking SDK. It is moved to a
  worker thread so it cannot stall the event loop, but awaiting a timeout does
  not forcibly terminate the in-flight SDK call. Hard cancellation is not
  achievable through the supplied interface.
- `ai.ProviderError` is coarse — it covers missing credentials, missing
  packages, network faults and provider errors alike. Classification relies on
  configuration checks and exception causes.
- Citation structure proves only that the answer's `[N]` markers map to real
  sources. It does **not** prove that any claim is factually supported.
- The offline demo returns canned sources and templated answers. Its success
  establishes wiring, not retrieval quality.
- The supplied `ai/providers/google.py` hardcodes a **retired** fallback model
  (`gemini-2.0-flash`, now `404`). We cannot correct it in place — `ai/` is
  supplied code — so the working default lives in our config instead. Any
  caller that reaches `GeminiLLM()` with `LLM_MODEL` unset still gets the
  broken id, which is a live hazard for anything bypassing `effective_llm_model`.
- Gemini's reasoning tokens count against the output budget, so a small
  `max_tokens` produces an **empty or truncated** response rather than an
  error. Measured: `max_tokens=64` returned `""` from `gemini-3.7-flash` and a
  truncated `{"answer":"` from `gemini-3.8-flash`; `1024` returned valid JSON
  from both. The synthesizer is safe because it relies on the provider default,
  but a retry wrapper that passes `max_tokens` explicitly could reintroduce
  this silently — the failure looks like a bad model response, not a config
  error.

Security sits in this section rather than its own: the report template has no
security section, and the honest content there is a list of gaps and what
closing each one would take. That list, plus the checks that came back clean, is
in [`docs/security.md`](docs/security.md) — the headline being that the local
PostgreSQL cluster still uses `initdb`'s default `trust` authentication.

## Tools & acknowledgements

AI assistants were used during development. The per-module disclosure is in
[`CONTRIBUTION_STATEMENT.md`](CONTRIBUTION_STATEMENT.md) and summarised in §10
of the report. In short: Claude (Anthropic) for scaffolding, test construction
and documentation drafts, with every claim verified by running the tests, the
container and the benchmark. Copilot was not used.

## License

This is academic coursework, not a published library.
