"""Exercise the actual Google storage SDK with synthetic provider replies only."""
import base64
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, unquote, urlsplit

import google_crc32c
import requests
from requests.adapters import BaseAdapter
from urllib3.response import HTTPResponse

import test_lite_owner_review_reader as readback
from lite_disposition_bootstrap import google_session
from lite_owner_review_command import execute, main, utc, READ_SCOPE, ExactTransport
from lite_operational_materials import canonical, read_private
from lite_usage_ledger import digest
from lite_attest_state import StateUnavailable
from lite_provider_budget import Allocation


class StorageAdapter(BaseAdapter):
    def __init__(self, fixture):
        self.fixture = fixture
        self.calls = []
        self.deny = None

    def send(self, request, **kwargs):
        url = urlsplit(request.url)
        assert request.method == 'GET' and url.netloc == 'storage.googleapis.com'
        assert request.headers['Authorization'] == 'Bearer synthetic-reader-token'
        name = unquote(url.path.split('/o/',1)[1])
        query = parse_qs(url.query)
        self.calls.append((url.path,query))
        bucket = self.fixture.bucket
        entry = bucket.live.get(name)
        code = 200
        if not entry or self.deny:
            code = self.deny or 404
            data = json.dumps({'error':{'code':code,'message':'unavailable'}}).encode()
        elif url.path.startswith('/download/'):
            generation = int(query['generation'][0])
            assert int(query['ifGenerationMatch'][0]) == generation and query['alt'] == ['media']
            data = bucket.versions[(name,generation)]
        else:
            generation,body = entry
            data = json.dumps({'name':name,'bucket':self.fixture.bucket_name,'generation':str(generation),
                               'size':str(len(body)),'crc32c':self.crc(body)}).encode()
        response = requests.Response()
        response.request = request
        response.status_code = code
        response.url = request.url
        headers={'Content-Length':str(len(data)), 'Content-Type':'application/json',
                 'x-goog-hash':'crc32c='+self.crc(data)}
        response.headers.update(headers)
        response.raw=HTTPResponse(body=io.BytesIO(data),headers=headers,preload_content=False,decode_content=False)
        return response

    @staticmethod
    def crc(data):
        return base64.b64encode(google_crc32c.value(data).to_bytes(4,'big')).decode()

    def close(self):
        pass


class CommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        readback.ReadbackTests.setUpClass()

    def setUp(self):
        fixture = readback.ReadbackTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.fixture = fixture
        self.project = 'synthetic-lite'
        native = fixture.native
        native['deployment'].update(project=self.project,
                                    service_account='native-runtime@synthetic-lite.iam.gserviceaccount.com')
        fixture.plan['native_sha256']=digest(native)
        self.request=dict(schema='lite-owner-readback-command/1',plan=fixture.plan,
                          installation=fixture.installation,operator='owner@example.invalid',project=self.project,
                          reader='review-reader@synthetic-lite.iam.gserviceaccount.com')
        self.issuer=Mock()
        self.issuer.return_value.request.return_value={'accessToken':'synthetic-reader-token',
                                                      'expireTime':utc(fixture.now+120)}
        self.wire = StorageAdapter(fixture)
        base=tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        self.directory=Path(base.name).resolve()
        self.output=self.directory/'owner-receipt.json'
        p=patch.dict(os.environ,{'DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA':'true'})
        p.start()
        self.addCleanup(p.stop)

    def run_command(self,mode='inspect',**overrides):
        def session(credentials):
            s=google_session(credentials)
            s.mount('https://',self.wire)
            return s
        options=dict(mode=mode,output=self.output,clock=lambda:self.fixture.now,monotonic=lambda:1.,
                     operator_transport=self.issuer,session_factory=session)
        execute(self.fixture.raw(),self.request,digest(self.request),**dict(options,**overrides))

    def test_scope_mode_offline_five_exact_names_and_time_bounds(self):
        self.run_command('scope')
        self.issuer.assert_not_called()
        result=json.loads(read_private(self.output))
        self.assertEqual(result['token_scope'],READ_SCOPE)
        self.assertEqual(len(result['bindings']),1)
        binding=result['bindings'][0]
        self.assertEqual(binding['permissions'],['storage.objects.get'])
        self.assertEqual(binding['expression'].count('resource.name =='),5)
        self.assertNotIn('startsWith',binding['expression'])
        self.assertIn(utc(self.fixture.now),binding['expression'])
        self.assertIn(utc(self.request['plan']['until']),binding['expression'])
        self.assertEqual(self.wire.calls,[])

    def test_scope_can_precede_review_window_but_inspect_waits(self):
        before = lambda: self.fixture.now - 1
        self.run_command('scope', clock=before)
        expression = json.loads(read_private(self.output))['bindings'][0]['expression']
        self.assertIn(utc(self.fixture.now), expression)
        with self.assertRaisesRegex(ValueError, 'outside owner review window'):
            self.run_command(clock=before, output=self.directory / 'too-early.json')
        self.issuer.assert_not_called()
        self.assertEqual(self.wire.calls, [])

    def test_expired_request_produces_no_scope_or_readback(self):
        expired = lambda: self.request['plan']['until']
        for mode in ('scope', 'inspect'):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.run_command(mode, clock=expired)
        self.issuer.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_configuration_fingerprint_and_emulator_checked_before_auth(self):
        with self.assertRaisesRegex(ValueError, 'native configuration differs'):
            execute(self.fixture.raw() + b' ', self.request, digest(self.request), mode='inspect',
                    output=self.output, clock=lambda: self.fixture.now, operator_transport=self.issuer)
        with patch.dict(os.environ, STORAGE_EMULATOR_HOST='http://localhost'), self.assertRaises(ValueError):
            self.run_command()
        self.issuer.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_cost_and_credentials_of_normal_wire_readback(self):
        self.run_command()
        envelope=json.loads(read_private(self.output))
        self.assertEqual(envelope['storage_gets'],18)
        self.assertEqual(len(self.wire.calls),18)
        self.assertEqual(envelope['receipt']['account_sha256'], self.fixture.proposal['account_sha256'])
        self.assertEqual(self.output.stat().st_mode & 0o777,0o600)
        self.issuer.assert_called_once_with(self.request['operator'])
        self.issuer.return_value.request.assert_called_once_with('POST','iamcredentials.googleapis.com',
            '/v1/projects/-/serviceAccounts/'+self.request['reader']+':generateAccessToken',
            {'scope':[READ_SCOPE],'lifetime':'120s'})
        self.assertNotIn('synthetic-reader-token',self.output.read_text())
        self.assertNotIn(self.fixture.op,self.output.read_text())
        self.assertNotIn(self.fixture.installation,self.output.read_text())

    def test_transport_only_accepts_current_metadata_and_pinned_content(self):
        backend = Mock()
        allocation = Allocation(dict(requests=18, send_bytes=0, receive_bytes=18*32768), 'readback',
                                dict(expires_at=150), clock=lambda:100, monotonic=lambda:0.)
        reader = ExactTransport(backend, allocation, {'registration': ('example-state', 'reg'),
                                                    'proposal': ('example-state', 'prop')})
        root = 'https://storage.googleapis.com/'
        meta = 'storage/v1/b/example-state/o/'
        media = 'download/' + meta
        pinned = '?alt=media&generation=21&ifGenerationMatch=21'
        for target in (meta+'reg?alt=media', meta+'prop?generation=2', media+'reg'+pinned,
                       media+'prop?alt=media', media+'prop'+pinned+'&generation=21',
                       media+'prop?alt=media&generation=2&ifGenerationMatch=3'):
            with self.subTest(target=target), self.assertRaises(StateUnavailable):
                reader.request('GET', root+target)
        with self.assertRaises(StateUnavailable):
            reader.request('GET', root+meta+'reg', params={'alt':'media'})
        backend.request.assert_not_called()
        self.assertEqual(allocation.snapshot()['used']['requests'], 0)

    def test_existing_registration_stops_after_metadata_without_its_payload(self):
        self.fixture.write('registration',lambda row:{'v':1})
        with self.assertRaises(ValueError):self.run_command()
        name=self.fixture.objects['registration'][2].hex()+'.json'
        relevant=[p for p,_ in self.wire.calls if p.endswith(name)]
        self.assertEqual(len(relevant),1)
        self.assertFalse(relevant[0].startswith('/download/'))
        self.assertFalse(self.output.exists())

    def test_forbidden_response_is_never_retried_or_treated_as_missing(self):
        self.wire.deny=403
        with self.assertRaises(StateUnavailable):self.run_command()
        self.assertEqual(len(self.wire.calls),1)
        self.assertFalse(self.output.exists())

    def test_existing_file_and_unsafe_destination_refuse_before_auth(self):
        self.output.write_text('keep')
        with self.assertRaises(ValueError):self.run_command()
        self.assertEqual(self.output.read_text(),'keep')
        self.issuer.assert_not_called()
        sub=self.directory/'shared'
        sub.mkdir(mode=0o755)
        sub.chmod(0o755)
        with self.assertRaises(ValueError):self.run_command(output=sub/'receipt')
        self.issuer.assert_not_called()

    def test_mint_failure_never_uses_operator_identity_to_read_gcs(self):
        self.issuer.return_value.request.side_effect=ValueError('rejected')
        with self.assertRaises(ValueError):self.run_command()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.wire.calls,[])

    def test_wrong_expiry_extra_field_or_bad_token_refuses_without_storage_access(self):
        for reply in [{'expireTime':utc(self.fixture.now+400),'accessToken':'abc'},
                      {'expireTime':utc(self.fixture.now-1),'accessToken':'abc'},
                      {'expireTime':utc(self.fixture.now+60),'accessToken':'a b'},
                      {'expireTime':utc(self.fixture.now+60),'accessToken':'abc','other':1}]:
            self.issuer.return_value.request.return_value=reply
            with self.subTest(reply=list(reply)), self.assertRaises(ValueError):self.run_command()
        self.assertEqual(self.wire.calls,[])
        self.assertFalse(self.output.exists())

    def test_wrong_reader_and_operator_are_rejected_before_credentials(self):
        original=copy.deepcopy(self.request)
        for changes in [{'reader':'other@different-project.iam.gserviceaccount.com'},
                        {'reader':self.fixture.native['deployment']['service_account']},
                        {'operator':original['reader']},{'project':'different-project'}]:
            self.request=dict(original,**changes)
            with self.subTest(fields=list(changes)), self.assertRaises(ValueError):self.run_command()
        self.issuer.assert_not_called()

    def export_inputs(self):
        plan = self.request['plan'].copy()
        operation = plan.pop('operation')
        template = dict(self.request, schema='lite-owner-readback-template/1', plan=plan)
        installation = template.pop('installation')
        reference = dict(schema='lite-owner-reference/1', installation=installation, operation=operation,
                         installation_sha256=plan['installation_sha256'],
                         origin=f'https://woundai-lite-native-sandbox-{plan["project_number"]}.asia-east1.run.app')
        return template, reference

    def compose(self, template, reference, expected=None):
        execute(self.fixture.raw(), template, expected or digest(template), mode='compose', output=self.output,
                reference=reference, clock=lambda: self.fixture.now, operator_transport=self.issuer)

    def test_export_composes_an_exact_request_usable_for_scoped_readback(self):
        template, reference = self.export_inputs()
        original = copy.deepcopy(template)
        self.compose(template, reference)
        self.issuer.assert_not_called()
        self.assertEqual(self.wire.calls, [])
        self.assertEqual(template, original)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.request = json.loads(read_private(self.output))
        self.assertEqual(self.request['plan'], self.fixture.plan)
        self.run_command(output=self.directory/'final.json')
        receipt = json.loads(read_private(self.directory/'final.json'))
        self.assertEqual(receipt['storage_gets'], 18)
        self.assertEqual(receipt['receipt']['account_sha256'], self.fixture.proposal['account_sha256'])

    def test_installation_pin_only_export_is_insufficient(self):
        template, reference = self.export_inputs()
        del reference['operation'], reference['installation']
        with self.assertRaises(ValueError):
            self.compose(template, reference)
        self.issuer.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_export_cannot_change_service_installation_time_or_copy_credentials(self):
        template, reference = self.export_inputs()
        for change in ({'origin':'https://research.example.invalid'}, {'operation':'bad'},
                       {'installation':'0'*32}, {'installation_sha256':'0'*64}, {'until':9999999},
                       {'schema':'other'}, {'authorizationCode':'placeholder'}):
            with self.subTest(fields=list(change)), self.assertRaises(ValueError):
                self.compose(template, dict(reference, **change))
        self.assertFalse(self.output.exists())
        self.issuer.assert_not_called()

    def test_different_template_and_extra_operator_fields_are_refused(self):
        template, reference = self.export_inputs()
        for change in ({'reader':'different-reader@synthetic-lite.iam.gserviceaccount.com'},
                       {'plan':dict(template['plan'], until=self.fixture.now+590)}):
            with self.subTest(fields=list(change)), self.assertRaises(ValueError):
                self.compose(dict(template, **change), reference, digest(template))
        with self.assertRaises(ValueError):
            self.compose(dict(template, installation=reference['installation']), reference)
        self.assertFalse(self.output.exists())
        self.issuer.assert_not_called()

    def test_pending_reference_only_consumed_during_offline_composition(self):
        _, reference = self.export_inputs()
        for mode in ('scope', 'inspect'):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.run_command(mode, reference=reference)
        self.issuer.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_command_entrypoint_scope_and_safe_console(self):
        config=self.directory/'native.json'
        config.write_bytes(self.fixture.raw())
        request=self.directory/'request.json'
        request.write_bytes(canonical(self.request))
        request.chmod(0o600)
        argv=['readback','scope','--request',str(request),'--native-config',str(config),'--expected-request',
              digest(self.request),'--output',str(self.output)]
        from contextlib import redirect_stdout,redirect_stderr
        from lite_owner_review_command import execute as original
        def offline(*args,**kwargs):
            return original(*args,**kwargs,clock=lambda:self.fixture.now,operator_transport=self.issuer)
        stdout,stderr=io.StringIO(),io.StringIO()
        with patch.object(sys,'argv',argv), patch('lite_owner_review_command.execute',side_effect=offline):
            with redirect_stdout(stdout),redirect_stderr(stderr):
                self.assertEqual(main(),0)
                self.assertEqual(main(),1)
        self.issuer.assert_not_called()
        for value in (self.fixture.installation,self.fixture.op,self.request['operator'],self.request['reader']):
            self.assertNotIn(value,stdout.getvalue()+stderr.getvalue())
