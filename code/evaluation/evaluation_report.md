# Evaluation Report - Ghost Data Still Exists

Everything below was produced by commands in this repository and can be reproduced:

```bash
python code/evaluation/evaluate.py     # metrics on the 32 labelled public samples
python code/evaluation/run_stats.py    # diagnostics on the 518-request run (no labels exist for it)
python -m pytest code/tests -q         # 8 targeted tests on a synthetic dataset
```

## 1. How to read these numbers (important caveat)

* The only labelled data are the 32 public samples (`dataset/sample_output.csv`). I **developed against them**, so the
  numbers in section 2 are *development-set* accuracy, not a held-out estimate. They are useful as a regression suite and as
  proof that the output contract is implemented correctly; they should not be read as the expected hidden-test score.
* There are no labels for the 518 evaluation requests and I did not use organizer-only data. Section 4 therefore reports
  *consistency diagnostics* (how the answers were grounded, how many are ambiguous), not accuracy.
* The public sample contains no `NOT_APPLICABLE` row, so non-deletion handling is covered by the custom cases in section 3
  and by the 10 non-deletion requests in the evaluation file (`case_533`-`case_542`, all recognised).

## 2. Public-sample results (32 cases)

| Metric | Result |
| --- | --- |
| Case-level `deletion_status` accuracy | **32 / 32 = 100 %** (COMPLETE 10/10, INCOMPLETE 22/22) |
| Exact-row accuracy (status and the whole `steps_json`, order included) | 28 / 32 = **87.5 %** |
| Step precision / recall / F1 (7-field tuples, `step_no` excluded) | 99.5 % / 97.4 % / **98.4 %** (188 TP, 1 FP, 5 FN of 193 expected steps) |
| Field-level accuracy (expected step aligned by phase+system+resource+operation) | `phase`, `system_id`, `resource_id`, `operation`, `state`, `evidence_id`: 97.4 % each; `step_no`: 95.3 %; overall 97.1 % |
| Sequence accuracy (mean normalised LCS of `(operation, resource_id)`) | **95.9 %** |

Field accuracy counts an expected step that was not produced as wrong on every field; `step_no` is lower than the others
because one wrong/missing step shifts the numbering of the steps after it.

### Remaining sample discrepancies (all understood, none hidden)

| Case | Difference | Why |
| --- | --- | --- |
| `case_04` | missing `account_004` absent and `analytics_901` anonymised | Hand-built persona: no participant file ties `account_004` to the person and the analytics id's worker event is 12 days before `reported_at`, outside the timeline window. Not guessed. |
| `case_18`, `case_20` | missing `audit_record_6645`, `audit_record_5569` verification | These ids occur in **no** participant file (searched every CSV, log, JSON, SQL and TXT file and the PDF text; the PNG artifacts were inspected visually). A retained audit record cannot be named without inventing an id, so the step is omitted. |
| `case_23` | `account_7015` instead of `account_410` | Three NOT_STARTED cases share `reported_at` 2026-09-05T12:00 and three FAILED account deletions; evidence does not say which is whose. Resolved deterministically (user-number order), which is a coin flip. Measured ordering heuristics (user number, case number, request time, e-mail, file row) agree with the truth ~50 % of the time, i.e. carry no signal. |

### Progress while debugging (exact-row matches on the 32 samples)

`8/32` first engine (identifier-keyed evidence only) -> `10/32` link a resource to a person by *resource id*, not only by
identifier (404 lookups carry none) -> `19/32` deletion-worker timeline links keyless accounts / anonymised analytics ->
`22/32` ignore stale pre-request evidence, cite only files captured at or before `audit_at`, activity-derived category for
support items -> `24/32` hand-timed persona workflows -> `28/32` cite the case's covering snapshot for timeline-only
accounts. Status accuracy was 32/32 from the first version that read the evidence at all. The starting placeholder (every
case `NOT_APPLICABLE`) scored 0/32 on status.

## 3. Additional evaluation cases created by me (`code/tests/test_engine.py`)

All run on a tiny synthetic dataset built in a temp directory (fictional people, no real data). Each asserts the full
behaviour, not just the status.

1. **Historical e-mail + replica drift + redaction.** The request quotes the person's *old* address
   (`old.name@example.com`); the only copy of the account lives in the read replica (absent from the primary) under the
   *new* address in upper case. Expected: `INCOMPLETE`, `REPORT_REMAINING_RECORD account_11` citing the replica snapshot,
   the retained `audit_record_1` verified present and never scheduled for deletion.
2. **Audit cutoff.** The same person audited at 2026-09-10 and at 2026-09-12: the active connector and restorable backup
   that only appear in a log entry of 2026-09-11 must be absent from the first answer and present in the second.
3. **Active sync + restore risk + ordering.** Remediation must start with `DISABLE_DATA_FLOW`, then `BLOCK_RESTORE`,
   and only then `DELETE_RECORD`; the support message is `REDACT_RECORD`; verification mirrors it and every future step has an
   empty `evidence_id`.
