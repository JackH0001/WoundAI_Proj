"""Real owner terminal, synthetic encrypted backup, no cloud or Apple connection."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import secrets
from types import SimpleNamespace

from PIL import Image
import lite_bootstrap_commands as commands
import lite_operational_materials as custody
import lite_role_custody as role_custody
from lite_operational_materials_probe import terminal
from lite_subscription_executor import canonical
from lite_usage_ledger import digest
import test_lite_bootstrap_commands as fixtures
import test_lite_fresh_bootstrap as bootstrap


def run(*,role_separated=False):
    fixtures.CommandTests.setUpClass()
    owner=fixtures.CommandTests(); transcripts=[]
    password=secrets.token_urlsafe(32)
    passwords=[password]
    try:
        owner.setUp();h=owner.f.h
        root=h.release.n.f.root.resolve()/'owner-command-probe';root.mkdir(mode=0o700)
        backup_dir,receipt_dir=root/'backup',root/'independent-receipt'
        backup_dir.mkdir(mode=0o700);receipt_dir.mkdir(mode=0o700)
        backup,receipt=backup_dir/'material.json',receipt_dir/'receipt.json'
        custody.save_pair(custody.seal(owner.material,password),backup,receipt)
        pair=(backup.read_bytes(),receipt.read_bytes()); original=owner.f.untouched()
        source_backup,source_receipt=backup,receipt
        # The CLI has no clock override. Only this synthetic subprocess wrapper
        # fixes time and forbids sockets, so terminal tests cannot reach a service.
        wrapper=root/'synthetic-clock.py'
        wrapper.write_text('import runpy, socket, time\n'
            'def forbidden(*a, **kw): raise AssertionError("no network")\n'
            'socket.socket.connect = forbidden\nsocket.socket.connect_ex = forbidden\n'
            f'time.time = lambda: {h.clock()}\n'
            f'runpy.run_path({str(Path(commands.__file__).resolve())!r}, run_name="__main__")\n')
        role_paths={}
        if role_separated:
            role_wrapper=root/'synthetic-role-terminal.py'
            role_wrapper.write_text('import runpy, socket\n'
                'def forbidden(*a, **kw): raise AssertionError("no network")\n'
                'socket.socket.connect = forbidden\nsocket.socket.connect_ex = forbidden\n'
                f'runpy.run_path({str(Path(role_custody.__file__).resolve())!r}, run_name="__main__")\n')
            for role in ('operator','observer'):
                private=root/(role+'-backup');pins=root/(role+'-independent-receipt')
                private.mkdir(mode=0o700);pins.mkdir(mode=0o700)
                output=private/'role.json';out_receipt=pins/'receipt.json'
                role_password=secrets.token_urlsafe(32);passwords.append(role_password)
                export=['export-role','--backup',source_backup,'--receipt',source_receipt,'--role',role,
                        '--output',output,'--role-receipt',out_receipt]
                prompts=[(b'export this role only: ',b'EXPORT_ONE_ROLE'),
                    (b'Original backup recovery passphrase: ',password.encode()),
                    (b'Independent role recovery passphrase: ',role_password.encode()),
                    (b'Repeat role recovery passphrase: ',role_password.encode())]
                if role=='operator':
                    code,text=terminal(role_wrapper,export,prompts[:2]+[
                        (b'Independent role recovery passphrase: ',password.encode())]);transcripts.append(text)
                    assert code!=0 and not output.exists() and not out_receipt.exists()
                code,text=terminal(role_wrapper,export,prompts);transcripts.append(text)
                assert code==0 and output.stat().st_mode&0o777==0o600,'role CLI export failed'
                saved=output.read_bytes(),out_receipt.read_bytes()
                restored,pin=role_custody.restore_for_role(saved[0],role_password,saved[1],role)
                assert set(restored.keys)=={role} and restored.keys[role]==owner.material.keys[role] and pin==owner.pin
                verify=['verify-role','--backup',output,'--receipt',out_receipt,'--role',role]
                code,text=terminal(role_wrapper,verify,[(b'Role recovery passphrase: ',role_password.encode())]);transcripts.append(text)
                assert code==0 and b'"restored_roles": 1' in text
                if role=='operator':
                    code,text=terminal(role_wrapper,export);transcripts.append(text)
                    assert code!=0 and saved==(output.read_bytes(),out_receipt.read_bytes())
                    code,text=terminal(role_wrapper,verify[:-1]+['observer']);transcripts.append(text)
                    assert code!=0 and b'Role recovery passphrase:' not in text
                role_paths[role]=(output,out_receipt,role_password,saved)
            backup,receipt,password,_=role_paths['operator']
        for stage in ('subscription','quota','release'):
            pinned=owner.prepare(stage)
            paths=[]
            for name,data in zip(('config','native','inspection'),owner.inputs):
                path=root/(stage+'-'+name+'.json');path.write_bytes(data)
                paths.extend(['--'+name,str(path)])
            draft_path=root/(stage+'-draft.json');request_path=root/(stage+'-request.json')
            prepare=['prepare',*paths,'--output',draft_path,'--stage',stage,
                '--config-sha256',pinned['config'],'--native-sha256',pinned['native'],
                '--inspection-sha256',pinned['receipt'],'--source',pinned['source'],'--image',pinned['image']]
            state=owner.snapshot();calls=owner.providers.connected
            code,text=terminal(wrapper,prepare);transcripts.append(text)
            assert code==0 and draft_path.stat().st_mode&0o777==0o600,'prepare CLI failed'
            draft=json.loads(draft_path.read_bytes())
            args=['sign',*paths,'--output',request_path,'--draft',draft_path,'--draft-sha256',digest(draft),
                  '--backup',backup,'--custody-receipt',receipt,'--key-id','operator']
            confirm=(b'Type SIGN_BOOTSTRAP for this reviewed single step: ',b'SIGN_BOOTSTRAP')
            if stage=='subscription':
                code,text=terminal(wrapper,args,[(confirm[0],b'CANCEL')]);transcripts.append(text)
                assert code!=0 and not request_path.exists() and b'Recovery passphrase:' not in text
                code,text=terminal(wrapper,args,[confirm,(b'Recovery passphrase: ',(password+'-wrong').encode())]);transcripts.append(text)
                assert code!=0 and not request_path.exists()
                if role_separated:
                    observer_args=list(args)
                    observer_args[observer_args.index('--backup')+1]=role_paths['observer'][0]
                    observer_args[observer_args.index('--custody-receipt')+1]=role_paths['observer'][1]
                    code,text=terminal(wrapper,observer_args,[confirm]);transcripts.append(text)
                    assert code!=0 and not request_path.exists() and b'Recovery passphrase:' not in text
            code,text=terminal(wrapper,args,[confirm,(b'Recovery passphrase: ',password.encode())]);transcripts.append(text)
            assert code==0 and request_path.stat().st_mode&0o777==0o600,'sign CLI failed'
            assert owner.snapshot()==state and owner.providers.connected==calls,'CLI must not execute'
            request=json.loads(request_path.read_bytes());saved=request_path.read_bytes()
            if stage=='subscription':
                code,text=terminal(wrapper,args);transcripts.append(text)
                assert code!=0 and request_path.read_bytes()==saved and b'SIGN_BOOTSTRAP' not in text
            completed=owner.invoke(request)
            assert completed['result']['stage']==stage and completed['result']['status']=='complete'
            if stage=='subscription':
                assert owner.invoke(request)['result']==completed['result'],'same signed replay differs'
        assert owner.f.untouched()==original and pair==(source_backup.read_bytes(),source_receipt.read_bytes())
        for output,pins,_,saved in role_paths.values():assert saved==(output.read_bytes(),pins.read_bytes())
        host=bootstrap.BootstrapTests();host.h=h;host.backend=h.compose();host.client=host.backend.create_app().test_client()
        host.e=h.e;host.key=bootstrap.b64(h.e.key_id);host.counter=0;host.login_serial=992
        host.register();host.login();photo=io.BytesIO();Image.new('RGB',(64,64),(200,130,110)).save(photo,format='JPEG')
        host.f=SimpleNamespace(jpeg=photo.getvalue());host.image_hash=hashlib.sha256(host.f.jpeg).hexdigest()
        first=host.complete(1);again=host.run_request(1)
        assert first.json==again.json and (h.model_calls,host.quota()['used'])==(1,1)
        assert all(secret.encode() not in t and b'Traceback' not in t for secret in passwords for t in transcripts)
        for raw in owner.material.keys.values():
            assert all(raw.hex().encode() not in t and bootstrap.b64(raw).encode() not in t for t in transcripts)
        return dict(schema='lite-bootstrap-command-probe/1',synthetic_only=True,terminal_processes=len(transcripts),
            custody_mode='separate-role' if role_separated else 'combined-owner',
            role_export_and_restore_verified=role_separated,observer_signing_refused=role_separated,
            actual_holder_separation_verified=False,original_combined_backup_retired=False,
            stages=['subscription','quota','release'],owner_confirmation_required=True,
            cancelled_and_wrong_password_refused=True,overwrite_refused=True,private_file_mode='0600',
            private_material_echoed=False,original_backup_and_receipt_preserved=True,
            signing_provider_calls=0,network_connections=0,prior_records_and_audits_preserved=True,
            repeated_result_equal=True,model_calls=h.model_calls,quota_used=host.quota()['used'],
            deployment_performed=False)
    finally:owner.doCleanups()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--role-separated',action='store_true')
    args=parser.parse_args();output=args.output
    if output.exists():parser.error('choose a new evidence path')
    result=run(role_separated=args.role_separated)
    with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2);stream.write('\n')
