import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import check_mmh_runtime_preflight as m
from medical_image_build import configuration, IMAGE_ROOT, BUCKET
from plan_mmh_runtime import generate, canonical
import hashlib

COMMIT = 'a'*40
MANIFEST = 'b'*64
BUILD = '12345678-1234-1234-1234-123456789abc'
DIGEST = 'sha256:'+'c'*64


def plan():
    return generate(IMAGE_ROOT+'@'+DIGEST, COMMIT, MANIFEST, {k:'1' for k in m.SECRET_NAMES})


def build():
    row = configuration(COMMIT, MANIFEST)
    row.update(id=BUILD, projectId=m.PROJECT, status='SUCCESS',
               source={'storageSource':{'bucket':BUCKET,'object':'source/test.tgz','generation':'123'}},
               results={'images':[{'name':row['images'][0],'digest':DIGEST}]})
    row['sourceProvenance']={'resolvedStorageSource':copy.deepcopy(row['source']['storageSource'])}
    return row


class FakeCloud:
    def __init__(self):
        self.calls=[]
        self.edit=lambda args,row: row

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ['projects','describe']:
            row={'projectId':m.PROJECT,'projectNumber':m.NUMBER,'lifecycleState':'ACTIVE'}
        elif args[:2] == ['projects','get-iam-policy']:
            row={'bindings':[],'etag':'fresh'}
        elif args[:2] == ['builds','describe']:
            row=build()
        elif args[:3] == ['artifacts','docker','images']:
            row={'image_summary':{'digest':DIGEST,'fully_qualified_digest':IMAGE_ROOT+'@'+DIGEST}}
        elif args[:3] == ['iam','service-accounts','describe']:
            row={'email':m.SA,'projectId':m.PROJECT,'disabled':False}
        elif args[:3] == ['storage','buckets','describe']:
            name=args[3][5:]
            row={'name':name,'projectNumber':m.NUMBER,'location':'ASIA-EAST1','storageClass':'STANDARD',
                 'iamConfiguration':{'uniformBucketLevelAccess':{'enabled':True},'publicAccessPrevention':'enforced'},
                 'softDeletePolicy':{'retentionDurationSeconds':'0'}}
            if name == m.BUCKETS[2]:
                row['retentionPolicy']={'isLocked':True,'retentionPeriod':'220903200'}
        elif args[:3] == ['run','services','list']:
            row=[{'metadata':{'name':'woundai-backend'}}]
        elif args[:3] == ['secrets','versions','describe']:
            name=args[4].split('=',1)[1]
            row={'name':f'projects/{m.NUMBER}/secrets/{name}/versions/{args[3]}','state':'ENABLED'}
        else:
            raise AssertionError('unexpected command')
        return self.edit(args,row)


