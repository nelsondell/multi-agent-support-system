# Base images are pinned by digest (multi-arch index), so a rebuild gets the same bytes until
# Dependabot proposes a newer digest.

# Build stage: resolve dependencies with uv against the lockfile, nothing else.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10.11@sha256:3472e43b4e738cf911c99d41bb34331280efad54c73b1def654a6227bb59b2b4 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# A local reranker (`--build-arg RERANKER=bge` or `qwen`) adds the `rerank` group, torch included,
# and its weights below. The default image has neither: evals/rerank_benchmark.md keeps it off.
ARG RERANKER=none

# Dependencies are their own layer: application edits do not invalidate the resolved venv.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev \
    $(case "$RERANKER" in (bge|qwen) echo --group rerank;; esac)

COPY app ./app
COPY data/customers.json ./data/customers.json
COPY data/corpus ./data/corpus
RUN uv sync --frozen --no-dev $(case "$RERANKER" in (bge|qwen) echo --group rerank;; esac)


# Runtime stage: the virtualenv, the application and the corpus snapshot, no build tooling.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime

# No API key at build time: the index is built on first boot, from the committed snapshot.
RUN groupadd --system --gid 1001 appuser \
    && useradd --system --uid 1001 --gid appuser --create-home appuser

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/app /app/app
COPY --from=builder --chown=appuser:appuser /app/data /app/data

# The index and state volumes mount here. Creating the directories owned by appuser makes Docker
# initialise fresh named volumes with that ownership, so the non-root process can write to them.
RUN mkdir -p /app/data/processed /app/data/state \
    && chown appuser:appuser /app/data/processed /app/data/state

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

# Fetch the local embedding model now, so the first boot builds the index without downloading
# anything; at runtime the app refuses to fetch it. The chunker's tokenizer ships in app/rag/assets.
RUN python -m app.providers.embeddings

# Same for a local reranker's weights, when the image was built with one.
ARG RERANKER=none
ENV RERANKER=$RERANKER
RUN case "$RERANKER" in bge|qwen) python -m app.rag.rerank "$RERANKER";; esac

EXPOSE 8000

# Readiness, with the interpreter already present: no curl in the image. /ready answers 503 until
# the store answers and the index is loaded.
HEALTHCHECK --interval=30s --timeout=3s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2).status == 200 else 1)"

# First boot builds the index into the volume; every later boot finds it and goes straight to the API.
# On SIGTERM uvicorn stops accepting connections and gives in-flight turns 30 s to finish.
CMD ["sh", "-c", "python -m app.rag.ingest --if-missing && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 30"]
