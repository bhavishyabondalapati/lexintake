.PHONY: dev down install test test-slow smoke requeue lint format requirements data data-cuad synthetic \
	parse-quality ingest-smoke extract extract-preview eval

dev: ## Start the whole stack (db, redis, migrate, api, watcher, worker, extractor, frontend)
	mkdir -p inbox
	docker compose up --build

down: ## Stop the stack (add -v to the compose command to also wipe the database)
	docker compose down

install: ## Install backend and frontend dependencies locally
	cd backend && uv sync
	cd frontend && npm ci

test: ## Run the fast tests (starts the Postgres container; slow Docling tests excluded)
	docker compose up -d --wait db
	cd backend && uv run pytest

test-slow: ## Run the slow tests with real Docling, inside the worker image
	docker compose --profile test run --rm --build worker-test

smoke: ## Upload a PDF to the running stack and wait until it is parsed
	python3 scripts/compose_smoke.py

requeue: ## Queue parsing again for documents stuck in received (STATUS=received,failed to widen)
	docker compose exec api python -m app.cli requeue --status $${STATUS:-received}

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

parse-quality: ## Parse the synthetic set; report evidence hit rates and the deskew experiment
	PYTHONPATH=backend uv run --project backend --group parse python -m scripts.parse_quality

ingest-smoke: ## Send 20 ContractNLI PDFs through the running stack and report crashes
	PYTHONPATH=backend uv run --project backend python -m scripts.ingest_smoke

extract: ## Classify + extract parsed documents. CALLS THE ANTHROPIC API (LIMIT=20, MAX_SPEND=...)
	docker compose exec api python -m app.cli extract --limit $${LIMIT:-20} \
		$${MAX_SPEND:+--max-spend $$MAX_SPEND}

extract-preview: ## 20 synthetic docs through the stack + preview report. CALLS THE API (MAX_SPEND=1.00)
	PYTHONPATH=backend uv run --project backend python -m scripts.extract_preview \
		--max-spend $${MAX_SPEND:-1.00}

eval: ## Run the evaluation harness (Phase 7)
	@echo "Evaluation harness not built yet (Phase 7)."
