"""Structural validation of output.csv against the submission contract."""
import csv
import json

PHASES = ['FINDING', 'REMEDIATION', 'VERIFICATION']
ALLOWED = {
    'REPORT_REMAINING_RECORD': ('FINDING', 'PRESENT'),
    'REPORT_EXPOSED_PII': ('FINDING', 'PRESENT'),
    'REPORT_ACTIVE_DATA_FLOW': ('FINDING', 'ACTIVE'),
    'REPORT_RESTORE_RISK': ('FINDING', 'ACTIVE'),
    'DISABLE_DATA_FLOW': ('REMEDIATION', 'DISABLED'),
    'BLOCK_RESTORE': ('REMEDIATION', 'BLOCKED'),
    'DELETE_RECORD': ('REMEDIATION', 'ABSENT'),
    'REDACT_RECORD': ('REMEDIATION', 'REDACTED'),
    'ANONYMIZE_RECORD': ('REMEDIATION', 'ANONYMIZED'),
    'VERIFY_RECORD_ABSENT': ('VERIFICATION', 'ABSENT'),
    'VERIFY_RECORD_PRESENT': ('VERIFICATION', 'PRESENT'),
    'VERIFY_RECORD_REDACTED': ('VERIFICATION', 'REDACTED'),
    'VERIFY_RECORD_ANONYMIZED': ('VERIFICATION', 'ANONYMIZED'),
    'VERIFY_DATA_FLOW_DISABLED': ('VERIFICATION', 'DISABLED'),
    'VERIFY_RESTORE_BLOCKED': ('VERIFICATION', 'BLOCKED'),
}
FIELDS = ['step_no', 'phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id']
STATUSES = ('COMPLETE', 'INCOMPLETE', 'NOT_APPLICABLE')


def validate(output_path, requests_path, evidence_ids, captured, audit_at_of, activity_ids=(), audit_ids=()):
    """Return a list of problems (empty when the file is valid)."""
    problems = []
    rows = list(csv.DictReader(open(output_path, encoding='utf-8', newline='')))
    header = open(output_path, encoding='utf-8').readline().strip()
    if header != 'case_id,deletion_status,steps_json':
        problems.append('bad header: %s' % header)
    expected = [r['case_id'] for r in csv.DictReader(open(requests_path, encoding='utf-8'))]
    ids = [r['case_id'] for r in rows]
    if len(rows) != len(expected):
        problems.append('row count %d != %d' % (len(rows), len(expected)))
    if sorted(ids) != ids:
        problems.append('rows are not sorted by case_id')
    if set(ids) != set(expected) or len(set(ids)) != len(ids):
        problems.append('case ids do not match the request file exactly once each')
    for r in rows:
        cid, st = r['case_id'], r['deletion_status']
        if st not in STATUSES:
            problems.append('%s: bad status %s' % (cid, st))
        try:
            steps = json.loads(r['steps_json'])
        except ValueError:
            problems.append('%s: steps_json is not JSON' % cid)
            continue
        if not isinstance(steps, list):
            problems.append('%s: steps_json is not an array' % cid)
            continue
        if st == 'NOT_APPLICABLE' and steps:
            problems.append('%s: NOT_APPLICABLE must have []' % cid)
        last_phase = -1
        phases_seen = set()
        for i, s in enumerate(steps, 1):
            if sorted(s.keys()) != sorted(FIELDS):
                problems.append('%s step %d: wrong fields' % (cid, i))
                continue
            if s['step_no'] != i:
                problems.append('%s: step numbering broken at %d' % (cid, i))
            op = ALLOWED.get(s['operation'])
            if not op or op != (s['phase'], s['state']):
                problems.append('%s step %d: bad operation/phase/state %s/%s/%s' % (cid, i, s['operation'], s['phase'], s['state']))
                continue
            pi = PHASES.index(s['phase'])
            if pi < last_phase:
                problems.append('%s step %d: phase order' % (cid, i))
            last_phase = max(last_phase, pi)
            phases_seen.add(s['phase'])
            ev = s['evidence_id']
            if s['phase'] == 'REMEDIATION' or (st == 'INCOMPLETE' and s['phase'] == 'VERIFICATION'):
                if ev != '':
                    problems.append('%s step %d: future step must have empty evidence_id' % (cid, i))
            else:
                if ev not in evidence_ids and ev not in activity_ids and ev not in audit_ids:
                    problems.append('%s step %d: unknown evidence id %r' % (cid, i, ev))
                elif ev in captured and captured[ev] > audit_at_of[cid]:
                    problems.append('%s step %d: evidence %s captured after audit_at' % (cid, i, ev))
        if st == 'COMPLETE' and phases_seen - {'VERIFICATION'}:
            problems.append('%s: COMPLETE may only contain verification steps' % cid)
        if st == 'INCOMPLETE' and steps and 'FINDING' not in phases_seen:
            problems.append('%s: INCOMPLETE without findings' % cid)
    return problems
