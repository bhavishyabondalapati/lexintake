# ContractNLI evaluation (real NDAs, real models, Batch API)

> One run. Real NDAs from the ContractNLI **test split** (CC BY 4.0). The sample is **label-stratified** (see below), so **overall accuracy is not reported**: it would mostly measure how many easy NotMentioned documents were sampled. Results are per class, with counts.

## What can and can't be scored

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

## The sample

66 NDAs from the test split's 123. **Original PDF** = the dataset's own file; **rendered** = the original is .txt/.htm, so the dataset's JSON text was rendered to a clean digital PDF (no scan noise, no original layout), so rendered results are easier than real-world files. Chosen with a fixed seed (20261103): the one Contradiction document, then every document with an Entailment label for either hypothesis, then NotMentioned documents until the budget ran out.

Small cells: the 5 original-PDF non-solicit Entailment documents are **all** such documents in the test split, so that limit is the dataset's, not the sampling's. The same holds for the single nda-16 Contradiction document. The 8 original-PDF nda-16 NotMentioned documents are a sample of 52 (the budget stopped there), so that cell is limited by our sampling.

## nda-16: Receiving Party shall destroy or return some Confidential Information upon the termination of Agreement.

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

## nda-18: Receiving Party shall not solicit some of Disclosing Party's representatives.

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

## How the pipeline behaved on real NDAs

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
