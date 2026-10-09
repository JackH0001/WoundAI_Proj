"""Open a time-limited, loopback-only official proxy to the private MMH console.

No secret values are read. Cloud Run IAM and application login remain separate.
"""
import argparse
import json
import os
import signal
import subprocess
from urllib.parse import urlsplit
from create_mmh_private_service import check_project, exact_bindings

PROJECT = 'woundai-jackh001'
REGION = 'asia-east1'
SERVICE = 'woundai-backend-mmhps20261007'
ORIGIN = 'https://woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app'
LOCAL = 'http://127.0.0.1:8765/console'


def validate(service):
    if service.get('metadata', {}).get('name') != SERVICE:
        raise ValueError('wrong service')
    spec = service.get('spec', {})
    annotations = service.get('metadata', {}).get('annotations', {})
    if annotations.get('run.googleapis.com/invoker-iam-disabled', 'false') != 'false':
        raise ValueError('private invoker check required')
    status = service.get('status', {})
    if status.get('url') != ORIGIN or urlsplit(status.get('url', '')).scheme != 'https':
        raise ValueError('wrong origin')
    if not any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in status.get('conditions', [])):
        raise ValueError('service is not ready')
    containers = spec.get('template', {}).get('spec', {}).get('containers', [])
    if len(containers) != 1:
        raise ValueError('unexpected containers')
    rows = containers[0].get('env', [])
    names = [r.get('name') for r in rows]
    if len(names) != len(set(names)):
        raise ValueError('duplicate environment keys')
    env = {r.get('name'): r.get('value') for r in rows}
    required = {'WOUNDAI_INSTITUTION_ORG':'mmhps20261007', 'WOUNDAI_SERVICE_PROFILE':'medical',
                'WOUNDAI_ENABLE_LITE_API':'0', 'WOUNDAI_STORE':'gcs',
                'WOUNDAI_AUDIT_MODE':'mmh-unlocked-validation'}
    if any(env.get(k) != v for k,v in required.items()):
        raise ValueError('MMH institution configuration mismatch')
    return status.get('latestReadyRevisionName')


def command():
    # gcloud itself fixes the bind host to 127.0.0.1. No custom host/token flags.
    return ['gcloud','run','services','proxy',SERVICE,'--project='+PROJECT,
            '--region='+REGION,'--port=8765','--quiet','--verbosity=error']


def validate_policies(service_policy, project_policy):
    exact_bindings(service_policy, {})
    check_project(project_policy)


def validate_project(project):
    if (project.get('projectId') != PROJECT or str(project.get('projectNumber')) != '421209514056'
            or project.get('parent')):
        raise ValueError('project identity or ancestor scope changed')


def duration(value):
    number = int(value)
    if not 1 <= number <= 3600:
        raise argparse.ArgumentTypeError('duration must be 1..3600 seconds')
    return number


def run_proxy(seconds, environment):
    child = subprocess.Popen(command(), env=environment, start_new_session=True)
    try:
        return child.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        print('MMH local proxy reached its time limit; closing.', flush=True)
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        # End this proxy's process group only, including the gcloud-managed binary.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=duration,default=3600)
    parser.add_argument('--check',action='store_true',help='read configuration only; do not open a listener')
    args = parser.parse_args()
    environment = dict(os.environ, CLOUDSDK_CORE_LOG_HTTP='false', CLOUDSDK_CORE_VERBOSITY='error')
    result = subprocess.run(['gcloud','run','services','describe',SERVICE,'--project='+PROJECT,
                             '--region='+REGION,'--format=json','--quiet'],
                            env=environment,capture_output=True,text=True,timeout=60)
    if result.returncode:
        raise RuntimeError('Cannot read MMH service; check gcloud sign-in and access. No proxy started.')
    revision = validate(json.loads(result.stdout))
    project = subprocess.run(['gcloud','projects','describe',PROJECT,'--format=json','--quiet'],
                             env=environment,capture_output=True,text=True,timeout=60)
    if project.returncode:
        raise RuntimeError('Cannot verify project scope. No proxy started.')
    validate_project(json.loads(project.stdout))
    policies = []
    for args_tail in (['run','services','get-iam-policy',SERVICE,'--region='+REGION],
                      ['projects','get-iam-policy',PROJECT]):
        policy = subprocess.run(['gcloud',*args_tail,'--project='+PROJECT,'--format=json','--quiet'],
                                env=environment,capture_output=True,text=True,timeout=60)
        if policy.returncode:
            raise RuntimeError('Cannot verify private invocation policy. No proxy started.')
        policies.append(json.loads(policy.stdout))
    validate_policies(*policies)
    print(json.dumps({'configuration_valid':True,'revision':revision,'local_console':LOCAL,
                      'loopback_only':True,'duration_seconds':args.seconds}),flush=True)
    if args.check:
        return 0
    def stop(_signum, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, stop)
    print('Use the MMH app account at the local console. Ctrl-C closes this proxy.',flush=True)
    return run_proxy(args.seconds, environment)


if __name__ == '__main__':
    raise SystemExit(main())
