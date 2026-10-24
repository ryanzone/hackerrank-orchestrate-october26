"""Evidence ingestion: turn every file under dataset/evidence into normalized observations.

An observation says "resource R of kind K was seen in state S at time T in file F,
and it carries these identifying keys".  No case logic lives here.
"""
import csv
import json
import os
import re
import sqlite3
from collections import defaultdict

from . import logparse

# resource-id prefix -> (system_id, kind)
PREFIX = [
    ('account_', 'account_service', 'account'),
    ('search_', 'search_service', 'search'),
    ('analytics_', 'analytics_platform', 'analytics'),
    ('support_', 'support_platform', 'support'),
    ('export_', 'export_service', 'export'),
    ('data_flow_', 'integration_service', 'flow'),
    ('backup_', 'backup_service', 'backup'),
    ('audit_record_', 'security_service', 'audit'),
    ('tombstone_', 'identity_service', 'tombstone'),
    ('legal_hold_', 'legal_service', 'legal'),
    ('marketing_', 'marketing_platform', 'marketing'),
    ('email_delivery_', 'email_delivery_service', 'email_log'),
    ('cache_', 'cache_service', 'cache'),
    ('object_', 'object_storage', 'attachment'),
]

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
USER_RE = re.compile(r'^user_\d+$')
HASH_RE = re.compile(r'^[0-9a-f]{64}$')


def classify_rid(rid):
    for p, system, kind in PREFIX:
        if rid.startswith(p):
            return system, kind
    return None, None


class Obs:
    __slots__ = ('fid', 'ts', 'system', 'rid', 'kind', 'status', 'keys', 'src', 'extra')

    def __init__(self, fid, ts, rid, kind, status, keys, src='', system=None, extra=None):
        self.fid, self.ts, self.rid, self.kind, self.status = fid, ts, rid, kind, status
        self.keys = frozenset(keys)
        self.src = src
        self.system = system or classify_rid(rid)[0]
        self.extra = extra or {}

    def __repr__(self):
        return 'Obs(%s %s %s %s %s %s)' % (self.fid, self.ts, self.rid, self.kind, self.status, sorted(self.keys))


def norm_key(v):
    """Return a normalized identifier key or None."""
    if v is None:
        return None
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s or s.lower() in ('null', 'none', '[redacted]'):
        return None
    sl = s.lower()
    if EMAIL_RE.match(sl):
        return 'e:' + sl
    if USER_RE.match(sl):
        return 'u:' + sl
    if HASH_RE.match(sl):
        return 'h:' + sl
    if re.match(r'^\+?[\d][\d ()\-]{6,}$', s):
        return 'p:' + re.sub(r'\D', '', s)
    return None


def keys_of(*vals):
    out = set()
    for v in vals:
        k = norm_key(v)
        if k:
            out.add(k)
    return out


def scan_keys(obj, skip=()):
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip:
                continue
            out |= scan_keys(v, skip)
    elif isinstance(obj, list):
        for v in obj:
            out |= scan_keys(v, skip)
    elif isinstance(obj, str):
        k = norm_key(obj)
        if k:
            out.add(k)
    return out


def rtime(s):
    return s


