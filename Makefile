.PHONY: dev down install test lint format requirements data data-cuad synthetic eval

dev: ## Start the whole stack (db, redis, api, worker, frontend)
	docker compose up --build

down: ## Stop the stack (add -v to the compose command to also wipe the database)
	docker compose down

install: ## Install backend and frontend dependencies locally
	cd backend && uv sync
	cd frontend && npm ci

test: ## Run all tests
	cd backend && uv run pytest

lint: ## Lint and typecheck backend and frontend
	cd backend && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts
	cd frontend && npm run lint && npm run typecheck

format: ## Auto-format backend code
	cd backend && uv run ruff check --fix . ../scripts && uv run ruff format . ../scripts

requirements: ## Regenerate requirements.txt from backend/uv.lock
	uv export --project backend --frozen --no-hashes --no-emit-project --quiet -o requirements.txt

data: ## Download and verify ContractNLI into data/ (skips what is already there)
	uv run --project backend python scripts/download_data.py

data-cuad: ## Same as data, plus the optional CUAD dataset (106 MB)
	uv run --project backend python scripts/download_data.py --include-cuad

synthetic: ## Generate 500 synthetic documents + manifest into data/synthetic/ (seed 42)
	PYTHONPATH=backend uv run --project backend python -m scripts.generate_synthetic

eval: ## Run the evaluation harness (Phase 7)
	@echo "Evaluation harness not built yet (Phase 7)."
