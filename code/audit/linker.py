"""Link resources that carry no identifier (404 account lookups, fully anonymized analytics rows)
to the case that processed them, using the deletion-worker timeline in system_activity.csv.

The deletion worker logs its work shortly before the workflow reports its status, so an event for a
resource R lies at a characteristic offset from the case's `reported_at`, with a result that matches
the reported status for account deletions.  Resources already tied to a person by identifiers are
removed from the candidate pool first (elimination); what remains is paired deterministically.
"""
import datetime as dt
from collections import defaultdict

RESULT_OF = {'COMPLETE': 'SUCCESS', 'PARTIALLY_COMPLETE': 'PARTIAL', 'NOT_STARTED': 'FAILED'}
# False: a resource is linked only when the evidence leaves exactly one candidate (never an arbitrary pairing).
ASSIGN_AMBIGUOUS = False
ACCOUNT_WINDOWS = [(-1.35, -0.6), (-0.1, 0.2)]      # hours relative to reported_at (batch / hand-timed workflows)
ANALYTICS_WINDOW = (-0.62, -0.38)


def _t(s):
    return dt.datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ')


def _offset_h(event_time, reported_at):
    return (_t(event_time) - _t(reported_at)).total_seconds() / 3600.0


def _cover(c):
    """Files that hold at least one identifier-keyed observation of this person."""
    return {r[4] for r in c['resources'] if r[4]}


def _user_no(c):
    try:
        return int(c['users'][0].split('_')[1])
    except (IndexError, ValueError):
        return 0


def link_keyless(auditor, cases):
    claimed = auditor_claimed(auditor)
    _link_accounts(auditor, cases, claimed)
    _link_analytics(auditor, cases, claimed)


def auditor_claimed(auditor):
    """rid -> set of user ids that evidence ties the resource to (via identifiers)."""
    claimed = defaultdict(set)
    for k, lst in auditor.by_key.items():
        if k.startswith(('u:', 'e:', 'h:')):
            for o in lst:
                for uid in auditor.ids.by_key.get(k, ()) if k[0] in 'eh' else [k[2:]]:
                    claimed[o.rid].add(uid)
    return claimed


def _pair(auditor, group, pool, is_kind):
    """Pair cases with candidate events.  A candidate that was observed in a file covering the case
    is matched to that case first; the rest are paired in (user number, event time) order."""
    pairs, cases, events = [], list(group), list(pool)
    for c in list(cases):
        cover = _cover(c)
        hits = [a for a in events
                if any(o.fid in cover and o.ts <= c['q']['audit_at'] and is_kind(o)
                       for o in auditor.by_rid.get(a['resource_id'], ()))]
        others = [c2 for c2 in cases if c2 is not c and _cover(c2) & {o.fid for a in hits for o in auditor.by_rid.get(a['resource_id'], ())}]
        if len(hits) == 1 and not others:
            pairs.append((c, hits[0]))
            cases.remove(c)
            events.remove(hits[0])
    if ASSIGN_AMBIGUOUS:
        pairs += list(zip(cases, events))
    elif len(cases) == 1 and len(events) == 1:
        pairs.append((cases[0], events[0]))   # only one case and one candidate remain: no guessing involved
    return pairs


def _free(claimed, rid, group):
    owners = claimed.get(rid, set())
    mine = {u for c in group for u in c['users']}
    return not owners or owners <= mine and len(owners) == 1 and next(iter(owners)) in mine


def _link_accounts(auditor, cases, claimed):
    events = [a for a in auditor.activity
              if a['event_type'] == 'DELETE_RECORD' and a['target_system'] == 'account_service']
    groups = defaultdict(list)
    for c in cases:
        if not c['authorized'] or not c['users']:
            continue
        if any(r[2] == 'account' for r in c['resources']):
            continue
        hand_timed = not c['q']['reported_at'].endswith(':00:00Z')
        res = None if hand_timed else RESULT_OF[c['q']['reported_status']]
        groups[(c['q']['reported_at'], res)].append(c)
    for (rep, res), group in groups.items():
        pool = []
        windows = ACCOUNT_WINDOWS[:1] if res else ACCOUNT_WINDOWS[1:]
        for a in events:
            if res and a['reported_result'] != res:
                continue
            off = _offset_h(a['event_time'], rep)
            if any(lo <= off <= hi for lo, hi in windows) and _free(claimed, a['resource_id'], group):
                pool.append(a)
        pool.sort(key=lambda a: a['event_time'])
        group.sort(key=_user_no)
        pairs = _pair(auditor, group, pool, lambda o: o.kind == 'account')
        for c, a in pairs:
            rid = a['resource_id']
            obs = [o for o in auditor.by_rid.get(rid, ()) if o.ts <= c['q']['audit_at']]
            if obs:
                status, fid, ts = auditor.decide_resource(obs, c['q']['audit_at'])
            else:
                status, fid, ts = 'ABSENT', '', a['event_time']
            c['resources'].append(['account_service', rid, 'account', status, fid or '', ts])
            c['linked_by_activity'] = a['activity_id']
            c['link_pool'] = (len(group), len(pool))


def _link_analytics(auditor, cases, claimed):
    events = [a for a in auditor.activity if a['event_type'] == 'ANONYMIZE_RECORD' and a['reported_result'] == 'SUCCESS']
    groups = defaultdict(list)
    for c in cases:
        if not c['authorized'] or not c['users']:
            continue
        if any(r[2] == 'analytics' for r in c['resources']):
            continue
        if any(r[3] in ('PRESENT', 'IDENTIFIABLE', 'ACTIVE') and r[2] != 'audit' for r in c['resources']):
            continue  # incomplete cases do not list compliant analytics rows
        groups[c['q']['reported_at']].append(c)
    for rep, group in groups.items():
        pool = []
        for a in events:
            off = _offset_h(a['event_time'], rep)
            rid = a['resource_id']
            if ANALYTICS_WINDOW[0] <= off <= ANALYTICS_WINDOW[1] and _free(claimed, rid, group):
                pool.append(a)
        pool.sort(key=lambda a: a['event_time'])
        group.sort(key=_user_no)
        pairs = _pair(auditor, group, pool, lambda o: o.kind == 'analytics')
        for c, a in pairs:
            rid = a['resource_id']
            obs = [o for o in auditor.by_rid.get(rid, ()) if o.ts <= c['q']['audit_at'] and o.kind == 'analytics']
            if not obs:
                continue
            status, fid, ts = auditor.decide_resource(obs, c['q']['audit_at'])
            if status == 'ANONYMIZED':
                c['resources'].append(['analytics_platform', rid, 'analytics', status, fid, ts])