# ---------------------------------------------------------------- record_lookup family
LOOKUP_MAP = {
    ('USER_PROFILE', 'FOUND'): ('account', 'PRESENT'),
    ('USER_PROFILE', 'NOT_FOUND'): ('account', 'ABSENT'),
    ('SEARCH_INDEX_ENTRY', 'FOUND'): ('search', 'PRESENT'),
    ('SEARCH_INDEX_ENTRY', 'NOT_FOUND'): ('search', 'ABSENT'),
    ('ANALYTICS_PROFILE', 'IDENTIFIABLE'): ('analytics', 'IDENTIFIABLE'),
    ('ANALYTICS_PROFILE', 'ANONYMIZED'): ('analytics', 'ANONYMIZED'),
    ('DATA_FLOW', 'ACTIVE'): ('flow', 'ACTIVE'),
    ('DATA_FLOW', 'DISABLED'): ('flow', 'DISABLED'),
    ('BACKUP_ARCHIVE', 'ACTIVE'): ('backup', 'ACTIVE'),
    ('BACKUP_ARCHIVE', 'BLOCKED'): ('backup', 'BLOCKED'),
    ('SECURITY_AUDIT_LOG', 'FOUND'): ('audit', 'PRESENT'),
    ('SUPPORT_MESSAGE', 'IDENTIFIABLE'): ('support_msg', 'IDENTIFIABLE'),
    ('SUPPORT_MESSAGE', 'REDACTED'): ('support_msg', 'REDACTED'),
    ('SUPPORT_ATTACHMENT', 'FOUND'): ('support_att', 'PRESENT'),
    ('SUPPORT_ATTACHMENT', 'NOT_FOUND'): ('support_att', 'ABSENT'),
    ('USER_EXPORT', 'FOUND'): ('export', 'PRESENT'),
    ('USER_EXPORT', 'NOT_FOUND'): ('export', 'ABSENT'),
    ('MARKETING_PROFILE', 'FOUND'): ('marketing', 'PRESENT'),
    ('MARKETING_PROFILE', 'NOT_FOUND'): ('marketing', 'ABSENT'),
    ('ACCOUNT_TOMBSTONE', 'IDENTIFIABLE'): ('tombstone', 'IDENTIFIABLE'),
    ('ACCOUNT_TOMBSTONE', 'REDACTED'): ('tombstone', 'REDACTED'),
    ('LEGAL_HOLD_RECORD', 'FOUND'): ('legal', 'PRESENT'),
}


def lookup_obs(fid, ts, system, rid, rtype, result, subject, unknown):
    m = LOOKUP_MAP.get((rtype, result))
    if not m:
        unknown[(rtype, result)] += 1
        return None
    return Obs(fid, ts, rid, m[0], m[1], keys_of(subject), 'lookup', system)


def parse_txt(fid, path, unknown):
    out = []
    for line in open(path, encoding='utf-8'):
        p = [x.strip() for x in line.lstrip('- ').split('|')]
        if len(p) == 6 and p[0][:4].isdigit():
            o = lookup_obs(fid, p[0], p[1], p[2], p[3], p[4], p[5], unknown)
            if o:
                out.append(o)
    return out


# ---------------------------------------------------------------- JSON
SECTIONS = []  # (fid, ts, system, set(resource ids)) -- completeness claims for absence reasoning


def parse_json(fid, path, unknown):
    d = json.load(open(path, encoding='utf-8'))
    out = []
    if 'snapshots' in d:
        for s in d['snapshots']:
            ts = s.get('captured_at') or d.get('exported_at')
            if s['collection'] != 'operational_record_history':
                SECTIONS.append((fid, ts, s['data_store'], {r['resource_id'] for r in s['records']}))
            for r in s['records']:
                if s['collection'] == 'operational_record_history':
                    o = lookup_obs(fid, r['recorded_at'], r['system'], r['resource_id'], r['record_type'],
                                   r['result'], r['lookup_value'], unknown)
                    if o:
                        out.append(o)
                else:
                    out += record_obs(fid, ts, s['data_store'], s['collection'], r, 'bundle')
    elif 'service_exports' in d:
        for sysn, cols in d['service_exports'].items():
            for col, recs in cols.items():
                for r in recs:
                    out += export_obs(fid, sysn, col, r)
    elif 'record_type' in (d.get('records') or [{}])[0]:
        for r in d['records']:
            o = lookup_obs(fid, r['recorded_at'], r['system'], r['resource_id'], r['record_type'], r['result'],
                           r['subject_reference'], unknown)
            if o:
                out.append(o)
    else:
        ts = d.get('captured_at')
        coll = d.get('collection')
        for r in d['records']:
            out += record_obs(fid, ts, None, coll, r, 'collection')
    return out


