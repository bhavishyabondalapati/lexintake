# LexIntake — Legal Document Intake & Triage for a PE Legal Team

## What this is
An automated intake pipeline for the legal team at a mid-market private equity firm (modeled on a firm like H.I.G. Capital: thousands of deals, constant inflow of NDAs, confidentiality agreements, investor documents and notices, ~hundreds of GB per year).

Documents arrive by email attachment, scanned mail, or a SharePoint drop. Today lawyers read each one, decide what it is, check it against their standards, file it, and track it by hand. LexIntake does the first pass so lawyers only review what's flagged.

This is an independent portfolio project. It is NOT affiliated with any firm and uses only public and synthetic data. Never add real confidential documents.

## Pipeline

1. Ingest — upload API + folder watcher → job queue. Dedupe by SHA-256.
2. Parse — Docling for layout-aware parsing; OCR fallback for scanned pages. Keep page numbers and bounding boxes for every text block.
3. Classify — NDA / side letter / investor notice / claim or legal notice / other.
4. Extract — structured fields (counterparty, effective date, term, governing law, non-solicit, non-compete, residuals clause, return/destroy obligations, deadlines). Every extracted field cites its source page + text span and carries a confidence.
5. Playbook check — compare extracted terms against `playbook/nda_playbook.yaml`; flag deviations with evidence.
6. Route & file — status workflow (received → parsed → extracted → auto-approved | needs_review → filed), filed through a storage adapter, every step in an audit log.
7. Review UI — queue, document view with highlighted evidence, approve/override.

## Stack

* Python 3.12, FastAPI, Pydantic v2, uv for dependency management
* Postgres 16 + pgvector (metadata, audit log, embeddings in one DB)
* Redis + Celery for background jobs
* Docling for parsing/OCR
* Anthropic Python SDK for classification + extraction (model name from env var `ANTHROPIC_MODEL`, never hardcoded); use structured/JSON outputs validated by Pydantic
* Next.js + TypeScript + Tailwind for the review dashboard
* Docker Compose for local dev; GitHub Actions for CI
* pytest (backend), Playwright (frontend E2E)

## Architecture rules

* Storage is behind a `StorageAdapter` interface: `LocalFolderStorage` (default) and `SharePointStorage` (Microsoft Graph API; may be a documented stub). Nothing outside the adapter knows which one is in use.
* The LLM is behind an `Extractor` interface so it can be swapped or mocked in tests.
* Every pipeline step is idempotent and retryable (safe to re-run on the same document).
* Low-confidence or out-of-scope results go to `needs_review`. Never silently guess.
* Track token usage and cost per document in the DB.

## Data

* `data/contractnli/` — ContractNLI (real NDAs with clause labels). Primary eval set.
* `data/cuad/` — CUAD commercial contracts (optional, extra clause types).
* `data/synthetic/` — generated documents, including scanned-looking PDFs (rendered to image, skewed, noised) and non-NDA types, to simulate mailroom volume.
* Raw data is gitignored. Scripts in `scripts/` download/generate it reproducibly. Check and note each dataset's license in the README.

## Working rules for Claude Code

* Work one phase at a time (see PROMPTS.md). Propose a plan before writing code.
* Write tests alongside code. A phase is done only when tests pass and `docker compose up` works.
* Small, focused commits with clear messages. Do NOT add Claude as co-author or add any "Generated with Claude" trailer to commits, PRs, or the README.
* No secrets in git. Use `.env` (gitignored) and keep `.env.example` up to date.
* Keep the README current: problem, architecture diagram (Mermaid), setup, eval results.
* If something is ambiguous, ask instead of guessing.

## Commands (keep this section updated as they're created)

* `make dev` — start everything
* `make test` — run all tests
* `make eval` — run the evaluation harness
