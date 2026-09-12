# Researcher — Async Research Assistant

> Ask a research question and get a concise, cited answer. Wikipedia, arXiv and a
> pluggable web-search provider are queried **concurrently**, each under its own
> deadline, and the resulting excerpts are synthesised into one answer with
> numbered references. A slow or failing source degrades the answer instead of
> breaking it.

**Team:** _[TODO: team name]_ • **Topic:** 4 — Async Research Assistant • **Course:** AI-ENG-110 Software Engineering, AI Academy

**Due:** **May 23, 2026 at 23:59 (UTC+4)**

---

## Project status

This repository is being built in the phases defined in [`docs/architecture.md`](docs/architecture.md).

| Phase | Scope | State |
|---|---|---|
| 1 — Requirements and environment | Packaging, pinned dependencies, ignore files, ADR | **done** |
| 2 — Contracts and configuration | `models.py`, `errors.py`, `config.py`, storage interfaces, validation | not started |
| 3 — Persistence | Migrations, PostgreSQL pool, cache + session repositories | not started |
| 4 — Resilient AI boundary | Logging, retry/rate limits, shared HTTP client, `ai_service.py` | not started |
| 5 — Orchestration | `orchestrator.py`, `core/researcher.py` | not started |
| 6 — Vertical slice | `bootstrap.py`, rendering, CLI wiring, demo script | not started |
| 7 — Verification | Test suites, coverage ≥60%, type check, benchmark | not started |
| 8 — Container and submission | Dockerfile, report, slides, contribution statement | not started |

The supplied AI layer (`ai/`), its smoke tests and the offline demo all run
green — see [Testing](#testing).

## Quick start

```bash
# 1. Clone & install
git clone https://github.com/your-team/your-repo
cd your-repo
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
docker build -t finalproj .
docker run --env-file .env finalproj
```

Docker is not yet set up — the `Dockerfile` and `compose.yaml` land in Phase 8.
The image will run the demo end-to-end from a single command given a populated
`.env`.

## Environment variables

Only `LLM_PROVIDER`, `LLM_MODEL` and the matching API key are required to run
against a live provider. Everything else has a working default.

| Variable | Required? | Default | What it controls |
|---|---|---|---|
| `LLM_PROVIDER` | yes | `anthropic` | `anthropic` \| `openai` \| `gemini` |
| `LLM_MODEL` | yes | `claude-sonnet-4-6` | Model id passed to the provider |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` | one of, yes | — | Credential for the chosen provider |
| `WEB_SEARCH_PROVIDER` | no | `tavily` | `tavily` \| `serper` \| `duckduckgo` |
| `TAVILY_API_KEY` / `SERPER_API_KEY` | for those providers | — | Web-search credential (DuckDuckGo needs none) |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `CACHE_TTL_SECONDS` | no | `86400` | How long a cached source result stays fresh |
| `PER_SOURCE_TIMEOUT_SECONDS` | no | `10` | Per-source deadline during retrieval |
| `MAX_SOURCES_PER_QUERY` | no | `3` | Results requested per source |
| `MAX_PARALLEL_SOURCES` | no | `3` | Semaphore bound on concurrent source tasks |
| `DATABASE_URL` | no | `postgresql://researcher:...@localhost:5432/researcher` | PostgreSQL DSN _(added in Phase 2)_ |

The full list lives in `.env.example`. **Never commit a real `.env`.**

## How to run the demo

```bash
# Supplied AI-layer demo — runs offline with no keys, no network
python demo_ai.py --offline
python demo_ai.py --offline --limit 5

# Application CLI (wired up in Phase 6)
python -m researcher ask "What is the current state of fusion energy research?"
python -m researcher ask "How does CRISPR-Cas9 work?" --sources wiki,arxiv
python -m researcher ask "..." --no-cache
python -m researcher demo
```

`--no-cache` bypasses the cache in **both** directions — no reads and no writes —
so runs are reproducible.

## Sequential vs concurrent benchmark

_[Filled in at Phase 7. `scripts/bench.py` will report retrieval-only and
end-to-end wall time for the same five questions, run sequentially and
concurrently, with per-source timings and failure counts.]_

| Workload | N | Sequential | Concurrent (sem=3) | Speedup |
|---|---|---|---|---|
| _[pending]_ | _[5]_ | _[pending]_ | _[pending]_ | _[pending]_ |

For a single question the idealised retrieval time goes from the **sum** of the
three source latencies to their **maximum**, subject to the concurrency bound.
Synthesis is a later, sequential stage, so end-to-end speedup is lower.

## Testing

```bash
# Supplied contract tests — must keep passing, run during grading
python -m pytest tests/test_ai_smoke.py -v

# Full application suite with coverage (Phase 7)
python -m pytest --cov=researcher --cov-report=term-missing
```

- Provided AI smoke tests: **16/16 passing**
- Offline demo: **5/5 questions, exit 0**
- Application coverage: _[pending — target ≥60%]_
- Every test runs offline: the `ai` module and the HTTP layer are mocked
  (`respx` for `httpx`). The suite must pass with the network cable pulled.

## Project layout

```
.
├── ai/                    # PROVIDED — do not modify
├── researcher/            # our application package
│   ├── __init__.py
│   ├── __main__.py        # `python -m researcher`
│   └── cli.py             # argument surface + exit statuses
├── tests/                 # provided smoke tests + our suite
├── data/                  # 5 sample research questions
├── docs/
│   └── architecture.md    # ADRs, module contracts, phase roadmap
├── demo_ai.py             # PROVIDED — AI-layer demo
├── pyproject.toml         # packaging + ruff/mypy config
├── requirements.txt       # pinned runtime
├── requirements-dev.txt   # pinned test + quality tooling
├── .env.example
├── .gitignore
└── README.md
```

Later phases add `scripts/demo.py`, `scripts/bench.py`, `migrations/`,
`artefacts/`, `report/`, `slides/`, `Dockerfile` and `compose.yaml`.

## Architecture in one diagram

_[Embedded at Phase 6, matching the diagram in `report/report.pdf`. The full
control contract and module responsibilities are in
[`docs/architecture.md`](docs/architecture.md).]_

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

_[Consolidated at Phase 7. Known so far:]_

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

## Tools & acknowledgements

AI assistants were used during development. The per-module disclosure is
maintained in `templates/CONTRIBUTION_STATEMENT.md` and reproduced in the
report.

## License

This is academic coursework, not a published library.