COLL_KIND = {
    'customer_profiles': 'account',
    'customer_search_documents': 'search',
    'customer_analytics_reports': 'analytics',
    'security_audit_events': 'audit',
    'backup_catalog': 'backup',
    'connector_configurations': 'flow',
    'customer_export_jobs': 'export',
    'support_conversations': 'support_msg',
    'ticket_attachments': 'support_att',
    'deleted_customer_references': 'tombstone',
    'retained_order_records': 'legal',
}


def record_obs(fid, ts, system, coll, r, src, table=None):
    """A record from a snapshot bundle / replica row / collection export."""
    kind = COLL_KIND.get(coll)
    if kind is None:
        return []
    rid = r['resource_id']
    keys = scan_keys(r, skip=('resource_id',))
    status = None
    extra = {}
    if kind == 'account':
        status = 'PRESENT'
        extra['account_status'] = r.get('account_status')
        keys = scan_keys(r, skip=('resource_id',))
    elif kind == 'search':
        status = 'PRESENT'
    elif kind == 'analytics':
        direct = [r.get('email'), r.get('phone'), r.get('email_hash'), r.get('user_id')]
        status = 'IDENTIFIABLE' if any(norm_key(x) for x in direct) else 'ANONYMIZED'
        keys = scan_keys({k: r.get(k) for k in ('email', 'phone', 'email_hash', 'user_id')})
    elif kind == 'audit':
        status = 'PRESENT'
    elif kind == 'backup':
        status = 'ACTIVE' if r.get('restore_allowed') in (True, 1, '1', 'true') else 'BLOCKED'
    elif kind == 'flow':
        status = 'ACTIVE' if r.get('enabled') in (True, 1, '1', 'true') else 'DISABLED'
    elif kind == 'export':
        status = 'PRESENT' if r.get('download_available') in (True, 1, '1', 'true') else 'ABSENT'
    elif kind == 'support_msg':
        txt = r.get('message_text') or ''
        keys = scan_keys({k: v for k, v in r.items() if k != 'message_text'})
        em = re.findall(r'[^@\s]+@[^@\s]+\.[^@\s]+', txt)
        for e in em:
            keys.add('e:' + e.lower())
        status = 'IDENTIFIABLE' if em or r.get('requester_email') or r.get('requester_name') else 'REDACTED'
        extra['text'] = txt
    elif kind == 'support_att':
        status = 'PRESENT'
    elif kind == 'tombstone':
        direct = [r.get(k) for k in ('name', 'email', 'phone', 'email_hash')]
        status = 'IDENTIFIABLE' if any(norm_key(x) or (x and str(x).lower() != 'null') for x in direct) else 'REDACTED'
        keys = scan_keys(r, skip=('resource_id',))
    elif kind == 'legal':
        status = 'PRESENT'
    o = Obs(fid, ts, rid, kind, status, keys, src, system or classify_rid(rid)[0], extra)
    o.extra['table'] = table
    return [o]


