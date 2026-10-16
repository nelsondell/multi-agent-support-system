# Build stage: resolve dependencies with uv against the lockfile, nothing else.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies are their own layer: application edits do not invalidate the resolved venv.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY app ./app
COPY data/customers.json ./data/customers.json
COPY data/corpus ./data/corpus
RUN uv sync --frozen --no-dev


# Runtime stage: the virtualenv, the application and the corpus snapshot, no build tooling.
FROM python:3.12-slim AS runtime

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
    PYTHONDONTWRITEBYTECODE=1 \
    TIKTOKEN_CACHE_DIR=/home/appuser/.cache/tiktoken

USER appuser

# Fetch the chunker's tokenizer and the local embedding model now, so the first boot builds the
# index without downloading anything.
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" \
    && python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; DefaultEmbeddingFunction()(['warm-up'])"

EXPOSE 8000

# Hits the liveness endpoint with the interpreter already present: no curl in the image.
HEALTHCHECK --interval=30s --timeout=3s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# First boot builds the index into the volume; every later boot finds it and goes straight to the API.
CMD ["sh", "-c", "python -m app.rag.ingest --if-missing && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
