"""Targeted tests on a tiny synthetic dataset (no real data, no network).

Run from the repository root:  python -m pytest code/tests -q
"""
import csv
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from audit.authorize import classify  # noqa: E402
from audit.engine import Auditor  # noqa: E402
from audit.validate import validate  # noqa: E402

EMAIL_OLD = 'old.name@example.com'
EMAIL_NEW = 'new.name@example.com'
REQ_COLS = ['case_id', 'email', 'requested_at', 'reported_status', 'reported_at', 'audit_at', 'request_text']
DELETE_TEXT = 'Please delete my account and my personal data.'


def write_csv(path, header, rows):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


SQL_REPLICA = """-- Captured at: 2026-09-10T10:00:00Z
CREATE TABLE customer_data_primary (resource_id TEXT PRIMARY KEY, origin_system TEXT, collection_name TEXT, record_json TEXT, updated_at TEXT);
CREATE TABLE customer_data_read_replica (resource_id TEXT PRIMARY KEY, origin_system TEXT, collection_name TEXT, record_json TEXT, updated_at TEXT);
INSERT INTO customer_data_primary VALUES('audit_record_1','security_service','security_audit_events','{"resource_id":"audit_record_1","subject_reference":"user_1"}','2026-09-10T10:00:00Z');
INSERT INTO customer_data_read_replica VALUES('audit_record_1','security_service','security_audit_events','{"resource_id":"audit_record_1","subject_reference":"user_1"}','2026-09-10T10:00:00Z');
INSERT INTO customer_data_read_replica VALUES('account_11','account_service','customer_profiles','{"resource_id":"account_11","customer_reference":"user_1","full_name":"New Name","email":"NEW.NAME@example.com","phone":"+1 555 0100","account_status":"ACTIVE"}','2026-09-10T10:00:00Z');
"""

BUNDLE = {
    'exported_at': '2026-09-12T12:00:00Z',
    'snapshots': [
        {'captured_at': '2026-09-12T12:00:00Z', 'data_store': 'identity_service', 'collection': 'deleted_customer_references',
         'records': [{'resource_id': 'tombstone_5', 'subject_reference': 'user_3', 'name': 'Tom Stone', 'email': 'tom.stone@example.com',
                      'phone': None, 'email_hash': None, 'account_status': 'DELETED'}]},
        {'captured_at': '2026-09-12T12:00:00Z', 'data_store': 'identity_service', 'collection': 'deleted_customer_references',
         'records': [{'resource_id': 'tombstone_6', 'subject_reference': 'user_4', 'name': None, 'email': None,
                      'phone': None, 'email_hash': None, 'account_status': 'DELETED'}]},
        {'captured_at': '2026-09-12T12:00:00Z', 'data_store': 'security_service', 'collection': 'security_audit_events',
         'records': [{'resource_id': 'audit_record_3', 'subject_reference': 'user_3'},
                     {'resource_id': 'audit_record_4', 'subject_reference': 'user_4'}]},
        {'captured_at': '2026-09-12T12:00:00Z', 'data_store': 'legal_service', 'collection': 'retained_order_records',
         'records': [{'resource_id': 'legal_hold_9', 'subject_reference': 'user_4', 'retained_until': '2033-12-31T23:59:59Z'}]},
    ],
}

LOG = """2026-09-11T09:00:00Z level=DEBUG service=integration_service trace_id=t1 event=connector_config_read_started resource_id=data_flow_7 lookup_key=new.name@example.com
2026-09-11T09:00:01Z level=INFO service=integration_service trace_id=t1 event=connector_config_read_completed resource_id=data_flow_7 enabled=true last_delivery_status=delivered connector_name=x customer_email=new.name@example.com customer_reference=null
2026-09-11T09:05:00Z level=DEBUG service=backup_service trace_id=t2 event=restore_policy_check_started subject_key=user_1
2026-09-11T09:05:01Z level=INFO service=backup_service trace_id=t2 event=restore_policy_read_completed resource_id=backup_8 restore_allowed=true archive_present=true archive_key=a customer_email=null customer_reference=user_1
2026-09-11T09:10:00Z level=DEBUG service=support_platform trace_id=t3 event=message_scan_started subject_key=new.name@example.com
2026-09-11T09:10:01Z level=WARN service=support_platform trace_id=t3 event=message_pii_scan resource_id=support_21 matched_value=new.name@example.com detector=direct_identifier
2026-09-11T09:20:00Z level=DEBUG service=account_service trace_id=t4 event=profile_lookup_started lookup_key=user_2
2026-09-11T09:20:01Z level=INFO service=account_service trace_id=t4 event=profile_lookup_completed resource_id=account_22 rows=0 http_status=404
"""


