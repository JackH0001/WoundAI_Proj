import copy
import unittest

import plan_mmh_iap_entry as m


def baseline():
    return {'name': m.RUN_NAME, 'uid': m.UID, 'etag': 'fresh', 'generation': '2',
            'observedGeneration': '2', 'terminalCondition': {'state': 'CONDITION_SUCCEEDED'},
            'traffic': copy.deepcopy(m.TRAFFIC),
            'trafficStatuses': copy.deepcopy(m.TRAFFIC_STATUS),
            'template': {'serviceAccount': f'woundai-mmhps20261007-runtime@{m.PROJECT}.iam.gserviceaccount.com',
                         'containers': [{'image': m.IMAGE}]}}


class EntryPlanTests(unittest.TestCase):
    def setUp(self):
        self.s = baseline()
        self.empty = {'etag': 'fresh'}
        self.settings = {'name': m.IAP_NAME}
        self.p = m.propose(self.s, self.empty, self.settings, self.empty)

    def after(self, **overrides):
        s = copy.deepcopy(self.s); s.update(iapEnabled=True, etag='next', generation='3', observedGeneration='3')
        args = dict(proposal=self.p, service=s,
                    run_policy={'etag': 'next', 'bindings': copy.deepcopy(m.RUN_BINDING)},
                    access_policy={'etag': 'next', 'bindings': copy.deepcopy(m.IAP_BINDING)},
                    settings={'name': m.IAP_NAME, 'accessSettings': {'oauthSettings': {
                        'clientId': m.WEB, 'programmaticClients': [m.IOS], 'clientSecretSha256': 'a'*64}}},
                    expected_secret_sha256='a'*64)
        args.update(overrides)
        return args

    def test_plan_is_not_execution_or_authorization(self):
        self.assertFalse(self.p['spec']['cloud_writes_authorized'])
        self.assertEqual(self.p['spec']['run_update_mask'], 'iapEnabled')
        self.assertEqual(self.p['spec']['protected']['traffic'], m.TRAFFIC)

    def test_exact_readback_is_only_configuration_evidence(self):
        r = m.check_after(**self.after())
        self.assertTrue(r['direct_resource_configuration_passed'])
        self.assertFalse(r['live_authentication_verified'])
        self.assertFalse(r['inherited_access_verified'])

    def test_wrong_resource_or_readiness_refused(self):
        for edit in [dict(name=m.RUN_NAME+'-other'), dict(uid='new'), dict(etag=''),
                     dict(generation='3'), dict(reconciling=True), dict(deleteTime='now'),
                     dict(terminalCondition={}), dict(invokerIamDisabled=True), dict(iapEnabled=True)]:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                m.propose(dict(self.s, **edit), self.empty, self.settings, self.empty)

    def test_observed_traffic_required_even_when_requested_traffic_matches(self):
        a=self.after();a['service']['trafficStatuses'][0]['percent']=99
        with self.assertRaises(ValueError):m.check_after(**a)

    def test_existing_iap_settings_not_overwritten(self):
        for s in [{}, {'name': m.IAP_NAME+'-other'}, dict(self.settings, accessSettings={})]:
            with self.subTest(s=s), self.assertRaises(ValueError):
                m.propose(self.s, self.empty, s, self.empty)

    def test_missing_policy_evidence_refused(self):
        for p in [{}, {'etag':''}, {'etag':'x','version':True}, {'etag':'x','unknown':[]}]:
            with self.subTest(p=p), self.assertRaises(ValueError):
                m.propose(self.s, p, self.settings, self.empty)

    def test_baseline_image_runtime_and_traffic_bound(self):
        for key in ('image', 'runtime', 'traffic'):
            s=copy.deepcopy(self.s)
            if key=='image': s['template']['containers'][0]['image']='other'
            elif key=='runtime': s['template']['serviceAccount']='other'
            else: s['traffic'][0]['percent']=99
            with self.subTest(key=key), self.assertRaises(ValueError):
                m.propose(s, self.empty, self.settings, self.empty)

    def test_other_members_and_conditions_refused_after(self):
        for principal in ('allUsers','allAuthenticatedUsers','group:team@example.com',
                          m.AGENT.replace(m.NUMBER,'123'), 'user:peer@example.com'):
            a=self.after();a['run_policy']['bindings'][0]['members'].append(principal)
            with self.subTest(principal=principal), self.assertRaises(ValueError):m.check_after(**a)
        for key in ('run_policy', 'access_policy'):
            a=self.after();a[key]['bindings'][0]['condition']={'expression':'true'}
            with self.subTest(key=key), self.assertRaises(ValueError):m.check_after(**a)

    def test_extra_roles_and_duplicate_bindings_refused(self):
        for key in ('run_policy','access_policy'):
            a=self.after();a[key]['bindings'].append(copy.deepcopy(a[key]['bindings'][0]))
            with self.subTest(key=key), self.assertRaises(ValueError):m.check_after(**a)
            a=self.after();a[key]['bindings'][0]['role']='roles/owner'
            with self.subTest(key=key), self.assertRaises(ValueError):m.check_after(**a)

    def test_template_and_traffic_cannot_change_with_entry(self):
        for key in ('template','traffic','scaling','ingress','invokerIamDisabled','labels'):
            a=self.after();a['service'][key]={}
            with self.subTest(key=key), self.assertRaises(ValueError):m.check_after(**a)

    def test_wrong_client_allowlist_secret_or_unknown_settings_refused(self):
        for key,val in [('clientId',m.IOS),('programmaticClients',[m.IOS,m.WEB]),
                        ('clientSecretSha256','b'*64),('clientSecret','must-not-be-here'),
                        ('loginHint','unreviewed')]:
            a=self.after();a['settings']['accessSettings']['oauthSettings'][key]=val
            with self.subTest(key=key), self.assertRaises(ValueError):m.check_after(**a)

    def test_iap_disabled_and_bad_digest_refused(self):
        a=self.after();a['service']['iapEnabled']=False
        with self.assertRaises(ValueError):m.check_after(**a)
        for value in ('',None,'not-a-digest'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.check_after(**self.after(expected_secret_sha256=value))

    def test_console_explicit_disabled_defaults_allowed_without_mutating_input(self):
        a=self.after()
        a['settings']['accessSettings']['allowedDomainsSettings']={'enable':False}
        a['settings']['applicationSettings']={'attributePropagationSettings':{'enable':False}}
        before=copy.deepcopy(a)
        self.assertTrue(m.check_after(**a)['direct_resource_configuration_passed'])
        self.assertEqual(a,before)

    def test_saved_web_secret_without_mobile_allowlist_is_not_acceptance(self):
        a=self.after()
        del a['settings']['accessSettings']['oauthSettings']['programmaticClients']
        a['settings']['accessSettings']['allowedDomainsSettings']={'enable':False}
        a['settings']['applicationSettings']={'attributePropagationSettings':{'enable':False}}
        with self.assertRaises(ValueError):m.check_after(**a)

    def test_console_controls_enabled_extra_fields_and_non_boolean_refused(self):
        for section, control in [('accessSettings','allowedDomainsSettings'),
                                 ('applicationSettings','attributePropagationSettings')]:
            for value in ({'enable':True},{'enable':0},{'enable':'false'},
                          {'enable':False,'unreviewed':'value'},{}):
                a=self.after();a['settings'].setdefault(section,{})[control]=value
                with self.subTest(section=section,value=value), self.assertRaises(ValueError):
                    m.check_after(**a)

    def test_proposal_tamper_and_rehashed_scope_change_refused(self):
        p=copy.deepcopy(self.p);p['spec']['run_resource']='projects/other'
        with self.assertRaises(ValueError):m.check_after(**self.after(proposal=p))
        p['sha256']=m.digest(p['spec'])
        with self.assertRaises(ValueError):m.check_after(**self.after(proposal=p))


if __name__ == '__main__':
    unittest.main()