def export_obs(fid, sysn, col, r):
    ts = r.get('observed_at')
    rid = r['resource_id']
    if sysn == 'account_service':
        p = r.get('profile')
        if p:
            return [Obs(fid, ts, rid, 'account', 'PRESENT', scan_keys(p), 'export')]
        return [Obs(fid, ts, rid, 'account', 'ABSENT', set(), 'export')]
    if sysn == 'search_service':
        return [Obs(fid, ts, rid, 'search', 'PRESENT' if r.get('hits') else 'ABSENT', keys_of(r.get('query')), 'export')]
    if sysn == 'analytics_platform':
        direct = [r.get('email'), r.get('phone'), r.get('email_hash'), r.get('customer_reference')]
        st = 'IDENTIFIABLE' if any(norm_key(x) for x in direct) else 'ANONYMIZED'
        return [Obs(fid, ts, rid, 'analytics', st, keys_of(*direct), 'export')]
    if sysn == 'support_platform':
        direct = [r.get('requester_email'), r.get('requester_name'), r.get('requester_phone')]
        st = 'IDENTIFIABLE' if any(direct) else 'REDACTED'
        return [Obs(fid, ts, rid, 'support_msg', st, keys_of(*direct, r.get('customer_reference')), 'export')]
    if sysn == 'export_service':
        return [Obs(fid, ts, rid, 'export', 'PRESENT', keys_of(r.get('owner_email'), r.get('owner_phone'),
                                                              r.get('customer_reference')), 'export')]
    if sysn == 'integration_service':
        return [Obs(fid, ts, rid, 'flow', 'ACTIVE' if r.get('enabled') else 'DISABLED',
                    keys_of(r.get('customer_email'), r.get('customer_reference')), 'export')]
    if sysn == 'backup_service':
        return [Obs(fid, ts, rid, 'backup', 'ACTIVE' if r.get('restore_allowed') else 'BLOCKED',
                    keys_of(r.get('customer_email'), r.get('customer_reference')), 'export')]
    if sysn == 'security_service':
        return [Obs(fid, ts, rid, 'audit', 'PRESENT', keys_of(r.get('subject_reference')), 'export')]
    return []


# ---------------------------------------------------------------- SQL
def parse_sql(fid, path, captured, unknown):
    text = open(path, encoding='utf-8').read()
    db = sqlite3.connect(':memory:')
    db.executescript(text)
    db.row_factory = sqlite3.Row
    tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
    m = re.search(r'-- Captured at: (\S+)', text) or re.search(r'snapshot_completed_at=(\S+)', text)
    ts = m.group(1) if m else captured
    out = []
    TABLE_SYSTEM = {'customer_profiles': 'account_service', 'customer_search_documents': 'search_service',
                    'customer_analytics_profiles': 'analytics_platform', 'support_records': 'support_platform',
                    'customer_exports': 'export_service', 'connector_subscriptions': 'integration_service',
                    'backup_restore_entries': 'backup_service', 'security_audit_records': 'security_service'}
    rec_sections = defaultdict(set)
    for t in tables:
        cols = [r[1] for r in db.execute('pragma table_info(%s)' % t)]
        for row in db.execute('select * from %s' % t):
            r = dict(row)
            if t == 'snapshot_scope':
                continue
            if 'record_json' in cols:
                rec = json.loads(r['record_json'])
                rec_sections[r['origin_system']].add(r['resource_id'])
                out += record_obs(fid, ts, r['origin_system'], r['collection_name'], rec, 'sql', t)
            elif t.startswith('customer_profiles'):
                out.append(Obs(fid, ts, r['resource_id'], 'account', 'PRESENT',
                               keys_of(r.get('user_id'), r.get('customer_reference'), r.get('email'), r.get('phone'),
                                       r.get('email_hash')), 'sql', None, {'table': t, 'account_status': r.get('account_status')}))
            elif t == 'customer_search_documents':
                out.append(Obs(fid, ts, r['resource_id'], 'search', 'PRESENT',
                               keys_of(r.get('customer_reference'), r.get('email'), r.get('phone'), r.get('email_hash')),
                               'sql', None, {'table': t}))
            elif t == 'search_documents_primary':
                out.append(Obs(fid, ts, r['resource_id'], 'search', 'PRESENT', keys_of(r.get('email')), 'sql',
                               None, {'table': t, 'source_resource_id': r.get('source_resource_id')}))
            elif t == 'customer_analytics_profiles':
                direct = [r.get('customer_reference'), r.get('email'), r.get('phone'), r.get('email_hash')]
                out.append(Obs(fid, ts, r['resource_id'], 'analytics',
                               'IDENTIFIABLE' if any(norm_key(x) for x in direct) else 'ANONYMIZED',
                               keys_of(*direct), 'sql', None, {'table': t}))
            elif t == 'support_records':
                direct = [r.get('requester_name'), r.get('requester_email'), r.get('requester_phone')]
                out.append(Obs(fid, ts, r['resource_id'], 'support_msg',
                               'IDENTIFIABLE' if any(direct) else 'REDACTED',
                               keys_of(r.get('customer_reference'), r.get('requester_email'), r.get('requester_phone')),
                               'sql', None, {'table': t, 'text': r.get('message_excerpt')}))
            elif t == 'customer_exports':
                out.append(Obs(fid, ts, r['resource_id'], 'export', 'PRESENT',
                               keys_of(r.get('customer_reference'), r.get('owner_email'), r.get('owner_phone')), 'sql',
                               None, {'table': t}))
            elif t == 'connector_subscriptions':
                out.append(Obs(fid, ts, r['resource_id'], 'flow', 'ACTIVE' if r['enabled'] else 'DISABLED',
                               keys_of(r.get('customer_reference'), r.get('customer_email')), 'sql', None, {'table': t}))
            elif t == 'backup_restore_entries':
                out.append(Obs(fid, ts, r['resource_id'], 'backup', 'ACTIVE' if r['restore_allowed'] else 'BLOCKED',
                               keys_of(r.get('customer_reference'), r.get('customer_email')), 'sql', None, {'table': t}))
            elif t == 'security_audit_records':
                out.append(Obs(fid, ts, r['resource_id'], 'audit', 'PRESENT', keys_of(r.get('subject_reference')),
                               'sql', None, {'table': t}))
            else:
                unknown[('sql-table', t)] += 1
        system_of_table = TABLE_SYSTEM.get(t) or ('account_service' if t.startswith('customer_profiles') else None)
        if system_of_table:
            SECTIONS.append((fid, ts, system_of_table, {r['resource_id'] for r in db.execute('select resource_id from %s' % t)}))
    for system, rids in rec_sections.items():
        SECTIONS.append((fid, ts, system, rids))
    return out, ts, tables


