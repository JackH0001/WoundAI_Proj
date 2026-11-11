"""Verify retained replica probe artifacts; this does not rerun the service."""
import argparse
import hashlib
import json
from pathlib import Path


def verify(root):
    root = Path(root)
    report = json.loads((root/'runtime-probe.json').read_text())
    parity = json.loads((root/'source-parity.json').read_text())
    stopped = json.loads((root/'runtime-vm-final.json').read_text())
    qa = json.loads((root/'qa-manifest.json').read_text())
    for name, expected in qa.items():
        path = root/'qa'/name
        if not path.resolve().is_relative_to((root/'qa').resolve()):
            raise ValueError('QA path escapes the evidence directory')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('QA source hash mismatch: '+name)
    if report['schema'] != 'lite.replica-quota-acceptance/1' or not report['passed']:
        raise ValueError('probe did not complete')
    if report['image_source'] != parity['image_source']:
        raise ValueError('image/source mismatch')
    if len(parity['matched']) != 72 or len(parity['models_verified_in_image']) != 3 or report['packaged_files'] != 75:
        raise ValueError('incomplete source parity')
    if stopped != dict(state='Stopped',host_mounts=False,cloud_access=False):
        raise ValueError('VM not confirmed stopped')
    for field in ('real_apple','real_gcs_kms','external_network','cloud_capacity_tested','clinical_accuracy_tested'):
        if report[field] is not False:
            raise ValueError('scope exceeded: '+field)
    if not all(report[f] for f in ('production_code_from_image','real_http','research_state_unchanged','worker_restarted')):
        raise ValueError('acceptance incomplete')
    if report['independent_gunicorn_processes'] != 2 or report['shared_durable_store'] != 'SQLite':
        raise ValueError('unexpected runtime')
    if not (report['model_calls'] == report['used'] == report['attempts'] == 10):
        raise ValueError('duplicate or missing model/usage')
    events = report['model_events']
    if len(events) != 10 or len({e['pid'] for e in events}) != 2 or sum(e['first'] for e in events) != 1:
        raise ValueError('model event mismatch')
    if report['source_image_sha256'] != report['synthetic_original_sha256']:
        raise ValueError('source image changed')
    receipts = report['receipts']
    if set(receipts) != {str(n) for n in range(1,11)}:
        raise ValueError('receipt set mismatch')
    operation_bindings = set()
    for n, r in receipts.items():
        receipt = r['receipt']
        if (r['operation'] != format(int(n),'032x') or receipt['operation'] != r['operation']
                or r['day'] != receipt['day'] or r['state'] != 'completed'
                or receipt['outcome'] != 'succeeded' or r['research_stored'] is not False):
            raise ValueError('receipt identity/state mismatch')
        if receipt['admitted_limit'] != (2 if int(n) <= 2 else 10):
            raise ValueError('free/Plus limit mixed')
        if (hashlib.sha256(r['result_json'].encode()).hexdigest() != receipt['result_sha256']
                or json.loads(r['result_json']) != r['result']):
            raise ValueError('result/receipt content mismatch')
        if r['result']['student_output_contract'] != 'student-onnx-logits-sigmoid-before-resize/v1':
            raise ValueError('unexpected model output contract')
        # The content digest intentionally excludes operation ID: all ten
        # explicit operations use the same synthetic image and model version.
        operation_bindings.add((receipt['operation'],receipt['request_digest']))
    if len(operation_bindings) != 10:
        raise ValueError('duplicate operation binding')
    cases = {v['case']:v for v in report['observations']}
    overlap, free, upgrade, restart = [cases[n] for n in ('overlapping_operation','free_limit',
        'upgrade_and_confirmation_replay','plus_limit_and_restart')]
    if overlap['duplicate_http'] != 202 or not overlap['second_completed_while_first_held']:
        raise ValueError('cross-process execution not covered')
    if (free['used'],free['next_http'],free['response']['reason']) != (2,429,'success_quota'):
        raise ValueError('free quota mismatch')
    if upgrade != dict(case='upgrade_and_confirmation_replay',used=2,confirmation_equal=True,extra_apple_queries=0):
        raise ValueError('upgrade reset or replay mismatch')
    if (restart['used'],restart['attempts'],restart['next_http'],restart['response']['reason']) != (10,10,429,'success_quota'):
        raise ValueError('Plus quota mismatch')
    if any(restart[k] != 10 for k in ('unchanged_receipts','status_reads','run_replays')):
        raise ValueError('incomplete restart readback')
    if restart['extra_model_calls'] or restart['extra_apple_queries']:
        raise ValueError('replay caused external work')
    return dict(passed=True,qa_files=len(qa),receipt_hashes_verified=10,
        model_calls=10,used=10,attempts=10,artifact_contract_only=True,service_rerun=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence',type=Path)
    print(json.dumps(verify(parser.parse_args().evidence),sort_keys=True))
