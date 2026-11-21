"""Offline consistency check of the saved synthetic closure-race evidence.

Does not rerun HTTP/model, contact providers, or prove that erasure completed.
"""
import argparse
import json
import re
from pathlib import Path


def verify(value):
    assert value['schema'] == 'lite.closure-race-acceptance/1' and value['passed'] is True
    assert re.fullmatch('[0-9a-f]{40}', value['image_source'])
    assert value['independent_gunicorn_processes'] == 2 and value['uid'] == 1000
    assert value['packaged_files'] == 75 and value['shared_durable_store'] == 'SQLite'
    for flag in ('real_http','private_worker_process','worker_restarted'):
        assert value[flag] is True, flag
    for flag in ('real_apple','real_gcs_kms','external_network','cloud_capacity_tested','complete_erasure_tested'):
        assert value[flag] is False, flag
    scenarios = value['scenarios']
    assert [x['mode'] for x in scenarios] == ['inference','result_commit']
    for s in scenarios:
        assert s['model_calls'] == len(s['model_events']) == 2
        assert all(type(e['pid']) is int and e['pid'] > 0 for e in s['model_events'])
        assert len(s['barrier_events']) == 1 and s['barrier_events'][0]['stage'] == s['mode']
        assert s['barrier_events'][0]['pid'] == s['model_events'][1]['pid']
        q = s['quota']
        assert (q['used'],q['attempts'],q['pending'],q['limit']) == (1,2,1,2)
        expected = (409,'lite_operation_conflict','status') if s['mode']=='inference' else (410,'lite_operation_expired','none')
        assert s['late_http'] == expected[0]
        assert s['late_response'] == dict(error=expected[1], retry_action=expected[2], keep_local=True)
        for flag in ('ledger_unchanged_by_seal_and_replay','previous_audit_rows_retained',
                     'privacy_unchanged','intake_did_not_seal','restart_state_unchanged'):
            assert s[flag] is True, flag
        assert re.fullmatch('[0-9a-f]{64}', s['original_sha256'])
        receipt=s['baseline_receipt']
        assert receipt['operation'] == f'{1:032x}' and receipt['outcome']=='succeeded'
        assert receipt['admitted_limit']==2 and receipt['day']==q['day']
        assert re.fullmatch('[0-9a-f]{64}', receipt['result_sha256'])
        lane=s['private_lane_receipt']
        assert (lane['sealed'],lane['total'],lane['service_result_payloads'])==(2,2,'current_sealed')
        for flag in ('account_deleted','historical_versions_erased','physical_backups_erased','quota_reset','research_data_changed'):
            assert lane[flag] is False, flag
        assert set(s['seals'])=={'1','2'}
        for n,seal in s['seals'].items():
            assert seal['v']==3 and seal['operation']==f'{int(n):032x}'
            assert 'result' not in seal and 'result_sha256' not in seal
            assert seal['disposition']['inventory_sha256']==lane['inventory_sha256']
            assert seal['disposition']['closure_operation']==lane['closure_operation']
            assert seal['prior']['revision']>0 and re.fullmatch('[0-9a-f]{64}',seal['prior']['sha256'])
        for state in (s['intake'],s['restarted_status']):
            assert state['account_deleted'] is False and state['state']=='account_deletion_pending'
            assert state['research_withdrawal']=='requested'
            assert state['operation']==lane['closure_operation']
            assert state['inventory_sha256']==lane['inventory_sha256']
            assert state['local_records_preserved'] is True and state['subscription_cancelled'] is False
        assert set(s['denied'])=={'prepare','status','restart_run','restart_status'}
        assert all(x['http']==401 and 'result' not in x['response'] for x in s['denied'].values())
    assert scenarios[0]['original_sha256']==scenarios[1]['original_sha256']
    return dict(passed=True,scenarios=2,total_model_calls=4,successful_debits=2,
        pending_executed_operations=2,late_result_resurrection=False,tests_rerun=False,
        real_provider_validation=False,complete_erasure=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence',type=Path)
    args=parser.parse_args()
    print(json.dumps(verify(json.loads(args.evidence.read_text()))))