# ---------------------------------------------------------------- logs
def f_true(v):
    return str(v).lower() == 'true'


def parse_log(fid, path, unknown):
    lines = [logparse.parse_line(l) for l in open(path, encoding='utf-8').read().splitlines()]
    lines = [l for l in lines if l and l['event']]
    by_trace = defaultdict(list)
    for l in lines:
        if l['trace']:
            by_trace[l['trace']].append(l)
    KEYF = ('lookup_key', 'correlation_key', 'subject_key', 'subject_ref', 'match_key', 'owner_key', 'scan_key',
            'matched_value', 'customer_email', 'owner_email', 'requester_email', 'requester_phone', 'customer_reference',
            'email', 'phone', 'email_hash', 'subject_reference')
    out = []
    for l in lines:
        f = l['f']
        rid = f.get('resource_id')
        if not rid:
            continue
        system, kind = classify_rid(rid)
        if kind is None:
            continue
        keys = set()
        for g in by_trace.get(l['trace'], [l]) if l['trace'] else [l]:
            for k in KEYF:
                keys |= keys_of(g['f'].get(k))
        ev = l['event']
        ts = l['ts']
        status = None
        if ev == 'profile_lookup_completed':
            status = 'PRESENT' if (f.get('rows') == '1' or f.get('http_status') == '200') else 'ABSENT'
        elif ev == 'index_query_completed':
            status = 'PRESENT' if f.get('result') == 'hit' else 'ABSENT'
        elif ev == 'analytics_profile_read':
            status = 'ANONYMIZED' if f.get('direct_identifiers') == '0' else 'IDENTIFIABLE'
        elif ev == 'analytics_identity_scan':
            status = 'IDENTIFIABLE'
        elif ev == 'analytics_profile_read_completed':
            direct = [f.get('email'), f.get('phone'), f.get('email_hash'), f.get('customer_reference')]
            status = 'IDENTIFIABLE' if any(norm_key(x) for x in direct) else 'ANONYMIZED'
            kind = 'analytics'
        elif ev == 'contact_lookup_completed':
            kind, status = 'marketing', 'PRESENT'
        elif ev == 'message_pii_scan':
            kind, status = 'support_msg', 'IDENTIFIABLE'
        elif ev == 'message_read':
            kind = 'support_msg'
            status = 'REDACTED' if f.get('message_text', '').strip() == '[REDACTED]' else 'IDENTIFIABLE'
        elif ev == 'support_ticket_read_completed':
            kind = 'support_msg'
            direct = [f.get('requester_email'), f.get('requester_name'), f.get('requester_phone')]
            status = 'IDENTIFIABLE' if any(x and x.lower() != 'null' for x in direct) else 'REDACTED'
        elif ev == 'attachment_pii_scan':
            kind, status = 'support_att', 'PRESENT'
        elif ev in ('export_download_check_completed', 'export_metadata_read_completed'):
            status = 'PRESENT' if f_true(f.get('available')) else 'ABSENT'
        elif ev == 'connector_config_loaded':
            status = 'ACTIVE' if (f_true(f.get('enabled')) and f_true(f.get('scheduler_registered'))) else 'DISABLED'
        elif ev == 'connector_heartbeat':
            status = 'ACTIVE' if f_true(f.get('enabled')) else 'DISABLED'
        elif ev == 'connector_config_read_completed':
            status = 'ACTIVE' if f_true(f.get('enabled')) else 'DISABLED'
        elif ev == 'restore_policy_read_completed':
            status = 'ACTIVE' if f_true(f.get('restore_allowed')) else 'BLOCKED'
        elif ev == 'restore_candidate_evaluated':
            status = 'ACTIVE' if (f_true(f.get('eligible')) and not f_true(f.get('deletion_block'))) else 'BLOCKED'
        elif ev == 'restore_authorization_completed':
            status = 'BLOCKED' if f.get('decision') == 'deny' else 'ACTIVE'
        elif ev == 'audit_record_read_completed':
            status = 'PRESENT'
        elif ev == 'retained_order_loaded':
            kind, status = 'legal', 'PRESENT'
        elif ev == 'record_lookup':
            o = lookup_obs(fid, ts, f.get('system'), rid, f.get('record_type'), f.get('result'),
                           f.get('subject_reference'), unknown)
            if o:
                out.append(o)
            continue
        elif ev in ('contact_query_completed',):
            kind, status = 'support_att', 'PRESENT' if f.get('rows') == '1' else 'ABSENT'
        elif ev == 'deletion_marker_check':
            kind = 'support_att'
            status = 'PRESENT' if not f_true(f.get('marker_found')) else 'ABSENT'
        elif ev.endswith('_started'):
            continue
        else:
            unknown[('log-event', ev)] += 1
            continue
        out.append(Obs(fid, ts, rid, kind, status, keys, 'log', system, {'event': ev, 'trace': l['trace']}))
    return out


# ---------------------------------------------------------------- loader
def load_all(dataset_dir):
    manifest = list(csv.DictReader(open(os.path.join(dataset_dir, 'evidence_files.csv'), encoding='utf-8')))
    unknown = defaultdict(int)
    obs = []
    files = {}
    for m in manifest:
        path = os.path.join(dataset_dir, m['file_path'])
        files[m['file_id']] = m
        ext = os.path.splitext(path)[1].lower()
        if ext == '.txt':
            obs += parse_txt(m['file_id'], path, unknown)
        elif ext == '.json':
            obs += parse_json(m['file_id'], path, unknown)
        elif ext == '.sql':
            o, ts, tables = parse_sql(m['file_id'], path, m['captured_at'], unknown)
            obs += o
        elif ext == '.log':
            obs += parse_log(m['file_id'], path, unknown)
    return obs, files, unknown
