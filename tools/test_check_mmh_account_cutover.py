import copy
from datetime import datetime, timedelta, timezone
import unittest
from check_mmh_account_cutover import check, SERVICE, PROJECT, STORAGE, IDENTITY

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
TARGET = 'asia-east1-docker.pkg.dev/woundai-jackh001/woundai-medical-build/medical@sha256:' + 'a'*64


class CutoverTests(unittest.TestCase):
    def setUp(self):
        self.data = dict(project=PROJECT, service=SERVICE, observed_at=NOW.isoformat(), iap_enabled='true',
            traffic=[dict(revisionName=SERVICE+'-new',percent=100),dict(revisionName=SERVICE+'-new',tag='candidate')],
            revisions=[dict(name=SERVICE+'-new',image=TARGET,identity=IDENTITY,storage=copy.deepcopy(STORAGE))])

    def check(self):return check(self.data,TARGET,now=NOW)

    def test_aligned_exposure_is_not_deployment_authorization(self):
        result=self.check();self.assertTrue(result['exposed_revisions_aligned'])
        self.assertFalse(result['deployment_or_write_authorized']);self.assertTrue(result['remaining_checks'])

    def test_candidate_only_update_rejected(self):
        self.data['traffic'][0]['revisionName']=SERVICE+'-old'
        old=copy.deepcopy(self.data['revisions'][0]);old.update(name=SERVICE+'-old',image=TARGET.replace('a'*64,'b'*64))
        self.data['revisions'].append(old)
        self.assertIn('exposed_old_or_unreviewed_image',self.check()['blockers'])

    def test_zero_percent_old_tag_is_still_an_exposed_writer(self):
        self.data['traffic'].append(dict(revisionName=SERVICE+'-old',tag='old-test'))
        old=copy.deepcopy(self.data['revisions'][0]);old.update(name=SERVICE+'-old',image=TARGET.replace('a'*64,'b'*64))
        self.data['revisions'].append(old);self.assertFalse(self.check()['exposed_revisions_aligned'])

    def test_missing_or_duplicate_revision_rejected(self):
        original=copy.deepcopy(self.data)
        for rows in ([],original['revisions']*2):
            self.data['revisions']=rows;self.assertFalse(self.check()['exposed_revisions_aligned'])

    def test_stale_future_and_naive_time_rejected(self):
        for stamp in (NOW-timedelta(seconds=901),NOW+timedelta(seconds=1),NOW.replace(tzinfo=None)):
            self.data['observed_at']=stamp.isoformat();self.assertIn('snapshot_not_fresh',self.check()['blockers'])

    def test_storage_identity_or_iap_change_rejected(self):
        original=copy.deepcopy(self.data)
        for edit in ('storage','identity','iap'):
            self.data=copy.deepcopy(original)
            if edit=='storage':self.data['revisions'][0]['storage']['WOUNDAI_GCS_PREFIX']='other'
            elif edit=='identity':self.data['revisions'][0]['identity']='other@example.test'
            else:self.data['iap_enabled']='false'
            self.assertFalse(self.check()['exposed_revisions_aligned'])

    def test_traffic_must_be_resolved_and_total_100(self):
        for value in (99,True,-1,'100'):
            self.data['traffic'][0]['percent']=value;self.assertFalse(self.check()['exposed_revisions_aligned'])

    def test_latest_or_foreign_image_refused(self):
        for image in ('medical:latest',TARGET.replace(PROJECT,'foreign')):
            with self.assertRaises(ValueError):check(self.data,image,now=NOW)


if __name__ == '__main__':unittest.main()
