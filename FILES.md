# File guide

Use this guide to find the part of the project you want to read.

## Root files

| File | What it does |
| --- | --- |
| [.dockerignore](.dockerignore) | Files and folders excluded from the Docker build context. |
| [.env.example](.env.example) | Documented provider, database, logging, cache, and timeout settings to copy into a local .env. |
| [.gitignore](.gitignore) | Files and folders that Git should leave out of commits. |
| [CONTRIBUTION_STATEMENT.md](CONTRIBUTION_STATEMENT.md) | Team contribution records, review links, AI tool disclosures, and signature fields. |
| [Dockerfile](Dockerfile) | Builds the Python app image and includes the migrations and smoke tests. |
| [FILES.md](FILES.md) | A short guide to the files in this repository. |
| [README.md](README.md) | Project overview and instructions for readers. |
| [TOPIC.md](TOPIC.md) | The course project brief, required features, and rules for the supplied AI code. |
| [compose.yaml](compose.yaml) | Runs the app and a PostgreSQL database together for the demo. |
| [demo_ai.py](demo_ai.py) | Runs the supplied source-fetching demo with live providers or local fake providers in offline mode. |
| [pyproject.toml](pyproject.toml) | Package metadata, dependency ranges, CLI entry point, and lint and type-check settings. |
| [pytest.ini](pytest.ini) | Pytest settings for async tests and imports from the repository root. |
| [requirements-ai.txt](requirements-ai.txt) | Dependency list shipped with the supplied AI package. |
| [requirements-dev.txt](requirements-dev.txt) | Runtime dependencies plus test, lint, type-check, and audit tools. |
| [requirements.txt](requirements.txt) | Pinned runtime packages used by the project. |

## .github

| File | What it does |
| --- | --- |
| [pull_request_template.md](.github/pull_request_template.md) | Review checklist and fields for pull request descriptions. |

## ai

| File | What it does |
| --- | --- |
| [__init__.py](ai/__init__.py) | Exports the supplied AI package functions used for fetching sources and building cited answers. |
| [schemas.py](ai/schemas.py) | Defines source, citation, and cited-answer records used by the supplied AI package. |
| [sources.py](ai/sources.py) | Provides the supplied Wikipedia, arXiv, and web-search fetchers and web-search adapters. |
| [synthesizer.py](ai/synthesizer.py) | Asks a model to answer from source excerpts and matches numbered citations to their sources. |

## ai/providers

| File | What it does |
| --- | --- |
| [__init__.py](ai/providers/__init__.py) | Exports the base provider types and provider factories from the supplied AI package. |
| [anthropic.py](ai/providers/anthropic.py) | Supplied Claude adapters for text replies and image descriptions. |
| [base.py](ai/providers/base.py) | Defines shared provider interfaces for text, images, and embeddings, plus the provider error type. |
| [factory.py](ai/providers/factory.py) | Chooses supplied AI providers based on environment variables. |
| [google.py](ai/providers/google.py) | Supplied Gemini adapters for text replies, image descriptions, and embeddings. |
| [openai.py](ai/providers/openai.py) | Supplied OpenAI adapters for text replies, image descriptions, and embeddings. |

## artefacts

| File | What it does |
| --- | --- |
| [bench-report.txt](artefacts/bench-report.txt) | Saved benchmark timing tables and source results. |
| [bench.json](artefacts/bench.json) | Saved machine-readable timing results from the live benchmark. |
| [pip-audit.txt](artefacts/pip-audit.txt) | Saved dependency-audit output from the project environment. |
| [reproduction-codespaces-bfec78.txt](artefacts/reproduction-codespaces-bfec78.txt) | A teammate's saved clean-clone build and test results from Codespaces. |
| [sample-run.md](artefacts/sample-run.md) | Saved commands and output from a live CLI run with citations and session storage. |

## data

| File | What it does |
| --- | --- |
| [research_questions.json](data/research_questions.json) | Five sample research questions with difficulty labels and suggested sources. |

## docs

| File | What it does |
| --- | --- |
| [architecture.md](docs/architecture.md) | Explains the app structure and the main design choices. |
| [peer-review.md](docs/peer-review.md) | Links the team reviews and records what was checked and changed. |
| [security.md](docs/security.md) | Records security checks, limits, and known gaps in the project. |

## migrations

| File | What it does |
| --- | --- |
| [001_initial_schema.sql](migrations/001_initial_schema.sql) | Creates the source-cache and research-session tables and their indexes. |

## report

| File | What it does |
| --- | --- |
| [report.pdf](report/report.pdf) | Compiled project report for readers who do not use LaTeX. |
| [report.tex](report/report.tex) | LaTeX source for the written project report. |

## researcher

| File | What it does |
| --- | --- |
| [__init__.py](researcher/__init__.py) | Sets the application version. Importing the package does not open connections or read settings. |
| [__main__.py](researcher/__main__.py) | Runs the CLI when called with python -m researcher. |
| [bootstrap.py](researcher/bootstrap.py) | Builds the application services and shared connections, sets up logging, and closes resources when a run ends. |
| [cli.py](researcher/cli.py) | Parses ask, demo, and purge commands, prints results, and chooses the process exit status. |
| [config.py](researcher/config.py) | Reads and checks environment settings, resolves provider keys and model names, and sets timeouts and result limits. |
| [errors.py](researcher/errors.py) | Defines the error types used by the app, with short codes and flags that control retries. |
| [models.py](researcher/models.py) | Defines the request, source result, cache, timing, and saved-session models and checks their fields. |
| [rendering.py](researcher/rendering.py) | Formats answers, numbered references, source status, warnings, and timing for terminal output. |
| [validation.py](researcher/validation.py) | Checks questions and source names, builds cache keys, removes duplicate URLs, and checks citation references. |