@pytest.fixture()
def dataset(tmp_path):
    d = tmp_path / 'dataset'
    (d / 'evidence' / 'databases').mkdir(parents=True)
    (d / 'evidence' / 'records').mkdir()
    (d / 'evidence' / 'logs').mkdir()
    write_csv(d / 'user_identities.csv', ['user_id', 'name', 'email', 'phone', 'email_hash', 'updated_at'], [
        ['user_1', 'New Name', EMAIL_NEW, '+1 555 0100', 'a' * 64, '2026-08-01T00:00:00Z'],
        ['user_2', 'Two', 'two@example.com', '+1 555 0101', 'b' * 64, '2026-08-01T00:00:00Z'],
        ['user_3', 'Tom Stone', 'tom.stone@example.com', '+1 555 0102', 'c' * 64, '2026-08-01T00:00:00Z'],
        ['user_4', 'Four', 'four@example.com', '+1 555 0103', 'd' * 64, '2026-08-01T00:00:00Z'],
    ])
    write_csv(d / 'user_audit_log.csv', ['audit_id', 'user_id', 'event_time', 'field_name', 'old_value', 'new_value'], [
        ['audit_01', 'user_1', '2026-07-01T00:00:00Z', 'email', EMAIL_OLD, EMAIL_NEW],
    ])
    write_csv(d / 'system_activity.csv', ['activity_id', 'event_time', 'event_type', 'source_system', 'target_system', 'resource_id', 'reported_result', 'details'], [])
    real_rules = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), 'dataset', 'deletion_rules.csv')
    if os.path.exists(real_rules):
        (d / 'deletion_rules.csv').write_text(open(real_rules, encoding='utf-8').read(), encoding='utf-8')
    else:
        write_csv(d / 'deletion_rules.csv', ['rule_id', 'data_category', 'condition', 'required_action'], [])
    (d / 'evidence' / 'databases' / 'replica.sql').write_text(SQL_REPLICA, encoding='utf-8')
    (d / 'evidence' / 'records' / 'bundle.json').write_text(json.dumps(BUNDLE), encoding='utf-8')
    (d / 'evidence' / 'logs' / 'trace.log').write_text(LOG, encoding='utf-8')
    write_csv(d / 'evidence_files.csv', ['file_id', 'file_path', 'origin', 'captured_at'], [
        ['evidence_01', 'evidence/databases/replica.sql', 'x', '2026-09-10T10:00:00Z'],
        ['evidence_02', 'evidence/records/bundle.json', 'x', '2026-09-12T12:00:00Z'],
        ['evidence_03', 'evidence/logs/trace.log', 'x', '2026-09-11T10:00:00Z'],
    ])
    return str(d)


def case(case_id, email, audit_at, text=DELETE_TEXT, requested='2026-09-01T00:00:00Z', status='COMPLETE'):
    return dict(zip(REQ_COLS, [case_id, email, requested, status, '2026-09-02T00:00:00Z', audit_at, text]))


def run(dataset, q):
    (cid, st, steps, info), = Auditor(dataset).audit_all([q])
    return st, steps


def ops(steps, phase=None):
    return [(s['operation'], s['resource_id']) for s in steps if phase is None or s['phase'] == phase]


def test_historical_email_and_replica_drift(dataset):
    """Request quotes the OLD email; the record lives only in the read replica under the NEW (upper-cased) email."""
    st, steps = run(dataset, case('c1', EMAIL_OLD, '2026-09-10T12:00:00Z'))
    assert st == 'INCOMPLETE'
    assert ('REPORT_REMAINING_RECORD', 'account_11') in ops(steps, 'FINDING')
    finding = [s for s in steps if s['phase'] == 'FINDING'][0]
    assert finding['evidence_id'] == 'evidence_01'          # cites the replica snapshot
    assert ('VERIFY_RECORD_PRESENT', 'audit_record_1') in ops(steps, 'VERIFICATION')   # retained, never deleted
    assert 'audit_record_1' not in [r for _, r in ops(steps, 'REMEDIATION')]


def test_audit_cutoff_ignores_later_evidence(dataset):
    """Log entries from 2026-09-11 must not change an audit performed at 2026-09-10T12:00."""
    early = run(dataset, case('c1', EMAIL_NEW, '2026-09-10T12:00:00Z'))[1]
    late = run(dataset, case('c1', EMAIL_NEW, '2026-09-12T12:00:00Z'))[1]
    assert 'data_flow_7' not in [r for _, r in ops(early)]
    assert ('REPORT_ACTIVE_DATA_FLOW', 'data_flow_7') in ops(late, 'FINDING')


