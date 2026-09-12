-- 001_initial_schema.sql
--
-- Initial schema for the two persistence concerns ADR-002 assigns to
-- PostgreSQL: the source-result cache and the research-session store.
--
-- Applied by researcher.storage.postgres.run_migrations, which records each
-- filename in `schema_migrations` and verifies its checksum on every later
-- start. Migrations are therefore append-only: editing a file that has already
-- been applied will be refused rather than silently skipped.
--
-- Every statement is idempotent so a partially applied migration can be
-- re-run safely.

-- --------------------------------------------------------------------------
-- Source cache
-- --------------------------------------------------------------------------
-- The primary key is the whole CacheKey: source, canonical query, provider,
-- result limit and format version. Putting all five in the key (rather than
-- hashing them into one opaque column) means an operator can see what is
-- cached and why a given lookup missed, which a digest column would hide.
--
-- The write path is `INSERT ... ON CONFLICT` against exactly this key, so the
-- upsert is enforced by the database rather than by a read-then-write.
CREATE TABLE IF NOT EXISTS source_cache (
    source         text        NOT NULL,
    query          text        NOT NULL,
    provider       text        NOT NULL,
    max_results    integer     NOT NULL,
    schema_version integer     NOT NULL,
    payload        jsonb       NOT NULL,
    fetched_at     timestamptz NOT NULL,
    expires_at     timestamptz NOT NULL,

    CONSTRAINT source_cache_pkey
        PRIMARY KEY (source, query, provider, max_results, schema_version),

    -- Mirrors the `ge=1` bounds on CacheKey. These are numeric ranges, not
    -- enumerations, so they cannot drift the way a copied enum could.
    CONSTRAINT source_cache_max_results_positive CHECK (max_results >= 1),
    CONSTRAINT source_cache_schema_version_positive CHECK (schema_version >= 1),

    -- A row that expires before it was fetched is a bug in the writer, not a
    -- user-visible condition, so it is rejected at the boundary.
    CONSTRAINT source_cache_window_ordered CHECK (expires_at >= fetched_at)
);

-- Supports purge_expired, which is otherwise a full scan on every run.
CREATE INDEX IF NOT EXISTS source_cache_expires_at_idx
    ON source_cache (expires_at);

-- --------------------------------------------------------------------------
-- Research sessions
-- --------------------------------------------------------------------------
-- `payload` holds the whole ResearchSession as JSON, written from and read
-- back into the Pydantic model. Snippets and per-source outcomes are retained:
-- the supplied AnswerWithCitations.to_dict() is a presentation format that
-- drops them, so it is not an archival record.
--
-- `created_at`, `status` and `question` are denormalised copies of fields
-- inside the payload. They exist so list_recent can order and page without
-- parsing every JSON document. They are deliberately NOT constrained to the
-- ResultStatus enumeration: that enum lives in Python, and duplicating it as a
-- SQL CHECK would mean a new status could only be added by shipping a
-- migration. The payload is validated against the model on read, so a bad
-- value surfaces as a StorageError rather than as a wrong answer.
CREATE TABLE IF NOT EXISTS research_sessions (
    id         uuid        PRIMARY KEY,
    created_at timestamptz NOT NULL,
    status     text        NOT NULL,
    question   text        NOT NULL,
    payload    jsonb       NOT NULL
);

-- list_recent orders by created_at descending; the matching index keeps that
-- from sorting the whole table once the store has history in it.
CREATE INDEX IF NOT EXISTS research_sessions_created_at_idx
    ON research_sessions (created_at DESC);
