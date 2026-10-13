# Thin wrappers over the commands in the README.
.PHONY: up test eval demo

# Build the image and start the API in the background, returning once it is healthy.
up:
	docker compose up --build --wait

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
