# Thin wrappers over the commands in the README.
.PHONY: verify verify-offline models up scale test eval demo

# From a clean clone, with no key: the frozen sync is the only step that uses the network; then
# lint, the offline suite and the offline evals, recorded in reports/verify.md.
verify:
	@start=$$(date +%s); uv sync --frozen && \
		VERIFY_SYNC_SECONDS=$$(( $$(date +%s) - start )) $(MAKE) --no-print-directory verify-offline

# The checks alone, in an environment already synced. CI runs them with the network cut.
verify-offline:
	uv run --frozen --offline python scripts/verify.py

# The local embedding model, for EMBEDDING_BACKEND=local outside Docker (the image build fetches
# it). Nothing downloads it at runtime.
models:
	uv run --frozen python -m app.providers.embeddings

# Build the image and start the API in the background, returning once it is healthy.
up:
	docker compose up --build --wait

# Postgres, two replicas over it and a round-robin load balancer on SCALE_PORT (8080), returning
# once both replicas are ready.
scale:
	docker compose --profile scale up --build --wait lb

# Lint, then the offline suite: no vendor call, no cost.
test:
	uv run ruff check .
	uv run pytest -q

# Routing golden set. The guardrail stage always runs; the model stage needs OPENAI_API_KEY.
eval:
	uv run python -m evals.run_routing_eval

# Scripted requests against the running API (make up first).
demo:
	./scripts/demo.sh
