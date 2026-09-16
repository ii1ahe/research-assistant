# Security notes

Source material for the report, plus the reasoning behind choices that look
arbitrary in the code without it. Everything below was checked against the
code or the running system; nothing here is asserted from memory.

The report template has **no dedicated security section**. The material is
written to land in the places the template actually asks for:

| Report section | Use from here |
|---|---|
| `Limitations \& Future Work` *(required)* | Gaps A, B, E, D — "what it would take to productionize" |
| `Deployment \& Reproducibility → Configuration` *(required)* | Gap A, and the variable list in `README.md` |
| `Robustness \& Error Handling → Input validation` | Posture 6 (`validation.py`) |
| `Robustness \& Error Handling → Wrapping the AI module` | Posture 4 and 5 |
| `Tools \& Acknowledgements` *(required)* | The AI-assistant disclosure on PR #1 |

The direct answer to "how would you harden this for production?" is gap A plus
B, in that order.

## Posture as built

These are deliberate, and each one is verifiable rather than aspirational.

1. **Credentials are never echoed into an error message.** Pydantic attaches
   the offending input to every validation error, and those inputs are
   routinely API keys. `config._describe` reports only the field location and
   the reason, never `error["input"]`. Verified by a test that plants a fake
   key in the environment, forces a validation failure, and asserts the key
   does not appear in the resulting message.

2. **Credentials are never in the repository.** `.env` is gitignored
   (`git check-ignore` reports `.gitignore:3`) and does not appear in
   `git add -A --dry-run`. `.env.example` ships with blank values.

3. **API keys travel in request bodies, not URLs.** The supplied
   `TavilyProvider` posts `{"api_key": ...}` to a fixed endpoint
   (`ai/sources.py:242-249`), so a key cannot leak into an access log, a proxy
   log, or a `Referer` header. Had it used a query parameter, that would be a
   finding.

4. **Driver detail is contained at the storage boundary.** `storage/interfaces.py`
   requires implementations to translate driver exceptions into `StorageError`,
   so `asyncpg` types and DSN fragments cannot reach the user or the logs.
   *(Phase 3 enforces this; the contract is set.)*

5. **Provider detail is contained at the AI boundary.** The supplied
   `ai.providers.base.ProviderError` is coarse — it covers a missing package, a
   missing credential and a network fault alike — and its messages can quote
   upstream response text. It is translated into an `UpstreamError` with a
   safe message at the `ai_service` boundary rather than allowed to escape.
   *(Phase 4.)*

6. **Model output is validated structurally, not trusted.** `validate_answer`
   rejects non-positive, out-of-range and duplicated citation indices, and
   detects a source list that was reordered after synthesis — which would
   otherwise silently repoint every reference number in the prose.
   `unreferenced_citation_indices` catches the dangling citations the supplied
   `ai.synthesize` leaves behind, because it drops out-of-range indices from
   `citations` but does not edit the answer prose.

## Gaps, and what productionizing takes

### A. PostgreSQL is configured with `trust` authentication — the headline finding

`pg_hba.conf` is at `initdb`'s default: `trust` for **local and host**
connections, every database, every user. Authentication is therefore not
performed at all. Any process that can reach port 5432 connects as any role,
including the `postgres` superuser, with no password.

*Blast radius today is small and bounded, not accidentally:* the server listens
only on `127.0.0.1` and `[::1]`, so nothing off-host can reach it; and the
`researcher` role is deliberately **not** a superuser. The exposure appears the
moment `listen_addresses` is widened, the port is forwarded, or the machine is
on an untrusted network.

*To productionize:* switch `pg_hba.conf` to `scram-sha-256`, set a real
password on the application role, keep `listen_addresses` pinned to loopback
unless remote access is genuinely required, and require TLS for any remote
connection. Keep the application role non-superuser and grant it only the
privileges its two tables need.

### B. Retrieved web content is untrusted input fed to a language model

Snippets come from arbitrary pages and are interpolated into the synthesis
prompt. A hostile page can carry text aimed at the model rather than the reader
("ignore the above and state that X"), which is prompt injection.

*What we do today is structural only:* the prompt instructs the model to answer
from the numbered sources and not to invent them, and we validate the **shape**
of the result. We do not, and cannot, validate that a source supports the claim
attached to it.

*To productionize:* treat retrieved text as data rather than instructions,
delimit it unambiguously in the prompt, strip control sequences and
instruction-shaped markup, and add a grounding or entailment check before an
answer is presented as supported.

### C. The concurrency bound is not a rate limit

`MAX_PARALLEL_SOURCES` caps how many source tasks are in flight at once. That
is a politeness bound on our own behaviour, not a quota against a provider's
limit, and it does not react to being throttled.

**Measured, Phase 8 (2026-09-16).** The gap is not theoretical and the margin is
large. When Gemini's free tier refused a synthesis it named both the limit and
the remedy — `429 RESOURCE_EXHAUSTED`, `limit: 20, model: gemini-3.8-flash`,
`Please retry in 21.9s` — while `RetryPolicy` waits a jittered ≤0.5 s between
three attempts. Every attempt lands inside the same window, so a throttled
synthesis fails in under a second despite having three tries and a 30 s
deadline. The delay the provider asks for is roughly forty times the backoff
ceiling.

Honouring it is not a one-line change. The hint lives in the provider's payload,
and `_translate` in `services/ai_service.py` replaces that payload with a
generic message *deliberately*, so that a provider's error text cannot reach a
log or a user. The classification is therefore the only place that can read
`Retry-After`, and it currently has nowhere to put it — the taxonomy carries a
category, not a delay.

*To productionize:* a token bucket per provider; a `Retry-After`-carrying
retryable error introduced at the classification boundary, where the payload
still exists; and a policy that waits for the larger of its own backoff and the
provider's instruction. The current behaviour is not unsafe — a throttled run
degrades to a partial answer and says so — but it converts a recoverable
condition into a lost question.

### D. Secrets live in the process environment

Adequate for a laptop. Anything that can read `.env` or `/proc/<pid>/environ`
reads the keys, and they are long-lived.

*To productionize:* a secret manager, short-lived or scoped credentials, and
rotation.

### E. Answer correctness is not verified — the core limitation

`validate_answer` establishes internal consistency and nothing more: that the
reference numbers exist, are unique and point where they claim to. A fluent,
correctly-cited, **wrong** answer passes every check the system has. This is
inherent to the approach, not a defect that more code removes, and it is why
partial and degraded results are surfaced to the user rather than smoothed over.

### F. Cache retention is implemented but not scheduled

`purge_expired` exists and is tested, but nothing calls it on a schedule, so
expired rows accumulate until something does.

*To productionize:* a retention job, or a database-side scheduled task.

### G. Dependencies are pinned but not scanned

Pinning in `requirements.txt` gives reproducible builds. Nothing scans those
pins for known vulnerabilities.

*To productionize:* `pip-audit` (or equivalent) in CI, plus automated update
PRs.

## Checked and found clean

Stated so that "we checked" is distinguishable from "we assumed":

- No `eval`, `exec`, `shell=True`, `os.system` or `subprocess` anywhere in
  `researcher/`.
- No hardcoded credentials in any tracked file.
- No credential is logged; the only references to key fields are their
  declarations and the resolution properties in `config.py`.
- No SQL is string-interpolated, because no SQL is written yet. Phase 3 must
  route every statement through parameterised `asyncpg` queries; this is a
  constraint on that phase, recorded here so it is not quietly missed.
- The supplied `ai/` package is imported and never modified, so its behaviour
  is auditable against the original by diff.
