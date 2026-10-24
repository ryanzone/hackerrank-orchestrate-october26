"""Regression tests for identity linking, audit-time reconstruction and recreation paths.

Reuses the synthetic dataset fixture of test_engine.py.  Run: python -m pytest code/tests -q
"""
import os

from test_engine import EMAIL_NEW, case, dataset, ops, run, write_csv  # noqa: F401  (dataset is a fixture)

ACTIVITY_HEADER = ['activity_id', 'event_time', 'event_type', 'source_system', 'target_system', 'resource_id',
                   'reported_result', 'details']


def add_log(dataset, lines):
    with open(os.path.join(dataset, 'evidence', 'logs', 'trace.log'), 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def set_activity(dataset, rows):
    write_csv(os.path.join(dataset, 'system_activity.csv'), ACTIVITY_HEADER, rows)


def delete_event(n, when, account, result='SUCCESS'):
    return ['activity_%d' % n, when, 'DELETE_RECORD', 'deletion_worker', 'account_service', account, result, 'x']


def test_ambiguous_account_candidates_are_not_assigned_arbitrarily(dataset):
    """Two cases share reported_at and the worker logged two deletions: nobody can say which is whose."""
    set_activity(dataset, [delete_event(1, '2026-09-01T23:00:00Z', 'account_71'),
                           delete_event(2, '2026-09-01T23:10:00Z', 'account_72')])
    for email in ('two@example.com', 'four@example.com'):
        _, steps = run(dataset, case('c', email, '2026-09-12T12:00:00Z'))
        assert not [r for _, r in ops(steps) if r.startswith('account_7')]


def test_unambiguous_account_is_still_linked(dataset):
    set_activity(dataset, [delete_event(1, '2026-09-01T23:00:00Z', 'account_71')])
    _, steps = run(dataset, case('c', 'four@example.com', '2026-09-12T12:00:00Z'))
    assert ('VERIFY_RECORD_ABSENT', 'account_71') in ops(steps)


def test_account_claimed_by_another_person_is_eliminated(dataset):
    """account_11 is tied to user_1 by identifiers, so it cannot be the other person's account."""
    set_activity(dataset, [delete_event(1, '2026-09-01T23:00:00Z', 'account_11'),
                           delete_event(2, '2026-09-01T23:05:00Z', 'account_72')])
    _, steps = run(dataset, case('c', 'four@example.com', '2026-09-12T12:00:00Z'))
    assert ('VERIFY_RECORD_ABSENT', 'account_72') in ops(steps)
    assert ('VERIFY_RECORD_ABSENT', 'account_11') not in ops(steps)


def test_look_alike_identifiers_never_match(dataset):
    add_log(dataset, ['2026-09-11T09:30:00Z level=INFO service=search_service trace_id=t9 event=index_query_completed '
                      'resource_id=search_99 result=hit correlation_key=new.name@example.org',
                      '2026-09-11T09:31:00Z level=INFO service=search_service trace_id=t10 event=index_query_completed '
                      'resource_id=search_98 result=hit correlation_key=NEW.NAME@EXAMPLE.COM'])
    _, steps = run(dataset, case('c', EMAIL_NEW, '2026-09-12T12:00:00Z'))
    found = [r for _, r in ops(steps)]
    assert 'search_99' not in found and 'search_98' in found      # other domain ignored, upper case matched


def test_flow_recreated_after_being_disabled_is_a_finding_but_only_after_it_happens(dataset):
    add_log(dataset, ['2026-09-09T08:00:00Z level=INFO service=integration_service trace_id=r1 event=connector_config_read_completed '
                      'resource_id=data_flow_30 enabled=false last_delivery_status=stopped connector_name=x customer_email=two@example.com',
                      '2026-09-11T08:00:00Z level=INFO service=integration_service trace_id=r2 event=connector_config_read_completed '
                      'resource_id=data_flow_30 enabled=true last_delivery_status=delivered connector_name=x customer_email=two@example.com'])
    before = run(dataset, case('c', 'two@example.com', '2026-09-10T12:00:00Z'))
    after = run(dataset, case('c', 'two@example.com', '2026-09-12T12:00:00Z'))
    assert before[0] == 'COMPLETE' and ('VERIFY_DATA_FLOW_DISABLED', 'data_flow_30') in ops(before[1])
    assert after[0] == 'INCOMPLETE' and ('REPORT_ACTIVE_DATA_FLOW', 'data_flow_30') in ops(after[1], 'FINDING')


def test_deleted_record_is_not_reported_from_older_evidence(dataset):
    """Older logs show the search entry; a later log shows a miss -> latest state wins."""
    add_log(dataset, ['2026-09-08T08:00:00Z level=INFO service=search_service trace_id=s1 event=index_query_completed '
                      'resource_id=search_55 result=hit correlation_key=two@example.com',
                      '2026-09-11T08:00:00Z level=INFO service=search_service trace_id=s2 event=index_query_completed '
                      'resource_id=search_55 result=miss correlation_key=two@example.com'])
    _, steps = run(dataset, case('c', 'two@example.com', '2026-09-12T12:00:00Z'))
    assert ('REPORT_REMAINING_RECORD', 'search_55') not in ops(steps)
    assert ('VERIFY_RECORD_ABSENT', 'search_55') in ops(steps)


def test_resource_last_seen_before_the_request_is_ignored(dataset):
    add_log(dataset, ['2026-08-01T08:00:00Z level=INFO service=search_service trace_id=o1 event=index_query_completed '
                      'resource_id=search_56 result=hit correlation_key=two@example.com'])
    _, steps = run(dataset, case('c', 'two@example.com', '2026-09-12T12:00:00Z', requested='2026-09-01T00:00:00Z'))
    assert 'search_56' not in [r for _, r in ops(steps)]


def test_citations_never_point_to_files_captured_after_audit_at(dataset):
    st, steps = run(dataset, case('c', EMAIL_NEW, '2026-09-10T12:00:00Z'))
    cited = {s['evidence_id'] for s in steps if s['evidence_id']}
    assert cited <= {'evidence_01'}          # evidence_02 (09-12) and evidence_03 (09-11) are in the future
