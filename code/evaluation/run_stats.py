"""Unlabelled diagnostics for the full 518-request run (no gold labels are used or exist for it).

    python code/evaluation/run_stats.py

Reports how each answer was grounded, which pieces are ambiguous, and how often the audit cutoff,
absence reasoning and activity-vs-evidence disagreement mattered.  The numbers are quoted in
evaluation_report.md.
"""
import collections
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

from audit.engine import Auditor, VIOLATING  # noqa: E402
from audit.linker import _user_no  # noqa: E402,F401


def main():
    ds = os.path.join(ROOT, 'dataset')
    reqs = list(csv.DictReader(open(os.path.join(ds, 'deletion_requests.csv'), encoding='utf-8')))
    A = Auditor(ds)
    res = A.audit_all(reqs)
    out = collections.OrderedDict()
    out['cases'] = len(res)
    out['status'] = dict(collections.Counter(st for _, st, _, _ in res))
    rep = collections.Counter((c['q']['reported_status'], st) for _, st, _, c in res if st != 'NOT_APPLICABLE')
    out['reported_status_vs_audited'] = {'%s -> %s' % k: v for k, v in sorted(rep.items())}
    steps = [s for _, _, ss, _ in res for s in ss]
    out['steps_total'] = len(steps)
    out['steps_by_operation'] = dict(collections.Counter(s['operation'] for s in steps))
    out['cases_without_audit_record_step'] = sum(
        1 for _, st, ss, c in res if st in ('COMPLETE', 'INCOMPLETE') and not any(s['resource_id'].startswith('audit_record') for s in ss))
    out['cases_without_account_step'] = sum(
        1 for _, st, ss, c in res if st in ('COMPLETE', 'INCOMPLETE') and not any(s['system_id'] == 'account_service' for s in ss))
    out['account_linked_by_activity_timeline'] = sum(1 for *_, c in res if c.get('linked_by_activity'))
    amb = collections.Counter()
    for *_, c in res:
        if c.get('link_pool'):
            n_cases, n_events = c['link_pool']
            amb['unique (1 case, 1 candidate)' if (n_cases, n_events) == (1, 1) else 'ambiguous (%d cases share %d candidates)' % (n_cases, n_events)] += 1
    out['account_link_certainty'] = dict(sorted(amb.items()))
    # activity claims vs evidence
    claims = collections.Counter()
    by_rid = {}
    for _, st, ss, c in res:
        for r in c['resources']:
            by_rid[r[1]] = r
    for a in A.activity:
        if a['source_system'] != 'deletion_worker' or a['reported_result'] != 'SUCCESS':
            continue
        r = by_rid.get(a['resource_id'])
        if r and r[3] in VIOLATING and a['event_type'] in ('DELETE_RECORD', 'REDACT_RECORD', 'ANONYMIZE_RECORD'):
            claims['worker reported SUCCESS but evidence shows the record still exposed'] += 1
    out['activity_claims'] = dict(claims)
    # cutoff sensitivity
    no_cut = []
    for q in reqs:
        q2 = dict(q)
        q2['audit_at'] = '9999-12-31T00:00:00Z'
        no_cut.append(q2)
    base = {cid: (st, [(s['resource_id'], s['operation']) for s in ss]) for cid, st, ss, _ in res}
    other = {cid: (st, [(s['resource_id'], s['operation']) for s in ss]) for cid, st, ss, _ in A.audit_all(no_cut)}
    out['cases_whose_answer_changes_if_evidence_after_audit_at_were_used'] = sum(1 for c in base if base[c] != other[c])
    out['cases_whose_status_changes_if_evidence_after_audit_at_were_used'] = sum(1 for c in base if base[c][0] != other[c][0])
    # reported-status trust: how often would blindly trusting reported_status be wrong
    wrong = sum(1 for _, st, _, c in res if st in ('COMPLETE', 'INCOMPLETE')
                and ((c['q']['reported_status'] == 'COMPLETE') != (st == 'COMPLETE')))
    out['cases_where_reported_status_disagrees_with_audited_status'] = wrong
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
