<!--
  Members are listed by GitHub handle. The separate one-page PDF must be
  signed by all three members; printed full names belong on that form.
  The final tag must point at the reviewed submission commit on main.
-->

# Contribution Statement

**Team:** Async Research Assistant Team
**Topic:** Topic 4 — Async Research Assistant
**Repository:** [https://github.com/ii1ahe/research-assistant](https://github.com/ii1ahe/research-assistant)
**Final tag:** `v1.0-final`
**Submission date:** 2026-09-18

---

## Submission type: three members

Git history attributes the application commits to `ii1ahe` and one
clean-clone reproduction commit to `fatimekazimli`. `Elmin995`'s contribution is peer review, which
produces no commits and so cannot appear in `shortlog`; it is recorded on
the repository as approvals and inline comments on PRs #11 and #12. The
approximate work shares below include review and are not commit percentages:
the application is one person's code, and the other two members'
parts — the clean-clone reproduction and the peer review — are exactly what
their briefs assigned them.

Where a section says "I", it is that member speaking. The report and the
README say "we", the ordinary editorial convention for a team document.

---

## @ii1ahe — the application, ≈80 %

**Owned (sole author of these files / PRs):**

Application — `researcher/`, 25 source files, 4 681 lines:

- `researcher/models.py` (371), `researcher/validation.py` (358),
  `researcher/config.py` (316), `researcher/errors.py` (175) — the typed
  contracts, the input and output validators, and the settings object whose
  `persistence_enabled` flag is derived rather than stored.
- `researcher/services/ai_service.py` (525), `resilience.py` (233),
  `http_client.py` (48) — the only boundary that calls `ai/`, the retry,
  backoff and timeout policy around it, and the throttling translation that
  honours retry delays named in provider exception text (not HTTP headers).
- `researcher/services/orchestrator.py` (240), `cache.py` (199),
  `wikipedia.py` (190) — bounded concurrent retrieval with per-source
  deadlines and partial results; cache-aside reads and writes; a replacement
  for the supplied Wikipedia *search* step only.
- `researcher/storage/interfaces.py` (146), `postgres.py` (264),
  `memory.py` (125), `cache_store.py` (150), `session_repository.py` (125),
  `_driver.py` (59) — the two storage Protocols and both implementations
  behind them.
- `researcher/core/researcher.py` (256) — the use case.
- `researcher/bootstrap.py` (277), `cli.py` (362), `rendering.py` (196) — the
  composition root, argument parsing, exit statuses and terminal output.

Verification — 19 test files, 6 375 lines:

- `tests/` — **357 tests, 96 % coverage** over `researcher/`.
- `tests/conftest.py` — including the autouse `no_internet` fixture that makes
  the suite's offline guarantee enforced rather than intended.
- `tests/test_offline_guard.py` — tests the guard itself.

Infrastructure and documentation:

- `migrations/001_initial_schema.sql`, `Dockerfile`, `compose.yaml`,
  `.dockerignore`
- `scripts/bench.py`; `artefacts/bench.json`, `artefacts/bench-report.txt`,
  `artefacts/pip-audit.txt`
- `pyproject.toml`, `pytest.ini`, `requirements*.txt`, `.env.example`,
  `.gitignore`
- `README.md`; `docs/architecture.md` (six ADRs); `docs/security.md`
- `report/report.tex` and the compiled `report/report.pdf` (10 pages, pdfLaTeX)
- `slides/slides.tex` and the compiled `slides/slides.pdf` (11 frames, beamer)

**PRs:** [#1](https://github.com/ii1ahe/research-assistant/pull/1),
[#2](https://github.com/ii1ahe/research-assistant/pull/2),
[#3](https://github.com/ii1ahe/research-assistant/pull/3),
[#4](https://github.com/ii1ahe/research-assistant/pull/4),
[#5](https://github.com/ii1ahe/research-assistant/pull/5),
[#6](https://github.com/ii1ahe/research-assistant/pull/6),
[#7](https://github.com/ii1ahe/research-assistant/pull/7),
[#8](https://github.com/ii1ahe/research-assistant/pull/8), the Phase 8
submission PR (#9), the purge-and-scan PR (#10), the Retry-After PR (#11),
and the final polish PR that carries this statement. One branch per piece,
each cut from an updated `main`.

**Reviewed:** by `Elmin995` — PR #11 and PR #12, each approved with a
written review and an inline comment, both on the repository. Code-level
gating is mechanical and is described in §6 of the report: 357 tests,
`mypy` in strict mode over `researcher/` and `scripts/`, `ruff` with the
`T20` rule enabled for the modules that must not print, and a live benchmark
that drives the real APIs and a real PostgreSQL. The two defects the README
records under "What the benchmark found" — the synthesis deadline and the
refused-synthesis speedup — were both found by *running* the system rather
than by reading it, which is the gap a second reviewer would otherwise have
covered.

**Approximate share:** ≈80 %

---

## @fatimekazimli — the reproduction, ≈10 %

**Owned (sole author):**

- `artefacts/reproduction-codespaces-bfec78.txt` — the clean-clone
  reproduction on a second machine that the blueprint names as the exit
  criterion (Brief B). Run on a fresh GitHub Codespace: built the image,
  ran the 16 supplied smoke tests inside it, ran the full suite against a
  PostgreSQL the compose file created itself, and diagnosed the one thing
  that differed — the seven PostgreSQL tests skipped because the Codespace's
  Docker-in-Docker bridge drops container-to-container traffic. Her second
  finding, that an unreachable database cost sixty seconds per attempt
  because `PostgresStorage.connect` set no timeout, is the reason that call
  now fails fast.

**PRs:** [#12](https://github.com/ii1ahe/research-assistant/pull/12).

**Approximate work share:** ≈10 % — one recorded commit; the rest of her part is the
Brief B quiz answers and her defense slot.

---

## @Elmin995 — peer review, ≈10 %

**Owned:** nothing in the tree — review produces no commits, which is why
`shortlog` cannot show him.

**Reviewed:**

- PR [#11](https://github.com/ii1ahe/research-assistant/pull/11) (Retry-After):
  approved with a written review, plus an inline comment on the
  throttling-marker edge case in the translation layer.
- PR [#12](https://github.com/ii1ahe/research-assistant/pull/12) (clean-clone
  reproduction): approved with a written review, plus an inline comment on
  the artefact asking that the connect-timeout finding become a follow-up
  fix — which the final polish PR carries.

**Approximate work share:** ≈10 % — zero commits, all of it review; his
defense slot covers it.

---

## Co-owned

The connect-timeout fix is the one change three people touched: found by
fatimekazimli's reproduction, demanded as a follow-up by Elmin995's review
comment, written by ii1ahe.

---

## AI tool disclosure (also in §10 of the report)

The code author used AI coding assistants as follows. Each item names what
the assistant produced and what was done with it, including the parts that
were rejected or rewritten.

| Module / file | Assistant | What was done with it |
|---|---|---|
| `researcher/storage/`, `migrations/001_initial_schema.sql` | Claude (Anthropic) | Drafted the `asyncpg` repositories and the in-memory doubles from the Protocols I specified. I rewrote the connection handling after the in-memory and PostgreSQL doubles diverged on a cache-hit miss path, and added `tests/test_migrations.py` to pin the schema. |
| `researcher/services/ai_service.py`, `resilience.py` | Claude (Anthropic) | Drafted the retry policy and the translation layer that turns `ai/`'s exceptions into this project's taxonomy. I raised synthesis off the per-source 10 s deadline after the benchmark showed it refusing roughly one synthesis in six, and wrote the regression test that fails if the two deadlines are rejoined. |
| `researcher/services/wikipedia.py` | Claude (Anthropic) | Drafted the replacement search step. I established by experiment that the supplied `opensearch` search cannot answer a multi-word question, and chose `list=search` with a setting to switch back so the divergence is checkable. |
| `tests/` (all 19 files) | Claude (Anthropic) | Drafted the suite from the module contracts. I reviewed each test and wrote the offline guard after finding that five tests were silently calling the live Wikipedia API and passing anyway — they patched a fetcher that was no longer being called. |
| `Dockerfile`, `compose.yaml`, `.dockerignore` | Claude (Anthropic) | Drafted from the supplied template. Three defects were found only by building and running the image — see §7.1 of the report — and each fix is documented in the README. |
| `docs/architecture.md`, `docs/security.md` | Claude (Anthropic) | Drafted the prose from decisions I had already made. The six ADRs record choices that are mine; `docs/security.md`'s gap list was checked against the running system rather than asserted. |
| `report/report.tex`, `slides/slides.tex` | Claude (Anthropic) | Drafted the prose and typeset both documents from `templates/`. Every number is traceable to `artefacts/bench.json`, a test run, or the container — I re-ran the suite (357 passed, 96 %) and checked the module line counts against `wc -l` before submitting. |
| `artefacts/reproduction-codespaces-bfec78.txt` | Claude (Anthropic) | Drafted from the machine's actual output — versions, commands, counts — which I checked against my Codespace run before committing. |
| — | GitHub Copilot | Not used. |
| `report/`, `slides/`, submission documentation | OpenAI Codex | Checked the final documents against source code, test evidence, and compiled PDFs; corrected inconsistencies. |

We affirm that **every line of code in the repository can be defended** by
at least one member during the oral defense. "The AI wrote it" is not an
answer we will use, and where an assistant's output was wrong it is said so,
with the evidence left in the repository.

---

## Signatures

By signing below, we affirm that:
- The contributions described above are accurate.
- The percentages reflect actual work, not artificially split commits.
- Every line of code in the repository can be defended by at least one team
  member.
- AI assistant usage has been disclosed as described above.

| Member | Signature | Date |
|---|---|---|
| @ii1ahe | __________________________ | __________ |
| @fatimekazimli | __________________________ | __________ |
| @Elmin995 | __________________________ | __________ |
