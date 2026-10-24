# Usage Report - final full-dataset run

This report covers the run that produced the submitted `output.csv`
(`python code/main.py`, 518 requests from `dataset/deletion_requests.csv`).

## Summary

| Item | Value |
| --- | --- |
| Model providers / model names used by the solution at run time | **None** |
| Model API calls | **0** |
| Input tokens | **0** |
| Output tokens | **0** |
| Total tokens | **0** (average 0 per request) |
| Estimated cost | **USD 0.00** total, **USD 0.00** per request |
| Network access during the run | none |
| Secrets / API keys required | none |

The audit is fully deterministic: identity resolution, evidence parsing (SQLite for the SQL snapshots, JSON/log parsers),
state reconstruction at `audit_at`, policy application, step ordering and output validation are plain Python. There is no
prompt, no model configuration and no cache to report. The full run takes about one second on the development laptop (Windows 11, Python 3.13).

## Per-model and overall totals

| Model | Calls | Input tokens | Output tokens | Cost |
| --- | ---: | ---: | ---: | ---: |
| (no model used at run time) | 0 | 0 | 0 | 0.00 |
| **Overall** | **0** | **0** | **0** | **0.00** |

## Development-time model use (not part of the run above)

* The code was written interactively with the Claude Code harness (model `claude-sonnet-5-5`). The harness does not export
  per-request token counts into the repository, so **no token or cost figure is available for development**; I am not
  estimating one.
* The same assistant read the 11 PNG screenshots and 4 PDFs once (vision / `pdftotext`) because no OCR engine is available
  offline. The extracted facts are stored as data in `code/audit/transcribed.py`, so the final run does not repeat any model call.
* No evaluation data, organizer-only file or golden label was sent to or used by any model.
