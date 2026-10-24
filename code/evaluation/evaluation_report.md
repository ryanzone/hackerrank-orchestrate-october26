# Evaluation Report - Ghost Data Still Exists

## Summary

| | |
| --- | --- |
| Approach | Deterministic Python audit engine, no model calls at run time (see README for why, and where an LLM would help) |
| Public samples (32, development set) | status 32/32; step precision 100 %, recall 93.8 %, F1 96.8 %; exact rows 22/32 |
| Full run (518, unlabelled) | 355 INCOMPLETE / 153 COMPLETE / 10 NOT_APPLICABLE; output validates |
| Tests | 16 pytest tests on a synthetic dataset, all passing |
| Not recoverable from participant data | 92 audit-record ids; 107 ambiguous account / 51 analytics links (deliberately not guessed) |
| Least certain rows | `case_63` (`marketing_001` identity conflict), `case_45` (cited screenshot vs PDF), `case_55` (restore inferred) |
| Caveat | Sample numbers are development-set results, not a hidden-test estimate; no organizer-only data was used |

Details follow; the debugging history is at the end of section 2.

Everything below was produced by commands in this repository and can be reproduced:

```bash
python code/evaluation/evaluate.py     # metrics on the 32 labelled public samples
python code/evaluation/run_stats.py    # diagnostics on the 518-request run (no labels exist for it)
python -m pytest code/tests -q         # 16 targeted tests on a synthetic dataset
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

Two configurations are reported. **v2 (current, submitted)** never links an account or an analytics row to a case unless the
evidence leaves exactly one candidate (`ASSIGN_AMBIGUOUS = False` in `audit/linker.py`). **v1 (previous submission)** paired
ambiguous candidates deterministically. Both were measured with `python code/evaluation/evaluate.py` (flip the flag to reproduce v1).

| Metric (32 public samples) | v1 (guess ambiguous pairs) | **v2 (current)** |
| --- | --- | --- |
| Case-level `deletion_status` accuracy | 32 / 32 | **32 / 32** |
| Exact-row accuracy (status + whole `steps_json`) | 28 / 32 = 87.5 % | **22 / 32 = 68.8 %** |
| Step precision | 99.5 % | **100 %** (0 wrong steps) |
| Step recall | 97.4 % | **93.8 %** |
| Step F1 | 98.4 % | **96.8 %** (181 TP, 0 FP, 12 FN of 193) |
| Field-level accuracy overall | 97.1 % | **92.3 %** |
| Sequence accuracy (mean normalised LCS) | 95.9 % | **91.5 %** |

This is a deliberate trade, not an accuracy gain: v2 removes every step whose id could only be chosen by a coin flip, so
precision is 100 % on the samples but recall and exact rows fall. It was made because naming an account the evidence does not
support is a fabricated finding. On the full run v2 removes 158 steps from 137 cases (107 account steps and 51
anonymised-analytics steps); no status changes. These are **public-sample, development-set** numbers: I tuned on these 32
cases, so they are not a hidden-test estimate. Synthetic-test results (section 3) are separate.

### Remaining sample discrepancies (v2)

| Case(s) | Missing | Why |
| --- | --- | --- |
| `case_04` | `account_004`, `analytics_901` | Hand-built persona: no participant file ties them to the person; the worker event is far outside the timeline window. |
| `case_18`, `case_20` | `audit_record_6645`, `audit_record_5569` | These ids occur in **no** participant file (checked every CSV/log/JSON/SQL/TXT file and the PDF text). |
| `case_03`, `23`, `25`, `26`, `27`, `28`, `29` | account (`23`, `28`, `29`) and/or anonymised-analytics id (`03`, `25`-`28`) | Several cases share `reported_at` and the worker logged several accounts / anonymisations; nothing in the evidence says whose is whose, so no step is emitted. (v1 emitted a guess: right for most samples, wrong for `case_23`.) |

### Progress while debugging (exact-row matches on the 32 samples)

`8/32` first engine (identifier-keyed evidence only) -> `10/32` link a resource to a person by *resource id*, not only by
identifier (404 lookups carry none) -> `19/32` deletion-worker timeline links keyless accounts / anonymised analytics ->
`22/32` ignore stale pre-request evidence, cite only files captured at or before `audit_at`, activity-derived category for
support items -> `24/32` hand-timed persona workflows -> `28/32` cite the case's covering snapshot for timeline-only
accounts (v1) -> `22/32` v2 stops guessing ambiguous pairings. Status accuracy moved 30/32 (first engine; `case_07` and `case_26` wrong) -> 31/32 (resource-id linking fixed `case_26`)
-> 32/32 (transcribing the support-attachment PDF fixed `case_07`). The starting placeholder (every case `NOT_APPLICABLE`)
scored 0/32 on status.

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
7. **No arbitrary account assignment (`test_regressions.py`).** Two cases share `reported_at` and two worker deletions: neither case gets an account step; with one case and one event the account is linked; an account already tied to another person by identifiers is eliminated. (The first test fails if `ASSIGN_AMBIGUOUS` is turned on.)
8. **Post-cutoff evidence and active recreation (`test_regressions.py`).** A connector disabled on 09-09 and re-enabled on 09-11 is COMPLETE when audited 09-10 and an active-flow finding when audited 09-12; an older "hit" followed by a later "miss" is not reported; a resource last seen before `requested_at` is ignored; look-alike identifiers (other domain) never match while an upper-case copy does; no citation points to a file captured after `audit_at`.
9. **Output validator.** The engine's own rows pass `validate()`; a row with a wrong `step_no` is rejected.

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
* **Grounding of accounts:** 153 accounts are linked through the worker timeline because exactly one case and one candidate
  remain. 107 further cases (2-3 cases sharing 2-3 candidates) are deliberately left without an account step; v1 had paired them.
  Corroboration was searched for and not found: the chosen account has an observation in a file in only 9 of 110 ambiguous cases,
  and the ordering heuristics tried (user number, case number, request time, e-mail, file row) agree with the truth only ~50 % of the time.
* **Not recoverable:** 92 cases have no `audit_record` verification step because no participant file names the person's audit
  record (all in the evidence families that never print it); 117 cases have no account step (10 image/PDF personas and the 107 ambiguous ones).
* Citation integrity on the final file: 1,124 cited steps point to a file that holds an observation of that very resource; the other 39 are `VERIFY_RECORD_ABSENT` account steps proven by the covering snapshot; none cites a file captured after `audit_at`.
* Every non-curated case cites exactly one covering evidence file (the round's snapshot), consistent with the rounds of ten
  cases per evidence file visible in the data.

### Constants chosen from data (not hidden answers)

| Constant | Value | Where it comes from |
| --- | --- | --- |
| Account timeline window (`ACCOUNT_WINDOWS[0]`) | -1.35 h to -0.6 h before `reported_at` | Offsets of the 249 cases whose account is already tied to the person by identifiers (observed range about -1.2 h to -0.7 h, 247 of 249 consistent; the two outliers were wrong links) |
| Hand-timed window (`ACCOUNT_WINDOWS[1]`) | -0.1 h to +0.2 h | The minute-precision persona cases in the public samples |
| Analytics window | -0.62 h to -0.38 h | Observed -0.48 h offset of ANONYMIZE_RECORD SUCCESS events for the public COMPLETE samples; this is the thinnest basis (5 samples) |
| Result class vs `reported_status` | SUCCESS / PARTIAL / FAILED | Held in 247 of 249 evidence-linked cases |
| Ignore resources last seen before `requested_at` | rule | Added after `case_17` picked up the same person's March workflow; affects 3 resources in the full run |

None of these use `sample_output.csv` content; they come from evidence-linked cases and activity timestamps, and the windows are
the part most likely to need re-tuning on different data.

### Image / PDF re-audit

Every entry in `audit/transcribed.py` was re-read against its source in this pass; no transcription needed changing.
`case_45`: `evidence_37` (live Sentinel console, the latest observation) and `evidence_55` (PDF that adds the retention date) both
show `audit_record_003`. The citation stays on `evidence_37`: VERIFY_RECORD_PRESENT asks whether the record still exists and both
sources support that equally, so there is no source-based reason to switch. `case_63`: `marketing_001` appears in the screenshot
under a different name, e-mail and phone than the data-catalog link (`activity_4008`, user_09). The conflict is real and
unresolved; the resource id is the only grounded link, so the finding is kept and this is the least certain row. `backup_9287`
(`case_55`) is inferred as restorable because the console lists it as a live archive in a weekly set restored on 24-25 Apr; the
screenshot has no explicit restore flag.

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
  restricts itself to participant-facing data; v2 accepts the recall loss instead of guessing.
