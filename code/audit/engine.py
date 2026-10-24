"""Case reasoning: resolve identity, reconstruct state at audit_at, apply policy, plan steps."""
import csv
import json
import os
import re
from collections import defaultdict

from . import evidence as ev
from . import evidence_manual
from .authorize import classify
from .identity import IdentityIndex

# state severity: later in the tuple == worse (violating)
VIOLATING = {'PRESENT', 'IDENTIFIABLE', 'ACTIVE'}
ABSENCE_KINDS = {'account', 'search', 'export', 'support_att', 'marketing'}

# kind -> (finding op, remediation op, remediation state, verification op, verification state)
POLICY = {
    # DELETE rules (USER_PROFILE, SEARCH_INDEX_ENTRY, SUPPORT_ATTACHMENT, USER_EXPORT, MARKETING_PROFILE)
    'account': ('REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'ABSENT', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
    'search': ('REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'ABSENT', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
    'support_att': ('REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'ABSENT', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
    'export': ('REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'ABSENT', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
    'marketing': ('REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'ABSENT', 'VERIFY_RECORD_ABSENT', 'ABSENT'),
    # REDACT rules (SUPPORT_MESSAGE, EMAIL_DELIVERY_LOG, ACCOUNT_TOMBSTONE)
    'support_msg': ('REPORT_EXPOSED_PII', 'REDACT_RECORD', 'REDACTED', 'VERIFY_RECORD_REDACTED', 'REDACTED'),
    'tombstone': ('REPORT_EXPOSED_PII', 'REDACT_RECORD', 'REDACTED', 'VERIFY_RECORD_REDACTED', 'REDACTED'),
    'email_log': ('REPORT_EXPOSED_PII', 'REDACT_RECORD', 'REDACTED', 'VERIFY_RECORD_REDACTED', 'REDACTED'),
    # ANONYMIZE rules (ANALYTICS_PROFILE, PRODUCT_TELEMETRY)
    'analytics': ('REPORT_EXPOSED_PII', 'ANONYMIZE_RECORD', 'ANONYMIZED', 'VERIFY_RECORD_ANONYMIZED', 'ANONYMIZED'),
    # active processes
    'flow': ('REPORT_ACTIVE_DATA_FLOW', 'DISABLE_DATA_FLOW', 'DISABLED', 'VERIFY_DATA_FLOW_DISABLED', 'DISABLED'),
    'backup': ('REPORT_RESTORE_RISK', 'BLOCK_RESTORE', 'BLOCKED', 'VERIFY_RESTORE_BLOCKED', 'BLOCKED'),
    # RETAIN rules (SECURITY_AUDIT_LOG, LEGAL_HOLD_RECORD)
    'audit': (None, None, None, 'VERIFY_RECORD_PRESENT', 'PRESENT'),
    'legal': (None, None, None, 'VERIFY_RECORD_PRESENT', 'PRESENT'),
}
FINDING_ORDER = ['REPORT_REMAINING_RECORD', 'REPORT_EXPOSED_PII', 'REPORT_ACTIVE_DATA_FLOW', 'REPORT_RESTORE_RISK']
REMEDIATION_ORDER = ['DISABLE_DATA_FLOW', 'BLOCK_RESTORE', 'DELETE_RECORD', 'REDACT_RECORD', 'ANONYMIZE_RECORD']
VERIFICATION_ORDER = ['VERIFY_RECORD_ABSENT', 'VERIFY_RECORD_PRESENT', 'VERIFY_RECORD_REDACTED',
                      'VERIFY_RECORD_ANONYMIZED', 'VERIFY_DATA_FLOW_DISABLED', 'VERIFY_RESTORE_BLOCKED']


def read_csv(path):
    return list(csv.DictReader(open(path, encoding='utf-8')))


class Auditor:
    def __init__(self, dataset_dir):
        self.dir = dataset_dir
        self.ids = IdentityIndex(dataset_dir)
        self.obs, self.files, self.unknown = ev.load_all(dataset_dir)
        self.rules = read_csv(os.path.join(dataset_dir, 'deletion_rules.csv'))
        self.activity = read_csv(os.path.join(dataset_dir, 'system_activity.csv'))
        self.sections = ev.SECTIONS
        self.obs += evidence_manual.load(dataset_dir, self.files)
        self.apply_activity_kinds()
        self.by_key = defaultdict(list)
        self.by_rid = defaultdict(list)
        for o in self.obs:
            self.by_rid[o.rid].append(o)
            for k in o.keys:
                self.by_key[k].append(o)

    # ------------------------------------------------------------ helpers
    def apply_activity_kinds(self):
        """The deletion worker's operation on a resource says which policy rule governs it.

        Evidence rows for support tickets and analytics rows look alike whatever they hold, but the
        treatment the worker applied (DELETE vs REDACT, DELETE vs ANONYMIZE) identifies the data
        category: DELETE on a support item means an attachment, DELETE on an analytics id means a
        marketing profile."""
        treat = defaultdict(set)
        for a in self.activity:
            if a['event_type'] in ('DELETE_RECORD', 'REDACT_RECORD', 'ANONYMIZE_RECORD'):
                treat[a['resource_id']].add(a['event_type'])
        for o in self.obs:
            t = treat.get(o.rid)
            if not t:
                continue
            if o.system == 'support_platform' and 'DELETE_RECORD' in t and o.kind in ('support_msg', 'support_att'):
                if o.kind == 'support_msg':
                    o.kind, o.status = 'support_att', 'PRESENT'
            elif o.system == 'analytics_platform' and 'DELETE_RECORD' in t and 'ANONYMIZE_RECORD' not in t                     and o.kind in ('analytics', 'marketing'):
                o.kind, o.status = 'marketing', 'PRESENT'

    def captured(self, fid):
        return self.files[fid]['captured_at']

    def case_obs(self, ident, audit_at):
        """Observations about this person's resources at or before audit_at.

        A resource is linked to the person when any observation of it carries one of the
        person's identifiers (current or historical email, hash, phone, user_id).  Once linked,
        keyless observations of the same resource (e.g. a 404 lookup) count as well."""
        linked = set()
        for k in ident.keys:
            for o in self.by_key.get(k, ()):
                linked.add(o.rid)  # identity of a resource is a stable fact; only its *state* is time-bound
        out = []
        for rid in linked:
            out += [o for o in self.by_rid.get(rid, ()) if o.ts <= audit_at and o.kind != 'link']
        # absence reasoning: a snapshot that covers this person (it holds some of their records) and
        # carries a section for a system, but omits one of their deletable records, shows it gone.
        cover = {o.fid for k in ident.keys for o in self.by_key.get(k, ())}
        have = {(o.system, o.rid, o.kind) for o in out}
        for fid, ts, system, rids in self.sections:
            if fid not in cover or ts > audit_at:
                continue
            for (osys, rid, kind) in have:
                if osys == system and rid not in rids and kind in ABSENCE_KINDS:
                    out.append(ev.Obs(fid, ts, rid, kind, 'ABSENT', set(), 'absence', system))
        return out

    def latest_state(self, obs_list):
        """Collapse observations to one per resource: latest timestamp, worst state on ties."""
        by_rid = defaultdict(list)
        for o in obs_list:
            by_rid[(o.system, o.rid, o.kind)].append(o)
        return by_rid

    # ------------------------------------------------------------ main
    def prepare(self, q):
        """Pass 1: classify the request, resolve identity and collect keyed resources."""
        c = {'q': q, 'resources': [], 'users': []}
        authorized, rule = classify(q['request_text'])
        c['authorized'], c['rule'] = authorized, rule
        if not authorized:
            return c
        uids = self.ids.resolve(q['email'])
        c['users'] = uids
        if not uids:
            return c
        ident = self.ids.users[uids[0]]
        c['ident'] = ident
        state = self.latest_state(self.case_obs(ident, q['audit_at']))
        for (system, rid, kind), lst in state.items():
            status, fid, ts = self.decide_resource(lst, q['audit_at'])
            if ts < q['requested_at']:
                continue  # last seen before the request existed: belongs to an earlier workflow
            c['resources'].append([system, rid, kind, status, fid, ts])
        return c

    def finish(self, c):
        """Pass 2: apply policy to the resources and plan the ordered steps."""
        q = c['q']
        if not c['authorized']:
            return 'NOT_APPLICABLE', []
        if not c['users']:
            return 'INCOMPLETE', []
        findings, remediation, verification = [], [], []
        best = {}
        for r in c['resources']:   # one state per resource id: a violating or later reading wins
            k = (r[0], r[1])
            if k not in best or (r[3] in VIOLATING, r[5]) > (best[k][3] in VIOLATING, best[k][5]):
                best[k] = r
        for system, rid, kind, status, fid, ts in best.values():
            fop, rop, rstate, vop, vstate = POLICY[kind]
            if status in VIOLATING and fop:
                findings.append((fop, system, rid,
                                 'PRESENT' if fop in ('REPORT_REMAINING_RECORD', 'REPORT_EXPOSED_PII') else 'ACTIVE', fid))
                remediation.append((rop, system, rid, rstate, ''))
            verification.append((vop, system, rid, vstate, fid))
        status = 'INCOMPLETE' if findings else 'COMPLETE'
        if status == 'COMPLETE':
            primary = self.primary_file(c)
            # a resource known only through the deletion timeline (no observation of its own) is proven
            # absent by the snapshot that covers the rest of the case; with no covering snapshot, use the
            # latest account-service snapshot inside the case window
            snap = primary or self.snapshot_file(q, 'account_service')
            verification = [(a, b, cc, d, e or snap) for (a, b, cc, d, e) in verification]
        if status == 'INCOMPLETE':
            # only the resources that need work, the account, and retained records are re-checked
            fixed = {(f[1], f[2]) for f in findings}
            verification = [(a, b, cc, d, '') for (a, b, cc, d, e) in verification
                            if (b, cc) in fixed or a in ('VERIFY_RECORD_PRESENT',) or b == 'account_service']
        steps = []
        findings.sort(key=lambda s: (FINDING_ORDER.index(s[0]), s[1], s[2]))
        remediation.sort(key=lambda s: (REMEDIATION_ORDER.index(s[0]), s[1], s[2]))
        verification.sort(key=lambda s: (VERIFICATION_ORDER.index(s[0]), s[1], s[2]))
        n = 0
        for phase, group in (('FINDING', findings), ('REMEDIATION', remediation), ('VERIFICATION', verification)):
            for op, system, rid, st, fid in group:
                n += 1
                steps.append({'step_no': n, 'phase': phase, 'system_id': system, 'resource_id': rid,
                              'operation': op, 'state': st, 'evidence_id': fid})
        return status, steps

    def primary_file(self, c):
        """The citable file that covers most of the case's resources (latest wins ties)."""
        cnt = defaultdict(int)
        for r in c['resources']:
            if r[4]:
                cnt[r[4]] += 1
        if not cnt:
            return ''
        return max(cnt, key=lambda f: (cnt[f], self.captured(f)))

    def snapshot_file(self, q, system):
        """Latest citable snapshot of a system taken inside the case window (used to prove absence)."""
        best = None
        for fid, ts, sysid, rids in self.sections:
            if sysid == system and q['requested_at'] <= ts and self.captured(fid) <= q['audit_at'] and ts <= q['audit_at']:
                if best is None or ts > best[0]:
                    best = (ts, fid)
        return best[1] if best else ''

    def audit_all(self, requests):
        cases = [self.prepare(q) for q in requests]
        from .linker import link_keyless
        link_keyless(self, cases)
        out = []
        for c in cases:
            status, steps = self.finish(c)
            out.append((c['q']['case_id'], status, steps, c))
        return out

    def decide_resource(self, lst, audit_at):
        """Latest state of one resource (worst state on ties) and the citable file proving it."""
        top = max(o.ts for o in lst)
        cur = [o for o in lst if o.ts == top]
        viol = [o for o in cur if o.status in VIOLATING]
        status = (viol or cur)[0].status
        same = [o for o in lst if o.status == status and self.captured(o.fid) <= audit_at]
        if same:
            c = max(same, key=lambda o: (o.ts, o.fid))
            fid = c.fid
        else:
            anyc = [o for o in lst if self.captured(o.fid) <= audit_at]
            fid = max(anyc, key=lambda o: (o.ts, o.fid)).fid if anyc else ''
        return status, fid, top