## researcher/core

| File | What it does |
| --- | --- |
| [__init__.py](researcher/core/__init__.py) | Marks the package containing the main research workflow. |
| [researcher.py](researcher/core/researcher.py) | Runs one question through validation, retrieval, answer generation, and optional session storage. |

## researcher/services

| File | What it does |
| --- | --- |
| [__init__.py](researcher/services/__init__.py) | Marks the package containing retrieval, caching, and provider services. |
| [ai_service.py](researcher/services/ai_service.py) | Wraps the supplied AI calls with checked settings, deadlines, retries, and error handling. |
| [cache.py](researcher/services/cache.py) | Reads and writes cached sources, reports cache failures, and removes expired entries. |
| [http_client.py](researcher/services/http_client.py) | Creates the shared async HTTP client with timeouts, connection limits, and a User-Agent header. |
| [orchestrator.py](researcher/services/orchestrator.py) | Fetches the selected sources under a concurrency limit and combines results even when a source fails. |
| [resilience.py](researcher/services/resilience.py) | Defines retry delays, respects provider retry hints, and limits how long an operation may run. |
| [wikipedia.py](researcher/services/wikipedia.py) | Searches Wikipedia using full-text search and turns article summaries into source records. |

## researcher/storage

| File | What it does |
| --- | --- |
| [__init__.py](researcher/storage/__init__.py) | Marks the package containing storage interfaces and implementations. |
| [_driver.py](researcher/storage/_driver.py) | Groups database and stored-data errors and converts them into app storage errors. |
| [cache_store.py](researcher/storage/cache_store.py) | Stores source results in PostgreSQL and returns entries whose cache keys match and have not expired. |
| [interfaces.py](researcher/storage/interfaces.py) | Defines the async methods that cache and session stores must provide. |
| [memory.py](researcher/storage/memory.py) | Provides in-memory cache and session stores for tests and runs that do not need PostgreSQL. |
| [postgres.py](researcher/storage/postgres.py) | Opens the PostgreSQL pool, applies SQL migrations, checks migration hashes, and closes the pool. |
| [session_repository.py](researcher/storage/session_repository.py) | Saves research sessions as JSON and reads individual or recent sessions from PostgreSQL. |

## scripts

| File | What it does |
| --- | --- |
| [bench.py](scripts/bench.py) | Compares sequential and concurrent retrieval with caching disabled and writes timing tables and JSON results. |

## slides

| File | What it does |
| --- | --- |
| [DEFENSE_RUNBOOK_AZ.md](slides/DEFENSE_RUNBOOK_AZ.md) | Azerbaijani speaker notes and explanations for each presentation slide. |
| [slides.pdf](slides/slides.pdf) | Compiled Azerbaijani presentation slides. |
| [slides.tex](slides/slides.tex) | LaTeX Beamer source for the Azerbaijani presentation. |

## tests

| File | What it does |
| --- | --- |
| [conftest.py](tests/conftest.py) | Shared test fixtures, fake providers, and a guard that blocks internet access during tests. |
| [test_ai_service.py](tests/test_ai_service.py) | Checks retrieval and answer generation, including empty results, failures, timeouts, and cleanup. |
| [test_ai_service_wiring.py](tests/test_ai_service_wiring.py) | Checks that provider keys, models, and timeouts reach the right adapters. |
| [test_ai_smoke.py](tests/test_ai_smoke.py) | Supplied checks for AI schemas, source parsing, web fetching, and cited answers. |
| [test_ai_translation.py](tests/test_ai_translation.py) | Checks how provider errors and retry hints become app errors without exposing raw payloads. |
| [test_bench.py](tests/test_bench.py) | Checks benchmark requests, timing calculations, failure counts, and text and JSON reports. |
| [test_bootstrap.py](tests/test_bootstrap.py) | Checks service setup, optional storage, and connection cleanup on success and failure. |
| [test_cache_service.py](tests/test_cache_service.py) | Checks cache reads, writes, expiry, missing storage, and reported cache failures. |
| [test_cli.py](tests/test_cli.py) | Checks command parsing, research requests, demo and purge commands, output streams, and exit codes. |
| [test_logging_config.py](tests/test_logging_config.py) | Checks log levels, stderr output, and separation from the supplied package logger. |
| [test_migrations.py](tests/test_migrations.py) | Checks migration discovery and the rule that applied migration files must not change. |
| [test_offline_guard.py](tests/test_offline_guard.py) | Checks that tests may use local addresses but cannot reach the internet. |
| [test_orchestrator.py](tests/test_orchestrator.py) | Checks parallel retrieval limits, result order, duplicate removal, caching, and source failures. |
| [test_rendering.py](tests/test_rendering.py) | Checks answer and diagnostic output, reference alignment, summaries, and hidden source snippets. |
| [test_research_service.py](tests/test_research_service.py) | Checks the full research workflow with fake providers and in-memory storage. |
| [test_resilience.py](tests/test_resilience.py) | Checks retries, backoff, provider wait hints, cancellation, and deadlines. |
| [test_retry_policy.py](tests/test_retry_policy.py) | Checks retry settings, delay limits, and which errors should be retried. |
| [test_storage.py](tests/test_storage.py) | Checks the same cache and session rules against in-memory and PostgreSQL stores. |
| [test_wikipedia.py](tests/test_wikipedia.py) | Checks Wikipedia requests and summary parsing against mocked HTTP responses. |
