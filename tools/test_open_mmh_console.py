import argparse
import copy
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch
import open_mmh_console as m


def service():
    return {'metadata':{'name':m.SERVICE},'status':{'url':m.ORIGIN,
            'conditions':[{'type':'Ready','status':'True'}],'latestReadyRevisionName':'mmh-revision'},
            'spec':{'template':{'spec':{'containers':[{'env':[
                {'name':k,'value':v} for k,v in {
                    'WOUNDAI_INSTITUTION_ORG':'mmhps20261007','WOUNDAI_SERVICE_PROFILE':'medical',
                    'WOUNDAI_ENABLE_LITE_API':'0','WOUNDAI_STORE':'gcs',
                    'WOUNDAI_AUDIT_MODE':'mmh-unlocked-validation'}.items()]}]}}}}


class ProxyTests(unittest.TestCase):
    def test_private_policies_reject_service_grants_and_public_project_members(self):
        owner={'etag':'test','bindings':[{'role':'roles/owner','members':['user:jack@example.com']}]}
        m.validate_policies({},owner)
        for member in ('allUsers','allAuthenticatedUsers','group:staff@example.com'):
            policy={'etag':'test','bindings':[{'role':'roles/run.invoker','members':[member]}]}
            with self.assertRaises(ValueError):m.validate_policies(policy,owner)
            with self.assertRaises(ValueError):m.validate_policies({},policy)

    def test_project_identity_and_no_unreviewed_ancestors(self):
        valid={'projectId':m.PROJECT,'projectNumber':'421209514056'}
        m.validate_project(valid)
        for edit in ({'projectId':'other'},{'projectNumber':'wrong'},{'parent':{'id':'123','type':'organization'}}):
            with self.subTest(edit=edit),self.assertRaises(ValueError):m.validate_project({**valid,**edit})

    def test_accepts_private_mmh(self):
        self.assertEqual(m.validate(service()),'mmh-revision')

    def test_rejects_public_invoker_bypass(self):
        s=service();s['metadata']['annotations']={'run.googleapis.com/invoker-iam-disabled':'true'}
        with self.assertRaises(ValueError):m.validate(s)

    def test_rejects_wrong_service_and_origin(self):
        for edit in [lambda s:s['metadata'].update(name='woundai-backend'),
                     lambda s:s['status'].update(url='https://example.org'),
                     lambda s:s['status'].update(url=m.ORIGIN+'/console')]:
            s=service();edit(s)
            with self.assertRaises(ValueError):m.validate(s)

    def test_rejects_not_ready(self):
        s=service();s['status']['conditions'][0]['status']='False'
        with self.assertRaises(ValueError):m.validate(s)

    def test_rejects_each_wrong_environment_value(self):
        rows=service()['spec']['template']['spec']['containers'][0]['env']
        for i in range(len(rows)):
            s=service();s['spec']['template']['spec']['containers'][0]['env'][i]['value']='wrong'
            with self.subTest(i=i),self.assertRaises(ValueError):m.validate(s)

    def test_rejects_missing_duplicate_secret_reference_environment(self):
        for mutation in ('missing','duplicate','reference'):
            s=service();rows=s['spec']['template']['spec']['containers'][0]['env']
            if mutation=='missing':rows.pop()
            elif mutation=='duplicate':rows.append(copy.deepcopy(rows[0]))
            else:rows[0]={'name':rows[0]['name'],'valueFrom':{'secretKeyRef':{'name':'unexpected'}}}
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):m.validate(s)

    def test_rejects_multiple_containers(self):
        s=service();s['spec']['template']['spec']['containers'].append({})
        with self.assertRaises(ValueError):m.validate(s)

    def test_duration_is_bounded(self):
        for v in ('0','3601','-1'):
            with self.assertRaises(argparse.ArgumentTypeError):m.duration(v)
        self.assertEqual(m.duration('3600'),3600)

    def test_command_is_fixed_without_token_or_public_bind(self):
        self.assertEqual(m.command(),['gcloud','run','services','proxy',m.SERVICE,
            '--project=woundai-jackh001','--region=asia-east1','--port=8765','--quiet','--verbosity=error'])

    @patch('open_mmh_console.os.killpg')
    @patch('open_mmh_console.subprocess.Popen')
    def test_timeout_closes_entire_own_process_group(self, popen, kill):
        child=Mock(pid=123);child.wait.side_effect=[subprocess.TimeoutExpired('proxy',1),0];popen.return_value=child
        self.assertEqual(m.run_proxy(1,{}),0)
        kill.assert_called_once_with(123,signal.SIGTERM)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])

    @patch('open_mmh_console.os.killpg')
    @patch('open_mmh_console.subprocess.Popen')
    def test_interruption_cleans_up(self,popen,kill):
        child=Mock(pid=123);child.wait.side_effect=[KeyboardInterrupt(),0];popen.return_value=child
        self.assertEqual(m.run_proxy(1,{}),130);kill.assert_called_once_with(123,signal.SIGTERM)

    @patch('open_mmh_console.os.killpg')
    @patch('open_mmh_console.subprocess.Popen')
    def test_failed_proxy_exit_is_not_success(self,popen,kill):
        child=Mock(pid=123);child.wait.return_value=1;popen.return_value=child
        self.assertEqual(m.run_proxy(1,{}),1)


if __name__=='__main__':unittest.main()
