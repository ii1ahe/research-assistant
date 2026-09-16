<!--
  Before handing this in, replace the three visible placeholders:
    _<Full Name>_     appears in the header, the member heading, and the signature table
    _<Team Name>_     leave the team as your name if you submitted alone
    _<YYYY-MM-DD>_    the date you send the package
  Then create the final tag on main and push it:
    git tag -a v1.0-final -m "Final submission" && git push origin v1.0-final
-->

# Contribution Statement

**Team:** _<Team Name>_
**Topic:** Topic 4 — Async Research Assistant
**Repository:** [https://github.com/ii1ahe/research-assistant](https://github.com/ii1ahe/research-assistant)
**Final tag:** `v1.0-final`
**Submission date:** _<YYYY-MM-DD>_

---

## Submission type: single member

This is a **one-person submission**. `git shortlog -sn main` reports a single
author across every commit, so the contribution split is not an estimate and
nothing needs to be apportioned: one member holds 100 %, and the rubric's
"member below 10 %" deduction cannot apply.

Where this statement says "I", it is the attestation voice of a signature
document. The report and the README say "we", which is the ordinary editorial
convention for technical writing and is not a claim of a second contributor.

---

## _<Full Name>_ (`@ii1ahe`)

**Owned (sole author of these files / PRs):**

Application — `researcher/`, 25 source files, 4 494 lines:

- `researcher/models.py` (371), `researcher/validation.py` (358),
  `researcher/config.py` (316), `researcher/errors.py` (142) — the typed
  contracts, the input and output validators, and the settings object whose
  `persistence_enabled` flag is derived rather than stored.
- `researcher/services/ai_service.py` (467), `resilience.py` (201),
  `http_client.py` (48) — the only boundary that calls `ai/`, and the retry,
  backoff and timeout policy around it.
- `researcher/services/orchestrator.py` (240), `cache.py` (185),
  `wikipedia.py` (190) — bounded concurrent retrieval with per-source
  deadlines and partial results; cache-aside reads and writes; a replacement
  for the supplied Wikipedia *search* step only.
- `researcher/storage/interfaces.py` (146), `postgres.py` (253),
  `memory.py` (125), `cache_store.py` (150), `session_repository.py` (125),
  `_driver.py` (59) — the two storage Protocols and both implementations
  behind them.
- `researcher/core/researcher.py` (256) — the use case.
- `researcher/bootstrap.py` (266), `cli.py` (334), `rendering.py` (196) — the
  composition root, argument parsing, exit statuses and terminal output.

Verification — 18 test files, 5 925 lines:

- `tests/` — **327 tests, 96 % coverage** over `researcher/`.
- `tests/conftest.py` — including the autouse `no_internet` fixture that makes
  the suite's offline guarantee enforced rather than intended.
- `tests/test_offline_guard.py` — tests the guard itself.

Infrastructure and documentation:

- `migrations/001_initial_schema.sql`, `Dockerfile`, `compose.yaml`,
  `.dockerignore`
- `scripts/bench.py`; `artefacts/bench.json`, `artefacts/bench-report.txt`
- `pyproject.toml`, `pytest.ini`, `requirements*.txt`, `.env.example`,
  `.gitignore`
- `README.md`; `docs/architecture.md` (six ADRs); `docs/security.md`
- `report/report.tex` and the compiled `report/report.pdf` (13 pages, pdfLaTeX)
- `slides/slides.tex` and the compiled `slides/slides.pdf` (11 frames, beamer)

**PRs:** [#1](https://github.com/ii1ahe/research-assistant/pull/1),
[#2](https://github.com/ii1ahe/research-assistant/pull/2),
[#3](https://github.com/ii1ahe/research-assistant/pull/3),
[#4](https://github.com/ii1ahe/research-assistant/pull/4),
[#5](https://github.com/ii1ahe/research-assistant/pull/5),
[#6](https://github.com/ii1ahe/research-assistant/pull/6),
[#7](https://github.com/ii1ahe/research-assistant/pull/7),
[#8](https://github.com/ii1ahe/research-assistant/pull/8), and the Phase 8
submission PR. One branch per phase, each cut from an updated `main`.

**Co-owned:**

- Nothing. There is no second author to share ownership with.

**Reviewed:**

- Nothing, in the peer-review sense. Every PR was authored and merged by the
  same person, because there is only one person. It would be misleading to
  claim otherwise.

  What stands in for review is mechanical and is described in §6 of the
  report: 327 tests, `mypy` in strict mode over `researcher/` and `scripts/`,
  `ruff` with the `T20` rule enabled for the modules that must not print, and
  a live benchmark that drives the real APIs and a real PostgreSQL. The five
  defects recorded in the README — the retired upstream model default, the
  synthesis timeout, the benchmark's own comparison bug, the excluded
  `migrations/` directory, and the loopback list that broke under Compose —
  were all found by *running* the system rather than by reading it, which is
  the gap a second reviewer would otherwise have covered.

**Approximate share of commits:** 100 %

---

## AI tool disclosure (also in §10 of the report)

I used AI coding assistants as follows. Each item names what the assistant
produced and what I did with it, including the parts I rejected or rewrote.

| Module / file | Assistant | What I did with it |
|---|---|---|
| `researcher/storage/`, `migrations/001_initial_schema.sql` | Claude (Anthropic) | Drafted the `asyncpg` repositories and the in-memory doubles from the Protocols I specified. I rewrote the connection handling after the in-memory and PostgreSQL doubles diverged on a cache-hit miss path, and added `tests/test_migrations.py` to pin the schema. |
| `researcher/services/ai_service.py`, `resilience.py` | Claude (Anthropic) | Drafted the retry policy and the translation layer that turns `ai/`'s exceptions into this project's taxonomy. I raised synthesis off the per-source 10 s deadline after the benchmark showed it refusing roughly one synthesis in six, and wrote the regression test that fails if the two deadlines are rejoined. |
| `researcher/services/wikipedia.py` | Claude (Anthropic) | Drafted the replacement search step. I established by experiment that the supplied `opensearch` search cannot answer a multi-word question, and chose `list=search` with a setting to switch back so the divergence is checkable. |
| `tests/` (all 18 files) | Claude (Anthropic) | Drafted the suite from the module contracts. I reviewed each test and wrote the offline guard after finding that five tests were silently calling the live Wikipedia API and passing anyway — they patched a fetcher that was no longer being called. |
| `Dockerfile`, `compose.yaml`, `.dockerignore` | Claude (Anthropic) | Drafted from the supplied template. Three defects were found only by building and running the image — see §7.1 of the report — and each fix is documented in the README. |
| `docs/architecture.md`, `docs/security.md` | Claude (Anthropic) | Drafted the prose from decisions I had already made. The six ADRs record choices that are mine; `docs/security.md`'s gap list was checked against the running system rather than asserted. |
| `report/report.tex`, `slides/slides.tex` | Claude (Anthropic) | Drafted the prose and typeset both documents from `templates/`. Every number is traceable to `artefacts/bench.json`, a test run, or the container — I re-ran the suite (327 passed, 96 %) and checked the module line counts against `wc -l` before submitting. |
| — | GitHub Copilot | Not used. |
| — | Any other assistant | None. |

I affirm that I **can defend every line of code** in this repository during the
oral defense. "The AI wrote it" is not an answer I will use, and where the
assistant's output was wrong I have said so and left the evidence in the
repository.

---

## Signatures

By signing below, I affirm that:
- The contributions described above are accurate.
- The commit percentage reflects actual work, not artificially split commits.
- Every line of code in the repository can be defended by at least one team
  member.
- AI assistant usage has been disclosed as described above.

| Member | Signature | Date |
|---|---|---|
| _<Full Name>_ | __________________________ | __________ |
