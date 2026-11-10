"""Offline single-step bootstrap review/signing from an existing owner backup.

No Google credentials, observer signing, cloud calls, key generation or execution.
The independent observer and real runner recheck current state at execution.
"""
import argparse
import base64
import copy
import json
import secrets
import sys
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import lite_operational_materials as custody
from lite_fresh_bootstrap import STAGES
from lite_fresh_bootstrap_runner import checked, preflight, read_regular
from lite_subscription_executor import canonical
from lite_usage_ledger import digest, _hex


def require(value, message):
    if not value: raise ValueError(message)


def prepare(raw, native_raw, receipt_raw, *, expected_config, expected_native,
            expected_receipt, expected_source, expected_image, stage, recover,
            nonce, clock=lambda:int(time.time())):
    """Build public unsigned payloads; never infer observer facts or change state."""
    now=clock();require(type(now) is int and 0<=now<2**53, 'integer clock required')
    require(stage in STAGES and type(recover) is bool, 'explicit single stage required');_hex(nonce)
    c=checked(raw,expected_config,256*1024)
    inspect=dict(schema='lite-bootstrap-request/1',plan=digest(c['plan']),mode='inspect',command=None,inner=None)
    c,_,_=preflight(raw,native_raw,canonical(inspect),expected_config=expected_config,
        expected_native=expected_native,expected_request=digest(inspect),expected_source=expected_source,
        expected_image=expected_image,clock=lambda:now)
    receipt=checked(receipt_raw,expected_receipt,32768)
    fields={'schema','mode','plan','request','source','image','result','inspection','allocation',
            'provider_mutation_requested','customer_ingress_changed','audit_preserved','automatic_recovery'}
    require(set(receipt)==fields and receipt['schema']=='lite-bootstrap-runner-receipt/2'
        and receipt['mode']=='inspect' and receipt['plan']==digest(c['plan'])
        and receipt['request']==digest(inspect) and receipt['source']==expected_source
        and receipt['image']==expected_image and receipt['allocation'] is None
        and receipt['provider_mutation_requested'] is False and receipt['customer_ingress_changed'] is False
        and receipt['audit_preserved'] is True and receipt['automatic_recovery'] is False,
        'pinned read-only runner receipt required')
    snap=receipt['inspection']
    require(type(snap) is dict and set(snap)=={'observed_at','quota','release'}
        and type(snap['observed_at']) is int and 0<=now-snap['observed_at']<=60,
        'fresh inspection required')
    require(now//86400==c['plan']['usage']['day'], 'original UTC day required')
    prior=receipt['result']
    if prior is not None:
        require(type(prior) is dict and set(prior)=={'v','plan','stage','status','attempt','started_at','intent','result'}
            and type(prior['v']) is int and prior['v']==1 and prior['plan']==digest(c['plan'])
            and prior['stage'] in STAGES and prior['status'] in ('running','complete')
            and type(prior['attempt']) is int and 1<=prior['attempt']<=8
            and type(prior['started_at']) is int and 0<=prior['started_at']<=snap['observed_at'],
            'valid observed control required')
        _hex(prior['intent'])
        if prior['status']=='complete':_hex(prior['result'])
        else:require(prior['result'] is None,'running step cannot contain a result')
    if recover:
        require(prior is not None and prior['status']=='running' and prior['stage']==stage
                and prior['attempt']<8, 'recovery requires this exact running step')
    else:
        require((prior is None or prior['status']=='complete') and STAGES.index(stage)==
                (STAGES.index(prior['stage'])+1 if prior else 0), 'ordered next step or explicit recovery required')
    expires=min(now+300,c['provider_budget']['expires_at'],(now//86400+1)*86400)
    require(expires>now,'closed execution window')
    common=dict(kind='lite-migration-approval/1',issued_at=now,expires_at=expires)
    inner=None;inner_signed=False
    if stage=='subscription':
        inner=dict(common,manifest=digest(c['plan']['subscription']),action='activate-fresh',
                   expected_state=digest(None),nonce=digest(['inner',nonce]));inner_signed=True
    elif stage=='quota':
        quota=snap['quota']
        if quota is not None:
            require(type(quota) is dict and quota.get('plan_sha256')==digest(c['plan']['usage']),
                    'quota observation differs')
            if quota.get('phase')=='paused':
                require(recover,'paused quota needs explicit recovery')
                inner=dict(action='resume-paused',expected_pause_sha256=digest(quota))
    else:
        release=snap['release']
        if release is not None:
            require(recover and type(release) is dict and release.get('release')==digest(c['plan']['release'])
                    and release.get('phase')=='active', 'changed or paused release requires separate review')
        inner=dict(common,manifest=digest(c['plan']['release']),
                   action='service-release-confirm' if release else 'service-release-publish',
                   expected_state=digest(release),nonce=digest(['inner',nonce]));inner_signed=True
    outer=dict(common,manifest=digest(c['plan']),action='bootstrap-recover' if recover else 'bootstrap-step',
               stage=stage,expected_state=digest(prior),nonce=nonce)
    return dict(schema='lite-bootstrap-command-draft/1',config=expected_config,native=expected_native,
                receipt=expected_receipt,source=expected_source,image=expected_image,
                command=outer,inner=inner,sign_inner=inner_signed,observer_created=False,cloud_calls=0)


def sign(draft, raw, native_raw, receipt_raw, *, material, expected_manifest, key_id, clock=lambda:int(time.time())):
    """Only the existing operator role may sign these reconstructed payloads."""
    now=clock();require(type(now) is int, 'integer clock required')
    require(type(draft) is dict and type(draft.get('command')) is dict,'reviewed draft required')
    command=draft['command']
    require(type(command.get('issued_at')) is int and type(command.get('expires_at')) is int
            and command['issued_at']<=now<command['expires_at'], 'draft expired')
    args=dict(expected_config=draft['config'],expected_native=draft['native'],expected_receipt=draft['receipt'],
              expected_source=draft['source'],expected_image=draft['image'],stage=command['stage'],
              recover=command['action']=='bootstrap-recover',nonce=command['nonce'])
    expected=prepare(raw,native_raw,receipt_raw,**args,clock=lambda:command['issued_at'])
    require(draft==expected, 'draft differs from inspected inputs')
    receipt=checked(receipt_raw,draft['receipt'],32768)
    require(0<=now-receipt['inspection']['observed_at']<=60, 'inspect again before signing')
    native=checked(native_raw,draft['native'],256*1024)
    material.validate();_hex(expected_manifest)
    require(digest(material.manifest())==expected_manifest, 'independent custody manifest differs')
    scope=material.scope
    require(scope['environment']=='owner-sandbox' and scope['project']==native['deployment']['project']
            and scope['namespace']==native['namespace'] and scope['audience']==native['app']['bundle']
            and scope['issuer']=='https://appleid.apple.com','operator scope differs')
    private=Ed25519PrivateKey.from_private_bytes(material.keys['operator'])
    public=base64.b64encode(private.public_key().public_bytes_raw()).decode()
    require(native['controls']['operator_keys'].get(key_id)==public
            and public not in native['controls']['observer_keys'].values(), 'pinned independent operator required')
    def signed(payload):
        return dict(key_id=key_id,payload=copy.deepcopy(payload),
                    signature=base64.b64encode(private.sign(canonical(payload))).decode())
    inner=signed(draft['inner']) if draft['sign_inner'] else copy.deepcopy(draft['inner'])
    request=dict(schema='lite-bootstrap-request/1',plan=command['manifest'],mode='execute',
                 command=signed(dict(command,inner_sha256=digest(inner))),inner=inner)
    preflight(raw,native_raw,canonical(request),expected_config=draft['config'],expected_native=draft['native'],
              expected_request=digest(request),expected_source=draft['source'],expected_image=draft['image'],clock=lambda:now)
    return request


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='action',required=True)
    for name in ('prepare','sign'):
        q=s.add_parser(name)
        for n in ('config','native','inspection','output'):q.add_argument('--'+n,required=True)
        if name=='prepare':
            for n in ('config','native','inspection'):q.add_argument('--'+n+'-sha256',required=True)
            for n in ('source','image'):q.add_argument('--'+n,required=True)
            q.add_argument('--stage',choices=STAGES,required=True);q.add_argument('--recover',action='store_true')
        else:
            for n in ('draft','draft-sha256','backup','custody-receipt','key-id'):q.add_argument('--'+n,required=True)
    a=p.parse_args()
    raw=read_regular(a.config,256*1024);native=read_regular(a.native,256*1024);inspection=read_regular(a.inspection,32768)
    custody.available_destination(a.output)
    if a.action=='prepare':
        result=prepare(raw,native,inspection,expected_config=a.config_sha256,expected_native=a.native_sha256,
            expected_receipt=a.inspection_sha256,expected_source=a.source,expected_image=a.image,
            stage=a.stage,recover=a.recover,nonce=secrets.token_hex(32))
    else:
        draft=checked(read_regular(a.draft,32768),a.draft_sha256,32768)
        require(sys.stdin.isatty(),'interactive owner terminal required')
        config=checked(raw,draft['config'],256*1024)
        print(json.dumps(dict(project=config['deployment']['project'],namespace=config['plan']['namespace'],
            source=draft['source'],image=draft['image'],plan=draft['command']['manifest'],stage=draft['command']['stage'],
            action=draft['command']['action'],expires_at=draft['command']['expires_at']),sort_keys=True),flush=True)
        require(input('Type SIGN_BOOTSTRAP for this reviewed single step: ')=='SIGN_BOOTSTRAP','signing cancelled')
        backup=custody.read_private(a.backup)
        pin=custody.check_receipt(backup,custody.read_private(a.custody_receipt))
        material=custody.restore(backup,custody.password_prompt('Recovery passphrase: '),expected_manifest_sha256=pin)
        result=sign(draft,raw,native,inspection,material=material,expected_manifest=pin,key_id=a.key_id)
    value=canonical(result);custody._write_exclusive(a.output,value)
    require(custody.read_private(a.output)==value,'output readback differs; preserve file')
    print(json.dumps(dict(saved=True,sha256=digest(result),signed=a.action=='sign',cloud_calls=0)))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps(dict(status='stopped',error_type=type(error).__name__,
                              action='preserve existing files; no automatic signing or execution')))
        raise SystemExit(1)
