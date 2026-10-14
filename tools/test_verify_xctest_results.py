import unittest
from verify_xctest_results import verify, PROTECTION_TEST, PROTECTION_REASON, FOLDER_TEST, FOLDER_REASON

class XCTestEvidenceTests(unittest.TestCase):
    def fixture(self):
        summary = dict(passedTests=1, failedTests=0, skippedTests=1, totalTestCount=2,
                       expectedFailures=0, result='Passed',
                       devicesAndConfigurations=[{'device': {'platform': 'iOS Simulator'}}])
        tree = [{'nodeType':'Test Case','result':'Passed','nodeIdentifier':'Other/test()'},
                {'nodeType':'Test Case','result':'Skipped','nodeIdentifier':PROTECTION_TEST,
                 'children':[{'nodeType':'Skip Message','name':PROTECTION_REASON}]}]
        return summary, tree
    def test_documented_simulator_limitation_remains_explicit(self):
        self.assertTrue(verify(*self.fixture(), True)['physical_file_protection_validation_pending'])
    def test_skip_is_not_allowed_by_default(self):
        with self.assertRaises(ValueError): verify(*self.fixture())
    def test_xcode_failure_message_label_does_not_change_skipped_outcome(self):
        s,t=self.fixture()
        t[1]['children'][0]['nodeType']='Failure Message'
        self.assertTrue(verify(s,t,True)['physical_file_protection_validation_pending'])
        t[1]['result']='Failed'
        with self.assertRaises(ValueError):verify(s,t,True)
    def test_extra_or_unknown_explanation_is_not_allowed(self):
        for message in ('Another failure', PROTECTION_REASON):
            s,t=self.fixture()
            t[1]['children'].append({'nodeType':'Failure Message','name':message})
            with self.assertRaises(ValueError):verify(s,t,True)
    def test_physical_device_or_missing_device_evidence_rejected(self):
        for devices in ([], [{'device':{'platform':'iOS'}}], [{}]):
            s,t=self.fixture();s['devicesAndConfigurations']=devices
            with self.assertRaises(ValueError):verify(s,t,True)
    def test_wrong_case_reason_and_missing_tree_rejected(self):
        for mutation in ('identity','reason','tree'):
            s,t=self.fixture()
            if mutation=='identity':t[1]['nodeIdentifier']='Security/testMustRun()'
            if mutation=='reason':t[1]['children'][0]['name']='No credentials'
            if mutation=='tree':t.pop()
            with self.assertRaises(ValueError):verify(s,t,True)
    def test_bad_counts_failures_and_no_tests_rejected(self):
        for field,value in [('failedTests',1),('expectedFailures',1),('passedTests',0),
                            ('totalTestCount',3),('skippedTests',2),('skippedTests',True)]:
            s,t=self.fixture();s[field]=value
            with self.assertRaises(ValueError):verify(s,t,True)
    def folder_fixture(self):
        s,t=self.fixture()
        t[1]['nodeIdentifier']=FOLDER_TEST
        t[1]['children'][0]['name']=FOLDER_REASON
        return s,t
    def test_folder_grant_remains_pending_and_requires_its_own_opt_in(self):
        s,t=self.folder_fixture()
        for protection in (False,True):
            with self.assertRaises(ValueError):verify(s,t,protection)
        result=verify(s,t,allow_system_folder_grant_skip=True)
        self.assertTrue(result['system_folder_acceptance_pending'])
        self.assertFalse(result['physical_file_protection_validation_pending'])
        with self.assertRaises(ValueError):verify(*self.fixture(),allow_system_folder_grant_skip=True)
    def test_folder_exception_rejects_wrong_reason_identity_device_or_failure(self):
        for mutation in ('reason','identity','device','failure','missing_reason'):
            s,t=self.folder_fixture()
            if mutation=='reason':t[1]['children'][0]['name']='Folder write failed'
            if mutation=='identity':t[1]['nodeIdentifier']='Export/testIntegrity()'
            if mutation=='device':s['devicesAndConfigurations']=[{'device':{'platform':'iOS'}}]
            if mutation=='failure':t[1]['result']='Failed'
            if mutation=='missing_reason':t[1]['children']=[]
            with self.assertRaises(ValueError):verify(s,t,allow_system_folder_grant_skip=True)
    def test_zero_skips_passes_without_exception(self):
        s,t=self.fixture();s.update(skippedTests=0,totalTestCount=1)
        self.assertFalse(verify(s,t[:1])['physical_file_protection_validation_pending'])

if __name__=='__main__':unittest.main(verbosity=2)
