# Developer shortcuts. All targets run offline except `install` (downloads dev dependencies).
.PHONY: install lint format typecheck test check precommit

install:
	uv sync --locked

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run pyright

test:
	uv run pytest

check: lint typecheck test

precommit:
	uvx pre-commit run --all-files
