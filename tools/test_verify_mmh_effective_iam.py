import copy
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import json
from urllib.error import HTTPError
from verify_mmh_effective_iam import verify
from verify_mmh_effective_iam import assess,cases

class V3Tests(unittest.TestCase):
    def response(self,granted=True):
        return {'accessTuple':{'principal':'p','fullResourceName':'r','permission':'x'},
                'overallAccessState':'CAN_ACCESS' if granted else 'CANNOT_ACCESS',
                'allowPolicyExplanation':{'allowAccessState':'ALLOW_ACCESS_STATE_GRANTED'},
                'denyPolicyExplanation':{'denyAccessState':'DENY_ACCESS_STATE_NOT_DENIED' if granted else 'DENY_ACCESS_STATE_DENIED'}}
    def test_deny_overrides_allow(self):
        self.assertTrue(assess(self.response(False),'p','r','x',False)['passed'])
    def test_allow_and_control(self):
        self.assertTrue(assess(self.response(),'p','r','x',True)['passed'])
        self.assertFalse(assess(self.response(),'p','r','x',False)['passed'])
    def test_unknown_and_missing_are_not_evidence(self):
        for section,field in [('allowPolicyExplanation','allowAccessState'),('denyPolicyExplanation','denyAccessState')]:
            for unknown in ('UNKNOWN','UNKNOWN_INFO_DENIED','UNKNOWN_CONDITIONAL',None):
                r=self.response(False);r[section][field]=unknown
                self.assertFalse(assess(r,'p','r','x',False)['passed'])
        r=self.response(False);r['overallAccessState']='UNKNOWN_INFO_DENIED'
        self.assertFalse(assess(r,'p','r','x',False)['passed'])
    def test_absent_allow_deny_proves_refusal(self):
        r=self.response(False);r['allowPolicyExplanation']['allowAccessState']='ALLOW_ACCESS_STATE_NOT_GRANTED'
        r['denyPolicyExplanation']['denyAccessState']='DENY_ACCESS_STATE_NOT_DENIED'
        self.assertTrue(assess(r,'p','r','x',False)['passed'])
    def test_wrong_echo_never_counts(self):
        for k in ('principal','fullResourceName','permission'):
            r=self.response(False);r['accessTuple'][k]='other'
            with self.assertRaises(ValueError):assess(r,'p','r','x',False)
    def test_legacy_v1_cannot_pass(self):
        with self.assertRaises(ValueError):assess({'access':'NOT_GRANTED'},'p','r','x',False)
    def test_inconsistent_overall_rejected(self):
        r=self.response();r['overallAccessState']='CANNOT_ACCESS'
        self.assertFalse(assess(r,'p','r','x',False)['passed'])
    def test_credential_failure_invalidates_old_green_report(self):
        with tempfile.TemporaryDirectory() as td:
            report=Path(td)/'report.json';report.write_text('{"complete":true,"passed":40,"total":40}')
            with patch('verify_mmh_effective_iam.access_token',side_effect=RuntimeError('unavailable')):
                with self.assertRaises(RuntimeError):verify(report)
            self.assertFalse(json.loads(report.read_text())['complete'])
    def test_quota_failure_preserves_partial_evidence_and_fails(self):
        with tempfile.TemporaryDirectory() as td:
            report=Path(td)/'report.json'
            with patch('verify_mmh_effective_iam.access_token',return_value='synthetic'), \
                 patch('verify_mmh_effective_iam.cases',return_value=[('p','r','x',True)]*2), \
                 patch('verify_mmh_effective_iam.time.sleep'), \
                 patch('verify_mmh_effective_iam.request_v3',side_effect=[self.response(),HTTPError('https://example.invalid',429,'quota',{},None)]):
                with self.assertRaises(HTTPError):verify(report)
            result=json.loads(report.read_text());self.assertFalse(result['complete'])
            self.assertEqual(result['http_status'],429);self.assertEqual(len(result['completed']),1)
    def test_case_matrix_includes_controls_and_impersonation(self):
        rows=cases();self.assertEqual(len(rows),40);self.assertEqual(len(set(rows)),40)
        self.assertEqual(sum(c[-1] for c in rows),5)
        self.assertTrue(any(c[2]=='iam.serviceAccountKeys.create' for c in rows))
    def test_least_privilege_rejects_legacy_bucket_deletion(self):
        rows=cases('least-privilege')
        targets=[r for r in rows if r[2]=='storage.buckets.delete' and 'flywheel-jackh001' in r[1]]
        self.assertEqual(len(targets),1)
        self.assertFalse(targets[0][-1])
        self.assertEqual(len(rows),51);self.assertEqual(len(set(rows)),51)
    def test_legacy_data_and_secrets_remain_required(self):
        rows=cases('least-privilege')
        for permission in ('storage.objects.get','storage.objects.list','storage.objects.create','storage.objects.delete'):
            self.assertTrue(any(r[2]==permission and r[-1] and r[1].endswith('/woundai-flywheel-jackh001') for r in rows))
        secrets=[r for r in rows if r[2]=='secretmanager.versions.access']
        self.assertEqual(len(secrets),2)
        self.assertTrue(all('/projects/421209514056/secrets/' in r[1] and r[-1] for r in secrets))
    def test_least_privilege_keeps_all_mmh_refusals(self):
        required={r for r in cases() if not r[-1]}
        self.assertTrue(required.issubset(set(cases('least-privilege'))))
        for permission in ('iam.serviceAccounts.delete','iam.serviceAccounts.disable','iam.serviceAccounts.update'):
            self.assertTrue(any(r[2]==permission and not r[-1] for r in cases('least-privilege')))
    def test_unknown_profile_invalidates_old_evidence_before_auth(self):
        with tempfile.TemporaryDirectory() as td:
            report=Path(td)/'report.json';report.write_text('{"complete":true}')
            with patch('verify_mmh_effective_iam.access_token') as token:
                with self.assertRaises(ValueError):verify(report,'typo')
                token.assert_not_called()
            self.assertFalse(json.loads(report.read_text())['complete'])

if __name__=='__main__':unittest.main(verbosity=2)
