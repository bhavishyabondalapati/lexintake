# Test fixtures

Synthetic PDFs copied from `data/synthetic/` (`make synthetic`, seed 42). No real documents.

| File | What it is |
|---|---|
| `syn_00001.pdf` | Digital (has a text layer), 1 page, legal notice |
| `syn_00010.pdf` | Digital, 2 pages, NDA |
| `syn_00002.pdf` | Scanned (image only, rotated -0.9°), 1 page, side letter |
| `syn_00246.pdf` / `syn_00251.pdf` | Exact duplicates (same bytes, different names) |

`manifest.json` holds their entries from the synthetic manifest (fields, evidence strings,
sha256), so the tests can check parsed text and extracted fields against the ground truth.
`manifest_types.json` has one manifest entry per (doc_type, subtype), for the schema tests.

Hand-made synthetic NDAs for the extraction tests, built by `scripts/make_special_fixtures.py`
(fixed seeds, reproducible); their ground truth and paragraphs are in `special_fixtures.json`:

| File | What it is |
|---|---|
| `injection_nda.pdf` | Digital NDA with three planted prompt-injection attacks (re-classify and approve, a fake `</document>` + system message, a planted governing-law value) |
| `angle_brackets_nda.pdf` | Digital NDA whose return-or-destroy clause contains a literal `<notices@...>` |
