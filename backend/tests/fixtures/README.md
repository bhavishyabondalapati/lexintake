# Test fixtures

Synthetic PDFs copied from `data/synthetic/` (`make synthetic`, seed 42). No real documents.

| File | What it is |
|---|---|
| `syn_00001.pdf` | Digital (has a text layer), 1 page, legal notice |
| `syn_00010.pdf` | Digital, 2 pages, NDA |
| `syn_00002.pdf` | Scanned (image only, rotated -0.9°), 1 page, side letter |
| `syn_00246.pdf` / `syn_00251.pdf` | Exact duplicates (same bytes, different names) |

`manifest.json` holds their entries from the synthetic manifest (evidence strings, sha256), so
the slow tests can check the parsed text against the ground truth.
