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

### C. The concurrency bound is not a rate limit — throttles are now honoured

`MAX_PARALLEL_SOURCES` caps how many source tasks are in flight at once. That
is a politeness bound on our own behaviour, not a quota against a provider's
limit. What changed since the measurement below is that a throttle is now
*reacted to* rather than retried into the same window.

**Measured, Phase 8 (2026-09-16).** The gap was not theoretical and the margin
was large. When Gemini's free tier refused a synthesis it named both the limit
and the remedy — `429 RESOURCE_EXHAUSTED`, `limit: 20, model: gemini-3.8-flash`,
`Please retry in 21.9s` — while `RetryPolicy` waited a jittered ≤0.5 s between
three attempts. Every attempt landed inside the same window, so a throttled
synthesis failed in under a second despite having three tries and a 30 s
deadline. The delay the provider asked for is roughly forty times the backoff
ceiling.

**Fixed, 2026-09-17.** The fix respects the boundary the measurement
described. The hint lives in the provider's payload, and `_translate` replaces
that payload with a generic message *deliberately*, so that a provider's error
text cannot reach a log or a user — the classification boundary is the last
place the raw text exists, so it is also the place that parses the delay out
of it. `UpstreamRateLimitError` carries the parsed number (never the text);
`execute` waits the larger of its jittered backoff and the hint, uncapped by
`max_backoff` and bounded instead by the caller's deadline. A throttle with no
parseable delay is still classified, and still retried on the ordinary
backoff. Tests cover the parse shapes, the precedence (own backoff versus the
hint), the per-attempt application, and the no-leak guarantee that the
payload's text does not reach the message.

*To productionize:* a token bucket per provider, so a quota is anticipated
rather than discovered by the first refused call. The current behaviour no
longer converts a recoverable throttle into a lost question; it converts it
into a slower answer.

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

### F. Cache retention needs an operator to run the purge

`purge_expired` exists and is tested, and `researcher purge` now calls it
from the command line — reporting how many rows it removed, failing loudly
(exit 1) if the database refuses the delete, and saying so when no database
is configured. It is **not scheduled**, by design: the application has no
daemon and no HTTP API (ADR-006), so the only honest shape for housekeeping
is a command an operator runs. Expired rows therefore accumulate until
someone runs it.

*To productionize:* a cron entry on the host, or a database-side scheduled
task. The purge itself now exists; what is left is the reminder to invoke
it.

### G. Dependency pins are scanned, but only when someone remembers

Pinning in `requirements.txt` gives reproducible builds, and the pins are now
scanned: `pip-audit` is pinned in `requirements-dev.txt`, and the 2026-09-17
run against both the runtime and the dev pins found **no known
vulnerabilities** — the raw output is committed in `artefacts/pip-audit.txt`.

*To productionize:* run `pip-audit` in CI on every change to either
requirements file, plus automated update PRs. A one-off scan is a snapshot,
not a property.

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
