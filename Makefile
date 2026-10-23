# Thin wrappers over the commands in the README.
.PHONY: verify verify-offline models dev-keys up scale observe drill purge test eval demo

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

# A local RS256 key pair standing in for the identity provider: .dev/jwks.json for the API,
# the private key for scripts/dev_token.py. Kept if it exists.
dev-keys:
	uv run --frozen python scripts/dev_keys.py

# Build the image and start the API in the background, returning once it is healthy.
up: dev-keys
	docker compose up --build --wait

# Postgres, two replicas over it and a round-robin load balancer on SCALE_PORT (8080), returning
# once both replicas are ready.
scale: dev-keys
	docker compose --profile scale up --build --wait lb

# The one node plus Prometheus (alert rules), Grafana (dashboard) and Jaeger (traces), on ports
# 9090, 3000 and 16686.
observe: dev-keys
	OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317 docker compose --profile observability up --build --wait

# A vendor drill: `make drill FAULT=model_error` (or model_timeout, web_down). docs/drills.md.
drill:
	./scripts/drills/run.sh $(FAULT)

# Erase conversations idle for longer than RETENTION_DAYS, with the cases they opened.
purge:
	uv run --frozen python -m app.retention

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
