# Full sample run — 2026-09-19

This artefact records one uncached end-to-end execution against the real
Wikipedia, arXiv, web-search and Gemini providers, with PostgreSQL session
persistence enabled. No credentials are included.

## Command

```bash
DATABASE_URL=postgresql://researcher@127.0.0.1:55439/researcher \
python -m researcher ask \
  "How does retrieval-augmented generation improve the factual reliability of large language models?" \
  --no-cache
```

The temporary database used the submitted migration. The configured model was
`gemini-3.1-flash-lite`. The command exited with status `0`.

## Standard output

```text
Question: How does retrieval-augmented generation improve the factual reliability of large language models?

Retrieval-augmented generation (RAG) improves the factual reliability of large language models by supplementing their static training data with external, up-to-date information [1, 4]. By retrieving relevant documents and injecting them as context at query time, the model can generate responses that are more accurate and domain-specific [4, 5]. This process allows for "grounded generation," which helps mitigate the occurrence of generative AI hallucinations by providing authoritative facts directly within the input prompt [1, 5]. Ultimately, this framework combines the model's language capabilities with verified data to ensure results are current and relevant to specific needs [4, 5].

References
[1] Retrieval-augmented generation
    https://en.wikipedia.org/wiki/Retrieval-augmented_generation
[4] What is Retrieval Augmented Generation (RAG)?
    https://www.databricks.com/blog/what-is-retrieval-augmented-generation
[5] What is Retrieval-Augmented Generation (RAG)?
    https://cloud.google.com/use-cases/retrieval-augmented-generation
```

## Diagnostic output

```text
INFO researcher.storage.postgres: applied migration 001_initial_schema.sql
INFO researcher.services.ai_service: wikipedia returned 3 result(s) in 0.63s (1 attempt(s))
INFO researcher.services.ai_service: web returned 3 result(s) in 1.17s (1 attempt(s))
WARNING researcher.services.ai_service: arxiv retrieval ended as timeout (upstream_timeout)
INFO researcher.services.orchestrator: retrieved 5 source(s) from 2 of 3 selected
INFO researcher.core.researcher: saved session c17d8714-cbc4-47b1-93aa-13c4f4952fc5

sources
  wikipedia  ok          3 results (0.63s)
  arxiv      timed out   upstream_timeout
  web        ok          3 results (1.17s)

warnings
  - arxiv was unavailable (upstream_timeout): exceeded its 10s deadline

timing  10.03s retrieval, 2.55s synthesis, 12.58s total
```

## Persistence verification

The most recent `research_sessions` row was queried after the command:

```text
id:         c17d8714-cbc4-47b1-93aa-13c4f4952fc5
status:     partial
created_at: 2026-09-19 01:42:58.172903-04
question:   How does retrieval-augmented generation improve the factual reliability of large language models?
payload:    id, answer, status, timing, request, provider, warnings, retrieval, created_at
```

The `partial` status is expected: one selected source timed out, while two
sources supplied enough evidence for a cited answer. The successful process
exit and the explicit warning demonstrate the application's documented
graceful-degradation contract.