def test_active_flow_disabled_before_deleting_and_restore_blocked(dataset):
    st, steps = run(dataset, case('c1', EMAIL_NEW, '2026-09-12T12:00:00Z'))
    rem = ops(steps, 'REMEDIATION')
    assert rem[0] == ('DISABLE_DATA_FLOW', 'data_flow_7')
    assert rem[1] == ('BLOCK_RESTORE', 'backup_8')
    assert rem.index(('DELETE_RECORD', 'account_11')) > rem.index(('BLOCK_RESTORE', 'backup_8'))
    assert ('REDACT_RECORD', 'support_21') in rem                  # support message keeps the thread, drops the PII
    ver = ops(steps, 'VERIFICATION')
    assert ('VERIFY_DATA_FLOW_DISABLED', 'data_flow_7') in ver and ('VERIFY_RESTORE_BLOCKED', 'backup_8') in ver
    assert all(s['evidence_id'] == '' for s in steps if s['phase'] != 'FINDING')


def test_tombstone_exposed_then_redacted_and_legal_hold_retained(dataset):
    st, steps = run(dataset, case('c3', 'tom.stone@example.com', '2026-09-12T12:00:00Z'))
    assert st == 'INCOMPLETE'
    assert ('REPORT_EXPOSED_PII', 'tombstone_5') in ops(steps, 'FINDING')
    assert ('REDACT_RECORD', 'tombstone_5') in ops(steps, 'REMEDIATION')
    assert ('VERIFY_RECORD_REDACTED', 'tombstone_5') in ops(steps, 'VERIFICATION')

    st, steps = run(dataset, case('c4', 'four@example.com', '2026-09-12T12:00:00Z'))
    assert st == 'COMPLETE'                                       # redacted tombstone + retained records only
    assert all(s['phase'] == 'VERIFICATION' and s['evidence_id'] == 'evidence_02' for s in steps)
    assert ('VERIFY_RECORD_REDACTED', 'tombstone_6') in ops(steps)
    assert ('VERIFY_RECORD_PRESENT', 'legal_hold_9') in ops(steps)
    assert ('VERIFY_RECORD_PRESENT', 'audit_record_4') in ops(steps)


def test_non_deletion_requests_are_not_applicable(dataset):
    for text in ['Please change the email address used for this account. I want to continue using the service.',
                 'Please remove me from marketing lists only. I still want receipts for this account.',
                 'Can you tell me what personal information you hold about me? This is not a request to erase it.',
                 'I can\'t access my profile and need help resetting the login. Do not close the account.']:
        st, steps = run(dataset, case('c9', EMAIL_NEW, '2026-09-12T12:00:00Z', text=text, status='NOT_STARTED'))
        assert (st, steps) == ('NOT_APPLICABLE', [])


def test_multilingual_deletion_requests_are_recognised():
    for text in ['Solicito la eliminación permanente de la cuenta asociada a este correo.',
                 'Je demande la suppression définitive du compte associé à cette adresse e-mail.',
                 'このメールアドレスに関連するアカウントの完全な削除を依頼します。',
                 '이 이메일 주소에 연결된 계정의 영구 삭제를 요청합니다.']:
        assert classify(text)[0], text


def test_account_without_identifiers_is_linked_through_the_deletion_worker_timeline(dataset):
    """A 404 lookup carries no identifier; the worker's DELETE event shortly before reported_at links it."""
    write_csv(os.path.join(dataset, 'system_activity.csv'),
              ['activity_id', 'event_time', 'event_type', 'source_system', 'target_system', 'resource_id', 'reported_result', 'details'],
              [['activity_1', '2026-09-01T23:00:00Z', 'DELETE_RECORD', 'deletion_worker', 'account_service', 'account_22', 'SUCCESS', 'x']])
    q = case('c2', 'two@example.com', '2026-09-12T12:00:00Z', status='COMPLETE')
    st, steps = run(dataset, q)
    assert ('VERIFY_RECORD_ABSENT', 'account_22') in ops(steps)


def test_output_validation_accepts_engine_output_and_rejects_bad_rows(dataset, tmp_path):
    reqs = [case('c1', EMAIL_NEW, '2026-09-12T12:00:00Z'), case('c2', 'four@example.com', '2026-09-12T12:00:00Z'),
            case('c3', EMAIL_NEW, '2026-09-12T12:00:00Z', text='Please change the email address used for this account.')]
    req_path = tmp_path / 'req.csv'
    write_csv(req_path, REQ_COLS, [[q[c] for c in REQ_COLS] for q in reqs])
    A = Auditor(dataset)
    out = tmp_path / 'out.csv'
    from main import serialize
    rows = sorted((cid, st, serialize(steps)) for cid, st, steps, _ in A.audit_all(reqs))
    write_csv(out, ['case_id', 'deletion_status', 'steps_json'], rows)
    captured = {f: m['captured_at'] for f, m in A.files.items()}
    audit_at = {q['case_id']: q['audit_at'] for q in reqs}
    assert validate(str(out), str(req_path), set(A.files), captured, audit_at) == []
    bad = [list(r) for r in rows]
    bad[0][2] = '[{"step_no":2}]'
    write_csv(out, ['case_id', 'deletion_status', 'steps_json'], bad)
    assert validate(str(out), str(req_path), set(A.files), captured, audit_at)
