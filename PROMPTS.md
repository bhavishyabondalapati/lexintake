# Prompts to paste into Claude Code, one phase at a time

Start each phase in plan mode (Shift+Tab), read the plan, then let it build.
Run `/clear` between phases — CLAUDE.md keeps the context. Commit after each phase.

---

## Phase 0 — Scaffold
Read CLAUDE.md. Scaffold the repo: backend (FastAPI, uv), frontend (Next.js + TS + Tailwind),
docker-compose with Postgres+pgvector and Redis, Celery worker, Makefile with `dev`, `test`, `eval`,
`.env.example`, .gitignore (including data/ and .env), pytest setup, and a GitHub Actions CI
that runs lint + tests. Add a health-check endpoint and one passing test. Show me the plan first.

## Phase 1 — Data
Write `scripts/download_data.py` to fetch ContractNLI (and optionally CUAD) into data/.
Write `scripts/generate_synthetic.py` that produces N documents (default 500) across types:
NDA, side letter, investor notice, legal notice/claim, other. Some should be scanned-looking PDFs
(rasterized, slight rotation, noise). Write a manifest (CSV/JSON) with ground-truth type and key
fields for each synthetic doc so we can evaluate later.

## Phase 2 — Ingestion & parsing
Build the upload endpoint and a folder watcher that enqueue jobs. Dedupe by SHA-256.
Celery task parses with Docling, uses OCR when a page has no text layer, and stores text blocks
with page number and bounding box. Status moves received → parsed. Add the audit log table and
write an entry for every state change. Tests: digital PDF, scanned PDF, duplicate upload.

## Phase 3 — Classification & extraction
Define Pydantic schemas for document type and NDA fields (see CLAUDE.md). Implement the
`Extractor` interface with an Anthropic implementation and a fake for tests. Every field must
include source page, quoted span, and confidence. Validate output; on invalid JSON retry once,
then mark needs_review. Record tokens and cost per document. Retry API errors with backoff.

## Phase 4 — Playbook check
Create `playbook/nda_playbook.yaml` with realistic rules (e.g. max term, governing law allowed
list, non-solicit allowed or not, residuals clause disallowed, must include return/destroy).
Implement the checker: each rule → pass / deviation / unknown, with evidence. Any deviation or
unknown routes to needs_review; all-pass + high confidence → auto_approved.

## Phase 5 — Routing, filing, storage adapter
Implement `StorageAdapter` with `LocalFolderStorage` (files into folders by type/counterparty/year)
and `SharePointStorage` using Microsoft Graph (clearly documented; may be a stub behind a flag).
Approved docs get filed. Add review endpoints: list queue, get doc with evidence, approve, override
field values (overrides logged in audit).

## Phase 6 — Review dashboard
Next.js dashboard: queue with filters (status, type, flags), document page showing the PDF with
highlighted evidence spans next to extracted fields and playbook results, approve/override buttons,
and a stats page (volume, auto-approve rate, avg processing time, cost). Playwright tests for the
main review flow. Make it look like a professional internal tool, not a generic template.

## Phase 7 — Evaluation harness
`make eval` runs the pipeline on ContractNLI and the synthetic manifest and reports: classification
accuracy, per-field extraction precision/recall, clause-detection F1 vs ContractNLI labels,
needs_review rate, avg latency, cost per doc. Save results as markdown + JSON and add a summary
table to the README.

## Phase 8 — Scale
Load test with a few thousand synthetic docs. Add worker concurrency settings, batch processing
for extraction where it makes sense, backpressure on the queue, and a throughput report
(docs/hour, cost per 1,000 docs). Document in the README how this scales to hundreds of GB.

## Phase 9 — Polish
README pass: problem statement, Mermaid architecture diagram, screenshots, eval table, setup in
under 5 commands, design decisions and limitations. Remove dead code, make sure CI is green.
