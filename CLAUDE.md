# Phase
IMPORTANT: do not write application code until specs/tasks.md exists
and I have explicitly approved it. Before that, only read, ask, and
write inside specs/.
IMPORTANT: implement only the tasks I name in the prompt. Do not move
on to the next task on your own. When done, stop and wait.

# Project
Multi-agent support system for a payments acquirer.
Technical challenge, 60-minute core development window.
Priority: defensible, readable architecture over feature coverage.

# Non-negotiable
- No answer to the user without a retrieved source or tool result.
- IMPORTANT: user_id comes from the authenticated request and is passed via InjectedState. The field is never exposed in the model schema.
- Sensitive data is masked before any log.
- Router decisions are Pydantic structured output, never parsed text.
- Tool errors become a response or an escalation, never a stacktrace.
- Prompts live in app/prompts/, versioned, never hardcoded.
- Every external dependency degrades with a message, never an exception.

# Style
- Python 3.12, type hints on public functions, Pydantic for contracts.
- Short functions. Over 40 lines, split.
- No speculative abstraction. No factory or service layer with a single implementation.
- No bare except swallowing exceptions.

# Language
- Code, docstrings, logs, commits, README and specs: English.
- System prompts in app/prompts/ and end-user answers: Brazilian Portuguese.
- Never mix languages inside the same file.

# Commands
- Tests: uv run pytest -q
- Lint: uv run ruff check .

# Tests
- LLM provider mocked. Suite runs offline, no cost.
- Tests use tests/fixtures/kb_sample.jsonl, never the full corpus.

# Commits
- Conventional Commits, one per task.
- Format: <type>(<T-id>): <imperative, lowercase>
- Never group two tasks in one commit. Never reuse a T-id.
- The message describes what is in the diff, not the intent.
