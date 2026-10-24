"""Score the engine against the public labelled sample (dataset/sample_output.csv).

    python code/evaluation/evaluate.py              # prints the metrics used in evaluation_report.md

Metrics
  * status accuracy        - deletion_status equal per case
  * exact-row accuracy     - status AND the whole steps array equal (order included)
  * step precision/recall  - steps compared as full 7-field tuples (step_no excluded, order ignored)
  * field accuracy         - every expected step is aligned to a predicted step by
                             (phase, system_id, resource_id, operation); each of the 7 fields of the
                             expected step counts as correct when the aligned step agrees (an
                             unaligned expected step scores 0 on all 7 fields)
  * sequence accuracy      - normalised longest-common-subsequence of (operation, resource) pairs
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

from audit.engine import Auditor  # noqa: E402

FIELDS = ['step_no', 'phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id']


def lcs(a, b):
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a)):
        for j in range(len(b)):
            dp[i + 1][j + 1] = dp[i][j] + 1 if a[i] == b[j] else max(dp[i][j + 1], dp[i + 1][j])
    return dp[-1][-1]


def evaluate(pred, gold):
    """pred, gold: {case_id: (status, steps)} -> metrics dict."""
    n = len(gold)
    status_ok = exact = 0
    tp = fp = fn = 0
    field_hits = {f: 0 for f in FIELDS}
    field_total = 0
    seq_scores = []
    per_status = {}
    for cid, (gst, gsteps) in gold.items():
        pst, psteps = pred.get(cid, ('MISSING', []))
        status_ok += pst == gst
        per_status.setdefault(gst, [0, 0])
        per_status[gst][1] += 1
        per_status[gst][0] += pst == gst
        key = lambda s: (s['phase'], s['system_id'], s['resource_id'], s['operation'], s['state'], s['evidence_id'])
        exact += (pst == gst and [key(s) for s in psteps] == [key(s) for s in gsteps])
        P, G = {key(s) for s in psteps}, {key(s) for s in gsteps}
        tp += len(P & G)
        fp += len(P - G)
        fn += len(G - P)
        by_key = {(s['phase'], s['system_id'], s['resource_id'], s['operation']): s for s in psteps}
        for g in gsteps:
            field_total += 1
            p = by_key.get((g['phase'], g['system_id'], g['resource_id'], g['operation']))
            if p:
                for f in FIELDS:
                    field_hits[f] += p[f] == g[f]
        gs = [(s['operation'], s['resource_id']) for s in gsteps]
        ps = [(s['operation'], s['resource_id']) for s in psteps]
        seq_scores.append(1.0 if not gs and not ps else lcs(gs, ps) / max(len(gs), len(ps)))
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    return {
        'cases': n,
        'status_accuracy': status_ok / n,
        'status_by_class': {k: (v[0], v[1]) for k, v in sorted(per_status.items())},
        'exact_row_accuracy': exact / n,
        'step_precision': prec, 'step_recall': rec,
        'step_f1': 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
        'steps': {'tp': tp, 'fp': fp, 'fn': fn},
        'field_accuracy': {f: field_hits[f] / field_total for f in FIELDS},
        'field_accuracy_overall': sum(field_hits.values()) / (field_total * len(FIELDS)) if field_total else 1.0,
        'sequence_accuracy_mean_lcs': sum(seq_scores) / len(seq_scores),
    }


def load_gold(path):
    return {r['case_id']: (r['deletion_status'], json.loads(r['steps_json']))
            for r in csv.DictReader(open(path, encoding='utf-8'))}


def predict(dataset_dir, requests_path):
    requests = list(csv.DictReader(open(requests_path, encoding='utf-8')))
    auditor = Auditor(dataset_dir)
    return {cid: (st, steps) for cid, st, steps, _ in auditor.audit_all(requests)}


def main():
    ds = os.path.join(ROOT, 'dataset')
    pred = predict(ds, os.path.join(ds, 'sample_deletion_requests.csv'))
    gold = load_gold(os.path.join(ds, 'sample_output.csv'))
    m = evaluate(pred, gold)
    print(json.dumps(m, indent=2))
    bad = [c for c in gold if pred[c][0] != gold[c][0]]
    print('status mismatches:', bad)
    return 0


if __name__ == '__main__':
    sys.exit(main())
