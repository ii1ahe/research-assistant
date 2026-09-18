# Peer review record

This repository's rule is that work is reviewed on a pull request before it
merges. Two of those reviews were substantive — they raised findings that
changed what shipped, or that are worth knowing were consciously kept — and
until now they lived only on GitHub, where someone reading the tree could not
see them. This file records them.

Reviewer: [`@Elmin995`](https://github.com/Elmin995), on both reviews. Every
claim below links to the review or to the inline comment on the public
repository, so each one can be checked against the original.

## PR #11 — honouring the provider's `Retry-After`

**Pull request:** [#11 — Honour the provider's Retry-After instead of retrying
into the throttle](https://github.com/ii1ahe/research-assistant/pull/11),
merged 2026-09-17.
**Review:** approved 2026-09-17
([review](https://github.com/ii1ahe/research-assistant/pull/11#pullrequestreview-5232366602)).

What was checked, beyond reading the diff:

- **The no-leak guarantee survived the change.** The new parsing reads the
  provider's exception text, which is the one place an upstream payload can
  reach a message the user sees. The review confirmed the tests still assert
  that the measured Gemini text never appears in the surfaced message.
- **The hint is applied where it can be.** It is parsed in `_translate`,
  carried as a number on `UpstreamRateLimitError`, and `execute` waits the
  larger of its jittered backoff and the hint — deliberately *uncapped* by
  `max_backoff`, since a 21.9 s hint against an 8 s ceiling would defeat the
  point of the change. The per-source deadline remains the real bound.
- **The tests cover the floor in both directions,** its application per
  attempt, and the whole chain with an elapsed-time assertion that the
  previous behaviour cannot pass.
- **The prose agrees with the code** in report §4.2, slide 9, the README and
  `docs/security.md`; both PDFs rebuild clean.

**Finding raised** — inline on the throttling-marker list,
`researcher/services/ai_service.py:90`
([comment](https://github.com/ii1ahe/research-assistant/pull/11#discussion_r4034154259)):
`"429"` is matched as a substring, so a payload containing `429` inside an
identifier or a byte count would be classified as a throttle. The suggested
tightening was a word boundary, or pairing the number with a
too-many-requests-style phrase.

**Outcome: raised, then consciously kept.** The cost of a false positive is
one extra attempt under the wrong category, while a narrower match risks
missing a real throttle — so the marker list is unchanged today. It is
recorded here because "reviewed and deliberately not changed" is a decision,
not an oversight.

## PR #12 — the clean-clone reproduction

**Pull request:** [#12 — Clean-clone reproduction on
codespaces-bfec78](https://github.com/ii1ahe/research-assistant/pull/12),
merged 2026-09-17.
**Review:** approved 2026-09-17
([review](https://github.com/ii1ahe/research-assistant/pull/12#pullrequestreview-5233340100)).

What was checked:

- The evidence rests on the machine's own output — versions, commands, counts
  — rather than on a narrative written over it.
- The seven skips are more informative than a fully green run would have been:
  DNS resolved, PostgreSQL was healthy on the same compose network, and the
  TCP handshake still never completed. That combination pins the failure to
  the Codespace's Docker-in-Docker bridge rather than to the repository, and
  it documents the one assumption the compose path makes.
- The live-demo skip is stated outright rather than glossed over.

**Finding raised** — inline on the artefact,
`artefacts/reproduction-codespaces-bfec78.txt:87`
([comment](https://github.com/ii1ahe/research-assistant/pull/12#discussion_r4034939460)):
an unreachable database cost sixty seconds per attempt because the connect call
set no timeout, while the compose header already promises that an unusable
database fails fast at bootstrap. The review asked for this to become a
follow-up change rather than a note that stayed inside an artefact.

**Outcome: acted on.** [PR #13](https://github.com/ii1ahe/research-assistant/pull/13)
carried the fail-fast connect — `PostgresStorage.connect` now bounds
connection attempts with a 10 s `timeout` instead of the driver's 60 s default.
The finding is the one change all three members touched, and
`CONTRIBUTION_STATEMENT.md` records it that way: found by `fatimekazimli`'s
reproduction, asked for by this review, written by `ii1ahe`.

## Scope

This is a review record, not an authorship claim over what was reviewed.
PR #11 was written by `ii1ahe` and PR #12 by `fatimekazimli`; the reviews above
did not change that, and they are the whole of the reviewer's contribution to
the tree. The review that produces no finding is worth stating too: nothing
else in either pull request needed to change before it merged.
