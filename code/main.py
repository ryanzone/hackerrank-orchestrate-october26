"""Ghost Data Still Exists - deletion audit agent.

    python code/main.py                      # reads dataset/, writes output.csv at the repo root
    python code/main.py --requests dataset/sample_deletion_requests.csv --output sample_out.csv

Everything is deterministic: identity resolution, evidence reconstruction at audit_at, policy
application, step ordering and validation run in plain Python.  No model API is called.
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from audit.engine import Auditor  # noqa: E402
from audit.validate import validate  # noqa: E402

FIELD_ORDER = ['step_no', 'phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id']


def serialize(steps):
    return json.dumps([{k: s[k] for k in FIELD_ORDER} for s in steps], separators=(',', ':'), ensure_ascii=False)


def run(dataset_dir, requests_path, output_path, check=True, verbose=True):
    requests = list(csv.DictReader(open(requests_path, encoding='utf-8')))
    auditor = Auditor(dataset_dir)
    results = auditor.audit_all(requests)
    rows = sorted(((cid, st, steps) for cid, st, steps, _ in results), key=lambda r: r[0])
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['case_id', 'deletion_status', 'steps_json'])
        for cid, st, steps in rows:
            w.writerow([cid, st, serialize(steps)])
    summary = {}
    for _, st, _ in rows:
        summary[st] = summary.get(st, 0) + 1
    if verbose:
        print('wrote %s: %d rows %s' % (output_path, len(rows), summary))
    if check:
        audit_at = {q['case_id']: q['audit_at'] for q in requests}
        captured = {fid: m['captured_at'] for fid, m in auditor.files.items()}
        activity_ids = {a['activity_id'] for a in auditor.activity}
        problems = validate(output_path, requests_path, set(auditor.files), captured, audit_at, activity_ids)
        if problems:
            print('VALIDATION PROBLEMS (%d):' % len(problems))
            for p in problems[:50]:
                print('  ', p)
            return 2
        if verbose:
            print('validation passed')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dataset', default=os.path.join(ROOT, 'dataset'))
    ap.add_argument('--requests', default=None, help='defaults to <dataset>/deletion_requests.csv')
    ap.add_argument('--output', default=os.path.join(ROOT, 'output.csv'))
    ap.add_argument('--no-validate', action='store_true')
    a = ap.parse_args(argv)
    req = a.requests or os.path.join(a.dataset, 'deletion_requests.csv')
    return run(a.dataset, req, a.output, check=not a.no_validate)


if __name__ == '__main__':
    sys.exit(main())
