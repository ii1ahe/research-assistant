# =====================================================================
# Researcher — Async Research Assistant (Topic 4)
#
# Multi-stage, as templates/Dockerfile.template suggests: the build stage
# resolves the pinned dependency tree, the runtime stage ships only the
# installed packages and the source — no compiler, no pip cache.
#
#   docker build -t finalproj .
#   docker run --rm finalproj pytest tests/test_ai_smoke.py   # no keys, no quota
#   docker compose up --build                                 # with PostgreSQL
# =====================================================================

# ---- Stage 1: resolve the pinned dependency tree --------------------
# python:3.14.7-slim, not 3.12: development and verification run on 3.14.7,
# and the image is the deployment the README's numbers come from. The patch
# is pinned rather than the minor version so a rebuild months from now is the
# same interpreter, not a newer one that quietly changes the wheel set.
FROM python:3.14.7-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Both requirement files, and only them, before the source: this layer is
# cached across builds that change nothing but code.
COPY requirements.txt requirements-dev.txt ./

# Runtime dependencies, then the test subset of the dev requirements — pytest
# and its plugins, without ruff and mypy. Filtered from requirements-dev.txt
# rather than pinned a second time here, so the tested version cannot drift
# from the version the image installs.
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt \
    && sed -e '/^-r /d' -e '/^ruff/d' -e '/^mypy/d' requirements-dev.txt \
         > /tmp/test-requirements.txt \
    && pip install --no-cache-dir --prefix=/install -r /tmp/test-requirements.txt


# ---- Stage 2: runtime -----------------------------------------------
FROM python:3.14.7-slim AS runtime

LABEL org.opencontainers.image.title="Researcher — Async Research Assistant"
LABEL org.opencontainers.image.description="Topic 4, AI-ENG-110 Software Engineering, AI Academy"
LABEL org.opencontainers.image.version="1.0"

# PYTHONDONTWRITEBYTECODE: no .pyc files in the container, which is also what
#   lets appuser run without write access to /app.
# PYTHONUNBUFFERED: the answer and the diagnostics appear as they are produced,
#   rather than at exit.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The installed packages land in the same layout the base image's python
# already searches, so no PYTHONPATH or venv activation is needed.
COPY --from=builder /install /usr/local

WORKDIR /app

# `.dockerignore` decides what this copies. `ai/` and `researcher/` sit side by
# side in the working directory, which is on sys.path — that is the flat layout
# ADR-001 chose precisely so `python -m researcher` resolves here with no
# install step.
COPY . .

# Non-root. Nothing the application does writes to disk — the cache and the
# session store are both PostgreSQL — so appuser needs no write access at all.
RUN useradd --create-home --shell /usr/bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

# No EXPOSE and no published port: ADR-006, this project has no HTTP API.

# The template's entrypoint for a CLI topic. `demo` runs the five supplied
# questions end to end against the live providers, which needs credentials —
# `docker run` without them exits 2 naming the variable that is missing,
# rather than failing later with an opaque provider error.
CMD ["python", "-m", "researcher", "demo"]

# =====================================================================
# See README "Run with Docker" for what each command proves, and for why
# `docker run --env-file .env` needs one adjustment to reach a database
# running on the host.
# =====================================================================