4. **Tombstone and retention.** A tombstone still holding name/e-mail -> `REPORT_EXPOSED_PII` + `REDACT_RECORD`
   + `VERIFY_RECORD_REDACTED`. A second person with an already-redacted tombstone, an active legal hold and an audit record
   -> `COMPLETE`, verification only, retained records verified *present*, all steps citing the covering bundle.
5. **Non-deletion requests.** Contact-detail change, marketing-only opt-out, information request ("not a request to erase it")
   and "can't access ... do not close the account" -> `NOT_APPLICABLE` with `[]`; four deletion requests in
   Spanish, French, Japanese and Korean are recognised.
6. **Keyless account via the deletion timeline.** A 404 lookup carries no identifier; the worker's `DELETE_RECORD` event one
   hour before `reported_at` links `account_22` to the case.
7. **Output validator.** The engine's own rows pass `validate()`; a row with a wrong `step_no` is rejected.

## 4. Full run (518 requests) - diagnostics, not accuracy

From `run_stats.py` on the final `output.csv`:

* Status: **355 INCOMPLETE, 153 COMPLETE, 10 NOT_APPLICABLE**; 3,254 steps; `validate()` passes (518 unique sorted rows,
  consecutive `step_no`, allowed triples, empty `evidence_id` on future steps, no citation captured after `audit_at`).
* **Reported status is not proof:** it disagrees with the audited result in 225 of 508 deletion cases (e.g. 163 reported
  `COMPLETE` are `INCOMPLETE`, 62 reported `NOT_STARTED`/`PARTIALLY_COMPLETE` are `COMPLETE`).
* **Worker "SUCCESS" is not proof:** in 69 cases the deletion worker logged SUCCESS for a record the evidence still shows
  exposed. Conversely every `CHECK_DATA_FLOW` / `CHECK_RESTORE_ACCESS` event (117) matches a flow or backup that the evidence
  shows as active, and every `BLOCK_RESTORE` / `DISABLE_DATA_FLOW` SUCCESS (72) matches a blocked / disabled one - an independent
  cross-check of the parsers.
* **Audit cutoff matters:** using evidence after `audit_at` would change the answer of 69 cases (22 would flip status).
* **Grounding of accounts:** 260 accounts were linked through the worker timeline: 153 unambiguously (one case, one
  candidate) and 107 ambiguously (2-3 cases share 2-3 candidates, so only about half of these are expected to be right).
* **Not recoverable:** 92 cases have no `audit_record` verification step because no participant file names the person's audit
  record (all in the evidence families that never print it); 10 cases (the image/PDF personas) have no account step.
* Every non-curated case cites exactly one covering evidence file (the round's snapshot), consistent with the rounds of ten
  cases per evidence file visible in the data.

### Judgement calls worth knowing about (could be wrong)

* The 12 image/PDF persona cases rely on one-off transcriptions (`audit/transcribed.py`). `case_45` (retained audit record)
  is cited to the Sentinel screenshot `evidence_37`; the PDF extract `evidence_55` also proves it (it adds the retention date).
  `case_63`: the marketing screenshot shows a different display name than the data-catalog link; the resource id is treated as the
  grounded link.
* A resource whose last observation is older than `requested_at` is treated as belonging to an earlier workflow and ignored
  (`case_17` / `case_46` are the same person in March and August; `case_34` has 09-15 log lines older than the 09-24 request).
* `legal_hold_*` records are verified present under `legal_service`; backups are findings only when restore is allowed
  (the rule retains the archive, so no deletion is ever proposed for it).

## 5. Failures found and improvements made

| Failure found | Improvement |
| --- | --- |
| Starting point was a placeholder marking every case `NOT_APPLICABLE` | Real engine; 0/32 -> 32/32 status accuracy on the samples |
| The first-draft authorization regexes (earlier session) flagged 131 of 550 requests as non-deletion although only 10 are (many non-English and "close/remove my account" phrasings) | Multilingual keyword rules + negation stripping; exactly the 10 genuine non-deletion requests are `NOT_APPLICABLE`, deletion audits without the word "delete" (`case_06/25/26`) stay authorised |
| 404 lookups and anonymised analytics rows carry no identifier, so records were missed | Resource-id linking, then deletion-worker timeline linking with elimination |
| Citing files captured after `audit_at` (bundles whose internal snapshot is earlier than the file) | Separate rules: state from observation time, citation only from files with `captured_at <= audit_at` |
| Support items typed wrongly (attachments looked like messages in SQL) | Treatment the worker applied (DELETE vs REDACT, DELETE vs ANONYMIZE) decides the data category |
| Stale snapshot said "present" after the record had been deleted | Absence reasoning from covering snapshots |
| A March workflow of the same person polluted an August case | Ignore resources last seen before `requested_at` |
| Timeline-only accounts cited an unrelated snapshot | Cite the case's covering snapshot (one file per case, as in every labelled example) |

## 6. Limitations

* No LLM is used, so unusual phrasing of a request that is not covered by the keyword rules would fall back to "treat as an
  audit of deletion state"; all 518 requests in the dataset are classified without ambiguity.
* Transcription of images/PDFs is manual and static.
* Ambiguous timeline pairings and unrecoverable ids (section 4) cap the achievable step recall below 100 % on any solver that
  restricts itself to participant-facing data.
