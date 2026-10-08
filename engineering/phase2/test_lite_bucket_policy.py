"""Candidate bucket policy, actual identity checks, and startup refusal before CAS."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend'/'Flask'))
from lite_bucket_policy import validate_bucket_policy
import lite_attest_config as config
from check_lite_candidate_buckets import check


def metadata(name='synthetic-security'):
    return dict(kind='storage#bucket',name=name,projectNumber='123456789012',metageneration='7',location='ASIA-EAST1',
                iamConfiguration=dict(publicAccessPrevention='enforced',uniformBucketLevelAccess=dict(enabled=True)),
                softDeletePolicy=dict(retentionDurationSeconds='0'))


def environment():
    return dict(WOUNDAI_LITE_ATTEST_APP_ID='ABCDEFGHIJ.com.woundai.lite',WOUNDAI_LITE_ATTEST_VERSIONS='33',
                WOUNDAI_LITE_ATTEST_AUDIENCE='lite-public',WOUNDAI_LITE_SECURITY_PROJECT='synthetic-project',
                WOUNDAI_LITE_SECURITY_PROJECT_NUMBER='123456789012',WOUNDAI_LITE_SECURITY_BUCKET='synthetic-security',
                WOUNDAI_LITE_BUDGET_MINUTE='20',WOUNDAI_LITE_BUDGET_DAY='100')


class BucketPolicyTests(unittest.TestCase):
    def validate(self,data,**kwargs):
        return validate_bucket_policy(data,name='synthetic-security',project_number='123456789012',role='security',**kwargs)
    def test_explicit_mutable_private_bucket_is_accepted(self):
        report=self.validate(metadata(),location='ASIA-EAST1')
        self.assertEqual(report['metageneration'],'7');self.assertIn('not IAM',report['scope'])
    def test_wrong_name_project_or_resource_type_rejected(self):
        for key,value in [('name','other'),('projectNumber','synthetic-project'),('projectNumber','999999999999'),('kind','storage#object')]:
            data=metadata();data[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):self.validate(data)
    def test_project_id_or_unknown_number_is_not_numeric_evidence(self):
        for value in [None,'synthetic-project','0','001234567890',123456789012]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                validate_bucket_policy(metadata(),name='synthetic-security',project_number=value,role='security')
    def test_retention_default_hold_or_versioning_rejected(self):
        for key,value in [('retentionPolicy',{}),('retentionPolicy',dict(retentionPeriod='60')),('defaultEventBasedHold',True),
                          ('defaultEventBasedHold','false'),('versioning',dict(enabled=True)),('versioning',dict(enabled=0))]:
            data=metadata();data[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):self.validate(data)
    def test_soft_delete_missing_unknown_or_nonzero_rejected(self):
        for value in [None,{},dict(retentionDurationSeconds='604800'),dict(retentionDurationSeconds=0)]:
            data=metadata();data['softDeletePolicy']=value
            with self.subTest(value=value),self.assertRaises(ValueError):self.validate(data)
    def test_lifecycle_cannot_delete_withdrawal_state_or_change_storage_class(self):
        for action in ['Delete','SetStorageClass','UnknownFutureAction']:
            data=metadata();data['lifecycle']=dict(rule=[dict(action=dict(type=action),condition=dict(age=1))])
            with self.subTest(action=action),self.assertRaises(ValueError):self.validate(data)
    def test_uniform_iam_and_public_access_prevention_must_be_explicit(self):
        for value in [None,{},dict(publicAccessPrevention='inherited',uniformBucketLevelAccess=dict(enabled=True)),
                      dict(publicAccessPrevention='enforced',uniformBucketLevelAccess=dict(enabled='true'))]:
            data=metadata();data['iamConfiguration']=value
            with self.subTest(value=value),self.assertRaises(ValueError):self.validate(data)
    def test_region_and_metageneration_are_verified(self):
        with self.assertRaises(ValueError):self.validate(metadata(),location='US')
        for value in [None,'0',1,'unknown']:
            data=metadata();data['metageneration']=value
            with self.subTest(value=value),self.assertRaises(ValueError):self.validate(data)
    def test_media_and_security_must_be_distinct(self):
        with self.assertRaises(ValueError):check(media=metadata(),security=metadata(),media_name='synthetic-security',
            security_name='synthetic-security',project_number='123456789012',location='ASIA-EAST1')
    def test_missing_new_config_rejects_before_google_client(self):
        env=environment();del env['WOUNDAI_LITE_SECURITY_PROJECT_NUMBER']
        with patch.object(config.storage,'Client') as client,self.assertRaises(ValueError):config.build_lite_security(env)
        client.assert_not_called()
    def test_startup_reloads_before_using_security_store(self):
        with patch.object(config,'_refuse_cloud_in_test_process'),patch.object(config.storage,'Client') as client:
            bucket=client.return_value.bucket.return_value;bucket._properties=metadata()
            def changed(**kwargs):bucket._properties['lifecycle']=dict(rule=[dict(action=dict(type='Delete'))])
            bucket.reload.side_effect=changed
            with self.assertRaises(ValueError):config.build_lite_security(environment())
            bucket.reload.assert_called_once_with(timeout=10,retry=None)
    def test_readback_failure_never_uses_cached_good_properties(self):
        with patch.object(config,'_refuse_cloud_in_test_process'),patch.object(config.storage,'Client') as client:
            bucket=client.return_value.bucket.return_value;bucket._properties=metadata();bucket.reload.side_effect=OSError('offline')
            with self.assertRaises(OSError):config.build_lite_security(environment())
    def test_cli_accepts_valid_pair_and_rejects_realistic_versioned_media(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);media=p/'media.json';security=p/'security.json'
            media.write_text(json.dumps(metadata('synthetic-media')));security.write_text(json.dumps(metadata()))
            command=[sys.executable,'-B',str(Path(__file__).with_name('check_lite_candidate_buckets.py')),
                '--media-metadata',str(media),'--security-metadata',str(security),'--media-bucket','synthetic-media',
                '--security-bucket','synthetic-security','--project-number','123456789012']
            result=subprocess.run(command,capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stderr)
            data=metadata('synthetic-media');data['versioning']=dict(enabled=True);media.write_text(json.dumps(data))
            result=subprocess.run(command,capture_output=True,text=True);self.assertEqual(result.returncode,2)
            self.assertEqual(json.loads(result.stdout)['status'],'blocked')


if __name__=='__main__':unittest.main()
