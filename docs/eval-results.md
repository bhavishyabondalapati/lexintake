# Evaluation results

Two separate evaluations. **Do not mix them up:**

- **Synthetic** (500 generated documents, ground truth = the generator's manifest): the pipeline is
  nearly perfect on clean documents and the interesting errors are all on scanned pages.
- **ContractNLI** (real NDAs, public dataset): only two fields can be scored, only as
  presence/absence of a clause, on a label-stratified sample. The pipeline looks much worse here, and
  most of the gap is **no answer** (documents sent to review before extraction) and the
  duration-vs-presence mismatch, not wrong answers. Read the caveats.

Both are single runs through the Batch API. The models take no `temperature`, so a rerun differs a
little (4 of 20 documents differed between two runs of the same sample).

**Spend.** Synthetic 500: $2.0371. ContractNLI (66 documents): $0.5347. A 20-document smoke batch:
$0.0816. Phase total $2.65 of the $3.50 budget. The whole project, including the Phase 3 preview
runs, is $2.92 of recorded API cost.

---

# Part 1

> One run (run `c8ec92be-2854-4980-97c6-ad166d603a8f`). The models take no `temperature`, so a rerun varies a little (see the README); small differences are noise. The documents are synthetic, generated from a manifest that is also the ground truth.

495 unique documents (500 minus exact duplicates). Split by a fixed seed (20261102) into a calibration half (247) and a test half (248). **Thresholds were chosen on the calibration half only; every number in the test-half sections is from the test half.**

### Thresholds

Calibrated against pipeline errors only (a deviation or an out-of-scope document goes to review whatever the thresholds are). Rule: the fewest reviews with no pipeline error getting through on the calibration half, ties to the higher threshold.

| | Text-layer field confidence | OCR field confidence |
|---|---|---|
| Before (the run's setting) | 0.8 | 0.9 |
| Calibrated | 0.6 | 0.9 |

On the calibration half there are 18 documents with a pipeline error out of 247. At the calibrated thresholds 18/18 (100%) of them are reviewed, with 119 reviews in all (before: 126).

### Test half: accuracy

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

### Test half: routing

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

### Test half: is the review queue right?

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

### Cost (test half, Batch API prices)

Total $1.0670 for 248 documents, $0.00430 each.

| Type | Documents | Cost | Per document |
|---|---|---|---|
| investor_notice | 42 | $0.0132 | $0.00031 |
| legal_notice | 26 | $0.0085 | $0.00033 |
| nda | 105 | $1.0184 | $0.00970 |
| other | 41 | $0.0114 | $0.00028 |
| side_letter | 34 | $0.0156 | $0.00046 |

### Whole set (495 documents): accuracy, cost and truncation

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

---

# Part 2

> One run. Real NDAs from the ContractNLI **test split** (CC BY 4.0). The sample is **label-stratified** (see below), so **overall accuracy is not reported**: it would mostly measure how many easy NotMentioned documents were sampled. Results are per class, with counts.

### What can and can't be scored

ContractNLI labels each NDA against 17 hypotheses as Entailment (the contract says it), Contradiction (it says the opposite) or NotMentioned. Our pipeline extracts 10 NDA fields, mostly values (a counterparty, a date, a number of months). Only two fields correspond to a hypothesis, and only as **presence vs absence of the clause**:

| Hypothesis (as quoted in the dataset) | Our field | Expected for Entailment | Expected for NotMentioned / Contradiction |
|---|---|---|---|
| nda-16: "Receiving Party shall destroy or return some Confidential Information upon the termination of Agreement." | `return_destroy_days` | not null (a return/destroy obligation is present) | null |
| nda-18: "Receiving Party shall not solicit some of Disclosing Party's representatives." | `non_solicit_months` | not null (a non-solicitation clause is present) | null |

Caveat: `return_destroy_days` and `non_solicit_months` are *durations*. An NDA that has a return/destroy duty with no deadline, or a non-solicit with no stated period, can come back null, which counts as a miss here. Recall is therefore a lower bound on "finds the clause".

Not scored, with the reason:

| Hypothesis | Why not scored |
|---|---|
| nda-1: "All Confidential Information shall be expressly identified by the Disclosing Party." | no field in the pipeline captures it |
| nda-2: "Confidential Information shall only include technical information." | no field in the pipeline captures it |
| nda-3: "Confidential Information may include verbally conveyed information." | no field in the pipeline captures it |
| nda-4: "Receiving Party shall not use any Confidential Information for any purpose other than the purposes stated in Agreement." | no field in the pipeline captures it |
| nda-5: "Receiving Party may share some Confidential Information with some of Receiving Party's employees." | no field in the pipeline captures it |
| nda-7: "Receiving Party may share some Confidential Information with some third-parties (including consultants, agents and professional advisors)." | no field in the pipeline captures it |
| nda-8: "Receiving Party shall notify Disclosing Party in case Receiving Party is required by law, regulation or judicial process to disclose any Confidential Information." | no field in the pipeline captures it |
| nda-10: "Receiving Party shall not disclose the fact that Agreement was agreed or negotiated." | no field in the pipeline captures it |
| nda-11: "Receiving Party shall not reverse engineer any objects which embody Disclosing Party's Confidential Information." | no field in the pipeline captures it |
| nda-12: "Receiving Party may independently develop information similar to Confidential Information." | no field in the pipeline captures it |
| nda-13: "Receiving Party may acquire information similar to Confidential Information from a third party." | no field in the pipeline captures it |
| nda-15: "Agreement shall not grant Receiving Party any right to Confidential Information." | no field in the pipeline captures it |
| nda-17: "Receiving Party may create a copy of some Confidential Information in some circumstances." | no field in the pipeline captures it |
| nda-19: "Some obligations of Agreement may survive termination of Agreement." | closest field is `term_months`, but a number of months can't be compared with 'some obligations may survive termination' |
| nda-20: "Receiving Party may retain some Confidential Information even after the return or destruction of Confidential Information." | closest field is `residuals_clause`, but retaining information after return or destruction is a different legal concept from a residuals clause (use of what is remembered) |

Fields with no ContractNLI label at all: counterparty, fund party, effective date, mutual, term, governing law, non-compete, residuals. They can't be scored on this dataset (the synthetic evaluation covers them).

### The sample

66 NDAs from the test split's 123. **Original PDF** = the dataset's own file; **rendered** = the original is .txt/.htm, so the dataset's JSON text was rendered to a clean digital PDF (no scan noise, no original layout), so rendered results are easier than real-world files. Chosen with a fixed seed (20261103): the one Contradiction document, then every document with an Entailment label for either hypothesis, then NotMentioned documents until the budget ran out.

Small cells: the 5 original-PDF non-solicit Entailment documents are **all** such documents in the test split, so that limit is the dataset's, not the sampling's. The same holds for the single nda-16 Contradiction document. The 8 original-PDF nda-16 NotMentioned documents are a sample of 52 (the budget stopped there), so that cell is limited by our sampling.

### nda-16: Receiving Party shall destroy or return some Confidential Information upon the termination of Agreement.

Field `return_destroy_days`.

| Class | Measure | Original PDF | Rendered |
|---|---|---|---|
| Entailment | value filled in (recall) | 4/24 (17%), 10 not extracted | 1/13 (8%), 5 not extracted |
| Entailment | filled in, or flagged as mentioned | 9/24 (38%), 10 not extracted | 6/13 (46%), 5 not extracted |
| NotMentioned | value filled in (false alarm) | 1/8 (12%), 2 not extracted | 2/20 (10%), 5 not extracted |
| NotMentioned | filled in, or flagged as mentioned | 2/8 (25%), 2 not extracted | 11/20 (55%), 5 not extracted |
| Contradiction | value filled in (expected: no) | no documents | 0/1 (0%), 1 not extracted |
| Contradiction | filled in, or flagged as mentioned | no documents | 0/1 (0%), 1 not extracted |

"Not extracted": the pipeline gave no field answer for that document (the type was in doubt or the answer was cut off), so it counts as a miss for Entailment and is not counted as a false alarm. "Flagged as mentioned": the model left the field empty but a keyword scan found the clause's wording in the text, so the document went to review with `absent_but_mentioned`.

### nda-18: Receiving Party shall not solicit some of Disclosing Party's representatives.

Field `non_solicit_months`.

| Class | Measure | Original PDF | Rendered |
|---|---|---|---|
| Entailment | value filled in (recall) | 4/5 (80%), 1 not extracted | 16/23 (70%), 7 not extracted |
| Entailment | filled in, or flagged as mentioned | 4/5 (80%), 1 not extracted | 16/23 (70%), 7 not extracted |
| NotMentioned | value filled in (false alarm) | 0/27 (0%), 11 not extracted | 0/11 (0%), 4 not extracted |
| NotMentioned | filled in, or flagged as mentioned | 0/27 (0%), 11 not extracted | 0/11 (0%), 4 not extracted |
| Contradiction | value filled in (expected: no) | no documents | no documents |
| Contradiction | filled in, or flagged as mentioned | no documents | no documents |

"Not extracted": the pipeline gave no field answer for that document (the type was in doubt or the answer was cut off), so it counts as a miss for Entailment and is not counted as a false alarm. "Flagged as mentioned": the model left the field empty but a keyword scan found the clause's wording in the text, so the document went to review with `absent_but_mentioned`.

### How the pipeline behaved on real NDAs

| | Original PDF | Rendered |
|---|---|---|
| Classified as NDA | 27/32 (84%) | 29/34 (85%) |
| Auto-approved | 0/32 (0%) | 0/34 (0%) |
| Sent to review | 32/32 (100%) | 34/34 (100%) |
| Cost per document (batch) | $0.0068 | $0.0093 |
| Truncated answers | 5 | 4 |

Review reasons (documents):

- `playbook_deviation`: 42
- `field_missing`: 37
- `playbook_unknown`: 33
- `low_field_confidence`: 32
- `absent_but_mentioned`: 20
- `low_classification_confidence`: 13
- `value_not_in_quote`: 9
- `truncated`: 9
- `invalid_output`: 1

Total cost: $0.5347 for 66 documents.
