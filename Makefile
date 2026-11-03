# Developer shortcuts. Everything runs locally; `install` downloads dependencies once.
.PHONY: install install-backend install-frontend lint format typecheck test check \
        api web web-build web-lint web-typecheck web-test e2e check-all precommit

install: install-backend install-frontend

install-backend:
	uv sync --locked

install-frontend:
	cd frontend && npm ci

# ---------- backend ----------
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

api:  ## Run the API on http://127.0.0.1:8000 (data under BIP_DATA_ROOT, default ./var/data)
	uv run python -m bip.api

# ---------- frontend ----------
web:  ## Vite dev server on http://127.0.0.1:5173, proxying /api to the backend
	cd frontend && npm run dev

web-build:
	cd frontend && npm run build

web-lint:
	cd frontend && npm run lint

web-typecheck:
	cd frontend && npm run typecheck

web-test:
	cd frontend && npm test

# ---------- integration ----------
e2e: web-build  ## Real backend + built frontend + Chromium, temporary data root
	cd frontend && npm run e2e

check-all: check web-lint web-typecheck web-test e2e

precommit:
	uvx pre-commit run --all-files
