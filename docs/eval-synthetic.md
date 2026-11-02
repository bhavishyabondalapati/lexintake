# Synthetic evaluation (500-document manifest, real models, Batch API)

> One run (run `c8ec92be-2854-4980-97c6-ad166d603a8f`). The models take no `temperature`, so a rerun varies a little (see the README); small differences are noise. The documents are synthetic, generated from a manifest that is also the ground truth.

495 unique documents (500 minus exact duplicates). Split by a fixed seed (20261102) into a calibration half (247) and a test half (248). **Thresholds were chosen on the calibration half only; every number in the test-half sections is from the test half.**

## Thresholds

Calibrated against pipeline errors only (a deviation or an out-of-scope document goes to review whatever the thresholds are). Rule: the fewest reviews with no pipeline error getting through on the calibration half, ties to the higher threshold.

| | Text-layer field confidence | OCR field confidence |
|---|---|---|
| Before (the run's setting) | 0.8 | 0.9 |
| Calibrated | 0.6 | 0.9 |

On the calibration half there are 18 documents with a pipeline error out of 247. At the calibrated thresholds 18/18 (100%) of them are reviewed, with 119 reviews in all (before: 126).

## Test half: accuracy

| | Digital | Scanned | All |
|---|---|---|---|
| Document type + subtype correct | 207/207 (100%) | 41/41 (100%) | 248/248 (100%) |
| NDA recall (classified as NDA) | 89/89 (100%) | 16/16 (100%) | 105/105 (100%) |
| Field exact match | 1489/1490 (100%) | 279/292 (96%) | 1768/1782 (99%) |
| Quote verified (non-null fields) | 1253/1253 (100%) | 242/242 (100%) | 1495/1495 (100%) |
| Wrong field: model copied damaged OCR text faithfully | 0 | 6 | 6 |
| Wrong field: model returned nothing (null) | 1 | 7 | 8 |
| Wrong field: model returned a value the text does not contain | 0 | 0 | 0 |
| NDA playbook result equals the manifest (no unknowns) | 89/89 (100%) | 11/16 (69%) | 100/105 (95%) |

## Test half: routing

At the calibrated thresholds (text 0.6, OCR 0.9):

| | Digital | Scanned | All |
|---|---|---|---|
| Sent to review | 77/207 (37%) | 34/41 (83%) | 111/248 (45%) |

| Review reason | Documents |
|---|---|
| `playbook_deviation` | 48 |
| `out_of_scope` | 41 |
| `low_field_confidence` | 34 |
| `value_not_in_quote` | 13 |
| `field_missing` | 8 |
| `playbook_unknown` | 5 |

At the original thresholds (0.8 / 0.9) for comparison: 118/248 (48%) sent to review.

## Test half: is the review queue right?

The test half has 13 documents with a pipeline error and 96 that need a human. 

Calibrated thresholds:

| View | Review precision | Review recall |
|---|---|---|
| Needs a human (error, true deviation, or out of scope) | 94/111 (85%) sent had a real problem | 94/96 (98%) problem documents caught |
| Pipeline error only (wrong type or wrong field) | 11/111 (10%) sent had a real problem | 11/13 (85%) problem documents caught |

Original thresholds:

| View | Review precision | Review recall |
|---|---|---|
| Needs a human (error, true deviation, or out of scope) | 94/118 (80%) sent had a real problem | 94/96 (98%) problem documents caught |
| Pipeline error only (wrong type or wrong field) | 11/118 (9%) sent had a real problem | 11/13 (85%) problem documents caught |

## Cost (test half, Batch API prices)

Total $1.0670 for 248 documents, $0.00430 each.

| Type | Documents | Cost | Per document |
|---|---|---|---|
| investor_notice | 42 | $0.0132 | $0.00031 |
| legal_notice | 26 | $0.0085 | $0.00033 |
| nda | 105 | $1.0184 | $0.00970 |
| other | 41 | $0.0114 | $0.00028 |
| side_letter | 34 | $0.0156 | $0.00046 |

## Whole set (495 documents): accuracy, cost and truncation

Accuracy does not depend on the thresholds, so the whole set is shown too.

| | Digital | Scanned | All |
|---|---|---|---|
| Document type + subtype correct | 396/396 (100%) | 99/99 (100%) | 495/495 (100%) |
| NDA recall (classified as NDA) | 161/161 (100%) | 39/39 (100%) | 200/200 (100%) |
| Field exact match | 2836/2837 (100%) | 663/696 (95%) | 3499/3533 (99%) |
| Quote verified (non-null fields) | 2389/2389 (100%) | 570/570 (100%) | 2959/2959 (100%) |
| Wrong field: model copied damaged OCR text faithfully | 0 | 16 | 16 |
| Wrong field: model returned nothing (null) | 1 | 17 | 18 |
| Wrong field: model returned a value the text does not contain | 0 | 0 | 0 |
| NDA playbook result equals the manifest (no unknowns) | 161/161 (100%) | 30/39 (77%) | 191/200 (96%) |

Total $2.0371 for 495 documents, $0.00412 each.

| Type | Documents | Cost | Per document |
|---|---|---|---|
| investor_notice | 98 | $0.0311 | $0.00032 |
| legal_notice | 49 | $0.0171 | $0.00035 |
| nda | 200 | $1.9346 | $0.00967 |
| other | 74 | $0.0193 | $0.00026 |
| side_letter | 74 | $0.0350 | $0.00047 |

Truncated answers (hit max_tokens): side_letter: 1

<details><summary>Threshold grid on the calibration half (reviews / pipeline errors that got through)</summary>

| Text | OCR | Reviews | Escaped |
|---|---|---|---|
| 0.5 | 0.5 | 100 | 6 |
| 0.6 | 0.6 | 100 | 6 |
| 0.6 | 0.9 | 119 | 0 |
| 0.7 | 0.7 | 108 | 6 |
| 0.75 | 0.75 | 111 | 3 |
| 0.8 | 0.8 | 111 | 3 |
| 0.8 | 0.9 | 126 | 0 |
| 0.85 | 0.85 | 130 | 1 |
| 0.9 | 0.9 | 155 | 0 |
| 0.95 | 0.95 | 203 | 0 |
| 0.97 | 0.97 | 244 | 0 |
| 0.99 | 0.99 | 247 | 0 |
| 1.01 | 1.01 | 247 | 0 |

</details>