class PreflightTests(unittest.TestCase):
    def inspect(self, cloud=None, main=COMMIT):
        return m.inspect(plan(), BUILD, cloud or FakeCloud(), lambda:main)

    def test_all_metadata_green_never_means_deployable(self):
        c=FakeCloud();r=self.inspect(c)
        self.assertTrue(r['metadata_passed']);self.assertTrue(r['complete'])
        self.assertEqual(r['passed'],13);self.assertFalse(r['deployable'])
        self.assertFalse(r['secret_payloads_read']);self.assertFalse(r['cloud_mutations'])
        self.assertTrue(r['remaining_evidence'])
        self.assertFalse(any('access' in args or 'deploy' in args for args in c.calls))

    def test_bad_plan_and_build_id_make_no_cloud_requests(self):
        c=FakeCloud();p=plan();p['spec']['environment']['WOUNDAI_STORE']='local'
        p['spec_sha256']=hashlib.sha256(canonical(p['spec'])).hexdigest()
        for candidate,bid in [(p,BUILD),(plan(),'latest')]:
            with self.assertRaises(ValueError):m.inspect(candidate,bid,c,lambda:COMMIT)
        self.assertEqual(c.calls,[])

    def test_project_failure_stops_remaining_queries(self):
        for edit in [lambda a,r: {},lambda a,r: {'projectId':m.PROJECT,'projectNumber':'999','lifecycleState':'ACTIVE'}]:
            c=FakeCloud();c.edit=edit;r=self.inspect(c)
            self.assertFalse(r['complete']);self.assertFalse(r['metadata_passed'])
            self.assertEqual(len(c.calls),1)

    def test_cloud_and_remote_errors_never_become_evidence(self):
        def fail(*args):raise RuntimeError('sensitive diagnostic must stay private')
        r=m.inspect(plan(),BUILD,fail,lambda:COMMIT)
        self.assertNotIn('sensitive',json.dumps(r));self.assertFalse(r['complete'])
        r=m.inspect(plan(),BUILD,FakeCloud(),fail)
        self.assertFalse(r['metadata_passed']);self.assertNotIn('sensitive',json.dumps(r))

    def test_draft_source_blocks(self):
        r=self.inspect(main='d'*40)
        self.assertFalse(r['metadata_passed'])
        self.assertEqual([x['check'] for x in r['checks'] if not x['passed']],['source_is_current_remote_main'])

    def test_immutable_provenance_mismatch_blocks(self):
        c=FakeCloud()
        c.edit=lambda a,r: dict(r,status='WORKING') if a[0]=='builds' else r
        self.assertFalse(self.inspect(c)['metadata_passed'])

    def test_each_bucket_identity_and_privacy_mismatch_blocks(self):
        for bucket in m.BUCKETS:
            for key,value in [('projectNumber','999'),('name','other'),('location','US'),
                              ('iamConfiguration',{}),('softDeletePolicy',{}),
                              ('lifecycle',{'rule':[{'action':{'type':'Delete'}}]})]:
                c=FakeCloud()
                c.edit=lambda a,r: dict(r,**{key:value}) if a[:3]==['storage','buckets','describe'] and a[3]=='gs://'+bucket else r
                with self.subTest(bucket=bucket,key=key):self.assertFalse(self.inspect(c)['metadata_passed'])

    def test_unlocked_unknown_and_wrong_retention_rejected(self):
        for policy in [{},{'isLocked':False,'retentionPeriod':'220903200'},
                       {'isLocked':'true','retentionPeriod':'220903200'},
                       {'isLocked':True,'retentionPeriod':'86400'}]:
            c=FakeCloud()
            c.edit=lambda a,r: dict(r,retentionPolicy=policy) if a[:4]==['storage','buckets','describe','gs://'+m.BUCKETS[2]] else r
            self.assertFalse(self.inspect(c)['metadata_passed'])

    def test_disabled_runtime_rejected(self):
        c=FakeCloud();c.edit=lambda a,r: dict(r,disabled=True) if a[0]=='iam' else r
        self.assertFalse(self.inspect(c)['metadata_passed'])

    def test_editor_even_conditional_and_incomplete_policy_block(self):
        for policy in [{},{'bindings':[]}, {'etag':'x','bindings':[{'role':'roles/editor',
                      'members':['serviceAccount:'+m.OLD_RUNTIME], 'condition':{'expression':'false'}}]}]:
            c=FakeCloud();c.edit=lambda a,r: policy if a[:2]==['projects','get-iam-policy'] else r
            self.assertFalse(self.inspect(c)['metadata_passed'])

    def test_existing_or_unreadable_service_inventory_blocks(self):
        for rows in [None,{},[{}],[{'metadata':{'name':m.SERVICE}}]]:
            c=FakeCloud();c.edit=lambda a,r: rows if a[0]=='run' else r
            self.assertFalse(self.inspect(c)['metadata_passed'])

    def test_wrong_secret_name_number_version_and_unknown_state_block(self):
        for field,value in [('name',f'projects/{m.PROJECT}/secrets/test/versions/1'),
                            ('name',f'projects/{m.NUMBER}/secrets/test/versions/2'),
                            ('state','DISABLED'),('state','UNKNOWN'),('state',None)]:
            c=FakeCloud();c.edit=lambda a,r: dict(r,**{field:value}) if a[0]=='secrets' else r
            self.assertFalse(self.inspect(c)['metadata_passed'])

    def test_previous_green_report_invalidated_on_bad_input(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'plan';p.write_text('{}');out=Path(td)/'report';out.write_text('{"metadata_passed":true}')
            r=m.write_report(p,BUILD,out,FakeCloud(),lambda:COMMIT)
            self.assertFalse(r['metadata_passed']);self.assertFalse(json.loads(out.read_text())['complete'])

    def test_adapter_rejects_mutations_and_payload_access_before_subprocess(self):
        with patch.object(m.subprocess,'run') as run:
            for args in [['secrets','versions','access','1'],['run','deploy','anything'],
                         ['projects','set-iam-policy','anything'],['storage','buckets','update','anything']]:
                with self.assertRaises(ValueError):m.cloud(args)
            run.assert_not_called()

    def test_adapter_fixes_project_and_does_not_echo_stderr(self):
        with patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','private diagnostic')) as run:
            with self.assertRaisesRegex(RuntimeError,'^cloud metadata unavailable$'):
                m.cloud(['projects','describe',m.PROJECT])
            self.assertIn('--project='+m.PROJECT,run.call_args.args[0])


if __name__=='__main__':unittest.main(verbosity=2)
