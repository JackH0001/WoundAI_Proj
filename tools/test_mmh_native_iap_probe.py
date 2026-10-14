import copy
import unittest
import plan_mmh_iap_entry as m
from test_plan_mmh_iap_entry import baseline

class NativeProbeTests(unittest.TestCase):
    def setUp(self):
        self.s=baseline();self.empty={'etag':'fresh'}
        self.st={'name':m.IAP_NAME,'accessSettings':{'oauthSettings':{'clientId':m.WEB,'clientSecretSha256':'a'*64}}}
        self.p=m.propose_native_probe(self.s,self.empty,self.st,self.empty)
    def after(self):
        s=copy.deepcopy(self.s);s['iapEnabled']=True
        return [self.p,s,{'etag':'next','bindings':copy.deepcopy(m.RUN_BINDING)},copy.deepcopy(self.st),{'etag':'next','bindings':copy.deepcopy(m.IAP_BINDING)}]
    def test_configuration_never_claims_mobile_or_upload_success(self):
        r=m.check_native_probe(*self.after())
        self.assertTrue(r['probe_configuration_passed'])
        self.assertFalse(r['mobile_authentication_verified']);self.assertFalse(r['upload_acceptance_passed'])
    def test_v1_cannot_be_silently_reused(self):
        args=self.after();args[0]=m.propose(self.s,self.empty,{'name':m.IAP_NAME},self.empty)
        with self.assertRaises(ValueError):m.check_native_probe(*args)
    def test_extra_clients_rejected(self):
        for v in [[],[m.IOS],[m.WEB],['other']]:
            st=copy.deepcopy(self.st);st['accessSettings']['oauthSettings']['programmaticClients']=v
            with self.subTest(v=v),self.assertRaises(ValueError):m.propose_native_probe(self.s,self.empty,st,self.empty)
    def test_unknown_settings_or_plaintext_rejected(self):
        for key,value in [('clientSecret','secret'),('loginHint','test'),('clientId',m.IOS)]:
            st=copy.deepcopy(self.st);st['accessSettings']['oauthSettings'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):m.propose_native_probe(self.s,self.empty,st,self.empty)
    def test_rehashed_scope_tampering_rejected(self):
        for key,value in [('required_token_audience',m.IOS),('expected_mobile_client',m.WEB),('probe_only',False),('run_resource','other'),('programmatic_clients',[m.IOS])]:
            args=self.after();args[0]=copy.deepcopy(self.p);args[0]['spec'][key]=value;args[0]['sha256']=m.digest(args[0]['spec'])
            with self.subTest(key=key),self.assertRaises(ValueError):m.check_native_probe(*args)
    def test_changed_image_traffic_bypass_refused(self):
        for k,v in [('template',{}),('traffic',[]),('iapEnabled',False),('invokerIamDisabled',True),('trafficStatuses',[])]:
            args=self.after();args[1][k]=v
            with self.subTest(k=k),self.assertRaises(ValueError):m.check_native_probe(*args)
    def test_extra_principals_or_roles_refused(self):
        for index in (2,4):
            args=self.after();args[index]['bindings'][0]['members'].append('allUsers')
            with self.subTest(index=index),self.assertRaises(ValueError):m.check_native_probe(*args)
    def test_secret_changed_refused(self):
        args=self.after();args[3]['accessSettings']['oauthSettings']['clientSecretSha256']='b'*64
        with self.assertRaises(ValueError):m.check_native_probe(*args)
    def test_google_canonical_email_case_is_same_known_account(self):
        args=self.after();args[4]['bindings'][0]['members']=['user:Jack.Hou@gmail.com']
        self.assertTrue(m.check_native_probe(*args)['probe_configuration_passed'])
        for member in ['user:jack.hou+other@gmail.com','user:Jack.Hou@example.com','group:Jack.Hou@gmail.com']:
            args=self.after();args[4]['bindings'][0]['members']=[member]
            with self.subTest(member=member),self.assertRaises(ValueError):m.check_native_probe(*args)

if __name__=='__main__':unittest.main()
