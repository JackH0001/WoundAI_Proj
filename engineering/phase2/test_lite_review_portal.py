"""Synthetic two-service review; real JWT signatures and production fence formats.

No ADC/network. GCS calls go only to the generation-aware in-memory bucket.
"""
from pathlib import Path
import copy,hashlib,io,json,sys,time,unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend/Flask'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
import jwt
from PIL import Image
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from Backend.LiteReview.contracts import *
from Backend.LiteReview.reader import Reader
from Backend.LiteReview.storage import Source,AccessAudit
from Backend.LiteReview.auth import IapVerifier,ReaderIdentity
from Backend.LiteReview.web import reader_app,portal_app
from Backend.LiteReview.runtime import Backend
from plan_lite_review_rollout import plan
from test_lite_fenced_objects import Bucket
from lite_fenced_store import FencedPrivacy
from lite_attest_state import GCSStateStore
from lite_fenced_objects import ObjectRef
from lite_reconsent import Reconsent,CONSENT,DISCLOSURE

OWNER='a'*32;IID='b'*16;SUB='accounts.google.com:123456';EPOCH='c'*32

class Fixture:
    def __init__(self):
        self.media=Bucket();self.media.name=MEDIA
        self.state=Bucket();self.state.name=STATE
        self.audit=Bucket();self.audit.name=AUDIT
        self.privacy=FencedPrivacy(GCSStateStore(self.state),self.media)
        self.owner,self.iid=OWNER,IID
        self.seed()
        self.viewer=Viewer(SUB,frozenset(CAPS))
        self.backend=Reader(Catalogue(self.catalogue()),Source(self.media,self.state),AccessAudit(self.audit),b's'*32)
    def seed(self,privacy=None,owner=OWNER,iid=IID):
        privacy=privacy or self.privacy
        b=io.BytesIO();Image.new('RGB',(32,24),(200,60,80)).save(b,'JPEG');jpeg=b.getvalue()
        ticket=privacy.begin_write(owner)
        def write(kind,ident,data):
            privacy.manifest.write_registered(ticket,privacy.objects,ObjectRef(owner,kind,ident),data)
        write('image',iid,jpeg)
        meta=dict(anon_id=owner,image_id=iid,image_w=32,image_h=24,research_consent=True,
            consent_version=CONSENT,ai_polygons=[[[1,1],[22,1],[22,20]]],route='untrusted <script>')
        write('metadata',iid,encoded(meta))
        for rev in (2,1):
            slot=hashlib.sha256(json.dumps([owner,iid,rev],separators=(',',':')).encode()).hexdigest()[:16]
            label=dict(anon_id=owner,image_id=iid,image_w=32,image_h=24,revision=rev,
                annotation_receipt_id=slot,payload_sha256=str(rev)*64,polygons=[[[2,2],[23,2],[23,21]]],
                source='manual',label_grade='lay',surface_cm2=17.5+rev,projected_cm2=17+rev)
            write('label',slot,encoded(label))
        privacy.finish_write(ticket)
    def catalogue(self,epoch='legacy',owner=OWNER,iid=IID):
        prefix=object_name(owner,epoch,'image',iid).rsplit('/',2)[0]+'/'
        objects=[]
        for name,(gen,data,meta) in self.media.live.items():
            if name.startswith(prefix):
                kind,ident=name.rsplit('/',2)[-2:]
                objects.append(dict(kind=kind,identifier=ident,generation=gen,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
        return dict(schema='lite-review-catalogue/1',captured_at=int(time.time()),records=[dict(owner=owner,epoch=epoch,image_id=iid,source='synthetic',objects=objects)])
    @property
    def rid(self):return self.backend.catalogue.rows[0]['record_id']

class ReadTests(unittest.TestCase):
    def setUp(self):self.f=Fixture();self.r=self.f.backend;self.v=self.f.viewer
    def test_real_fenced_rows_join_sorted_revisions_without_changing_source(self):
        before=copy.deepcopy((self.f.media.live,self.f.state.live))
        detail=self.r.detail(self.v,self.f.rid)
        self.assertEqual([x['revision'] for x in detail['revisions']],[1,2])
        self.assertEqual(detail['revisions'][-1]['surface_cm2'],19.5)
        self.assertEqual(detail['revisions'][-1]['projected_cm2'],19)
        self.assertEqual(detail['training_admission'],'not_evaluated')
        self.assertEqual(detail['depth_status'],'not_in_snapshot')
        self.assertEqual(before,(self.f.media.live,self.f.state.live))
        self.assertEqual(len(self.f.audit.live),1)
    def test_no_bulk_list_or_source_write_on_page_and_detail(self):
        self.f.media.calls.clear();self.f.state.calls.clear()
        self.f.media.list_blobs=Mock(side_effect=AssertionError('no list'))
        self.f.state.list_blobs=Mock(side_effect=AssertionError('no state list'))
        self.r.page(self.v);self.r.detail(self.v,self.f.rid);self.r.image(self.v,self.f.rid)
        self.assertTrue(all(c[0] in {'reload','download'} for c in self.f.media.calls+self.f.state.calls))
        self.assertTrue(all(c[2].get('selected_generation') is None for c in self.f.media.calls if c[0]=='download'))
    def test_metadata_role_cannot_read_image_or_mask(self):
        viewer=Viewer(SUB,frozenset({'lite.review.metadata'}))
        data=self.r.detail(viewer,self.f.rid)
        self.assertNotIn('ai_polygons',data);self.assertNotIn('polygons',data['revisions'][0])
        with self.assertRaises(ReviewError) as e:self.r.image(viewer,self.f.rid)
        self.assertEqual(e.exception.status,403)
    def test_withdrawal_hides_metrics_and_media_keeps_evidence(self):
        self.f.privacy.manifest.withdraw(OWNER)
        d=self.r.detail(self.v,self.f.rid)
        self.assertEqual(d['status'],'withdrawn');self.assertNotIn('revisions',d)
        with self.assertRaises(ReviewError):self.r.image(self.v,self.f.rid)
        self.assertTrue(self.f.media.live)
    def test_missing_state_fails_closed_no_invented_initial_consent(self):
        self.f.state.live.clear()
        with self.assertRaises(ReviewError):self.r.detail(self.v,self.f.rid)
        self.assertEqual(self.r.page(self.v)['records'][0]['status'],'state_unverified')
    def test_pending_writer_hides_partial_capture(self):
        self.f.privacy.begin_write(OWNER)
        self.assertEqual(self.r.detail(self.v,self.f.rid)['status'],'writes_pending')
    def test_withdrawal_during_image_download_releases_no_image(self):
        self.f.media.before_download=lambda:self.f.privacy.manifest.withdraw(OWNER)
        with self.assertRaises(ReviewError):self.r.image(self.v,self.f.rid)
    def test_withdrawal_during_audit_is_rechecked_before_release(self):
        append=self.r.audit.append
        def withdraw(event):append(event);self.f.privacy.manifest.withdraw(OWNER)
        self.r.audit.append=withdraw
        with self.assertRaises(ReviewError) as e:self.r.detail(self.v,self.f.rid)
        self.assertEqual(e.exception.status,409)
    def test_seal_during_view_never_uses_retained_historical_generation(self):
        append=self.r.audit.append
        def seal(event):append(event);self.f.privacy.objects.seal(ObjectRef(OWNER,'image',IID))
        self.r.audit.append=seal
        with self.assertRaises(ReviewError):self.r.image(self.v,self.f.rid)
        self.assertGreater(len(self.f.media.history),len(self.f.media.live))
    def test_audit_failure_blocks_metadata_and_media(self):
        self.f.audit.upload_error=RuntimeError('synthetic audit failure')
        for fn in (lambda:self.r.page(self.v),lambda:self.r.detail(self.v,self.f.rid),lambda:self.r.image(self.v,self.f.rid)):
            with self.subTest(fn=fn),self.assertRaises(ReviewError) as e:fn()
            self.assertEqual(e.exception.code,'review_audit_unavailable')
    def test_tampered_bytes_same_generation_rejected(self):
        name=object_name(OWNER,'legacy','metadata',IID)
        gen,data,meta=self.f.media.live[name];self.f.media.live[name]=(gen,b'X'*len(data),meta)
        with self.assertRaises(ReviewError):self.r.detail(self.v,self.f.rid)
    def test_owner_conflict_and_wrong_dimensions_rejected(self):
        for update in ({'anon_id':'d'*32},{'image_w':9000},{'research_consent':False}):
            with self.subTest(update=update):
                f=Fixture();name=object_name(OWNER,'legacy','metadata',IID)
                gen,data,meta=f.media.live[name];value=decode(data);value.update(update);data=encoded(value)
                meta['sha256']=hashlib.sha256(data).hexdigest();f.media.live[name]=(gen,data,meta)
                f.backend.catalogue=Catalogue(f.catalogue())
                with self.assertRaises(ReviewError):f.backend.detail(f.viewer,f.rid)
    def test_jpeg_dimensions_checked_before_preview(self):
        name=object_name(OWNER,'legacy','image',IID)
        gen,data,meta=self.f.media.live[name];data=b'not jpeg';meta['sha256']=hashlib.sha256(data).hexdigest()
        self.f.media.live[name]=(gen,data,meta);self.r.catalogue=Catalogue(self.f.catalogue())
        with self.assertRaises(ReviewError):self.r.image(self.v,self.f.rid)
    def test_same_identity_new_epoch_does_not_revive_old_snapshot(self):
        self.f.privacy.withdraw(OWNER);self.f.privacy.complete_withdrawal(OWNER)
        renewal=Reconsent(self.f.privacy)
        renewal.resume(OWNER,dict(anon_id=OWNER,research_consent=True,previous_epoch='legacy',new_epoch=EPOCH,consent_version=CONSENT,disclosure_version=DISCLOSURE))
        self.assertEqual(self.r.detail(self.v,self.f.rid)['status'],'withdrawn')
        self.f.seed(self.f.privacy.scoped(EPOCH))
        self.r.catalogue=Catalogue(self.f.catalogue(EPOCH))
        self.assertEqual(self.r.detail(self.v,self.f.rid)['status'],'active')
        # Corrupt renewal audit must fail closed, even though active fence exists.
        key=renewal.key(OWNER,EPOCH);revision,event=renewal.store.load(key);event['owner']='e'*32
        renewal.store.compare_exchange(key,revision,event)
        with self.assertRaises(ReviewError):self.r.detail(self.v,self.f.rid)
    def test_key_derivation_matches_production_for_all_epochs(self):
        for epoch in ('legacy',EPOCH):
            fence,pointer,base=state_keys(OWNER,epoch)
            self.assertEqual(fence,self.f.privacy.scoped(epoch).manifest._key(OWNER).hex())
            renew=Reconsent(self.f.privacy)
            self.assertEqual(pointer,renew.key(OWNER).hex())
            self.assertEqual(hashlib.sha256(base+EPOCH.encode()).hexdigest(),renew.key(OWNER,EPOCH).hex())
    def test_cross_bucket_and_bad_object_paths_rejected(self):
        for bucket in ('woundai-mmh-review-media-421209514056','mmh-media','lite-public-v1'):
            with self.subTest(bucket=bucket):
                self.f.media.name=bucket
                with self.assertRaises(ReviewError):Source(self.f.media,self.f.state)
        for path in ('../image','https://evil','image/../'):
            with self.assertRaises(ReviewError):object_name(OWNER,'legacy',path,IID)
    def test_catalogue_rejects_duplicate_scopes_and_unsafe_pins(self):
        original=self.f.catalogue()
        variants=[]
        duplicate=copy.deepcopy(original);duplicate['records']*=2;variants.append(duplicate)
        for field,value in (('generation',0),('generation',True),('sha256','x'*64),('bytes',0),('kind','accounts')):
            c=copy.deepcopy(original);c['records'][0]['objects'][0][field]=value;variants.append(c)
        for c in variants:
            with self.subTest(c=c),self.assertRaises(ReviewError):Catalogue(c)
    def test_cursor_bound_to_viewer_capabilities_filter_snapshot_and_expiry(self):
        self.r.PAGE=0  # Force a cursor without more data or network.
        cursor=self.r._cursor(self.v,'all',0)
        self.assertIsNotNone(self.r.page(self.v,cursor=cursor))
        for viewer,source,c in ((Viewer('accounts.google.com:other',self.v.capabilities),'all',cursor),
                (Viewer(SUB,frozenset({'lite.review.metadata'})),'all',cursor),(self.v,'synthetic',cursor),(self.v,'all',cursor+'f')):
            with self.subTest(source=source),self.assertRaises(ReviewError):self.r.page(viewer,source,c)
        self.r.clock=lambda:time.time()+601
        with self.assertRaises(ReviewError):self.r.page(self.v,cursor=cursor)
    def test_bad_subject_does_not_disable_unrelated_listing(self):
        c=self.f.catalogue();other=copy.deepcopy(c['records'][0]);other['owner']='d'*32;c['records'].append(other)
        self.r.catalogue=Catalogue(c)
        self.assertEqual({r['status'] for r in self.r.page(self.v)['records']},{'active','state_unverified'})
    def test_real_fifty_record_page_and_snapshot_bound_cursor(self):
        c=self.f.catalogue();template=c['records'][0];c['records']=[]
        for n in range(51):
            row=copy.deepcopy(template);row['image_id']=format(n,'016x')
            row['objects']=[o for o in row['objects'] if o['kind']!='label']
            for o in row['objects']:o['identifier']=row['image_id']
            c['records'].append(row)
        self.r.catalogue=Catalogue(c)
        first=self.r.page(self.v);second=self.r.page(self.v,cursor=first['next_cursor'])
        self.assertEqual(len(first['records']),50);self.assertEqual(len(second['records']),1)
        self.assertIsNone(second['next_cursor'])
        self.assertFalse({r['record_id'] for r in first['records']}&{r['record_id'] for r in second['records']})
        c['captured_at']+=1;self.r.catalogue=Catalogue(c)
        with self.assertRaises(ReviewError):self.r.page(self.v,cursor=first['next_cursor'])
    def test_mask_coordinates_and_lay_provenance_are_not_trusted_blindly(self):
        for changes in ({'label_grade':'clinician_gt'},{'polygons':[[[0,0],[32,0],[20,20]]]},{'surface_cm2':float('inf')},{'revision':2}):
            with self.subTest(changes=changes):
                f=Fixture();name=next(n for n in f.media.live if '/label/' in n)
                gen,data,meta=f.media.live[name];label=json.loads(data);label.update(changes)
                # duplicate revision case deliberately rebinds slot to another revision.
                if changes=={'revision':2}:label['revision']=1
                data=json.dumps(label).encode();meta['sha256']=hashlib.sha256(data).hexdigest();f.media.live[name]=(gen,data,meta)
                f.backend.catalogue=Catalogue(f.catalogue())
                with self.assertRaises(ReviewError):f.backend.detail(f.viewer,f.rid)
    def test_audit_never_contains_media_or_authentication_token(self):
        self.r.image(self.v,self.f.rid)
        event=decode(next(iter(self.f.audit.live.values()))[1])
        self.assertEqual(set(event),{'schema','request_id','at','actor','purpose','action','records','catalogue_sha256'})
        self.assertEqual(event['actor'],SUB);self.assertEqual(event['records'],[self.f.rid])
    def test_current_generation_precondition_blocks_replacement_before_read(self):
        def replace():
            self.f.privacy.objects.seal(ObjectRef(OWNER,'metadata',IID))
        self.f.media.before_download=replace
        with self.assertRaises(ReviewError):self.r.detail(self.v,self.f.rid)

class Authentication(unittest.TestCase):
    def setUp(self):
        self.key=ec.generate_private_key(ec.SECP256R1())
        public=self.key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.http=Mock();self.http.get.return_value.status_code=200;self.http.get.return_value.json.return_value={'synthetic-key':public}
        self.verify=IapVerifier(self.http,{SUB:list(CAPS)})
        self.claims=dict(iss='https://cloud.google.com/iap',aud=IAP_AUDIENCE,sub=SUB,iat=int(time.time())-1,exp=int(time.time())+599)
    def token(self,**changes):return jwt.encode(dict(self.claims,**changes),self.key,algorithm='ES256',headers={'kid':'synthetic-key'})
    def test_valid_signature_and_subject(self):self.assertEqual(self.verify(self.token()).subject,SUB)
    def test_closed_bootstrap_denies_valid_human_at_both_hops_without_data_access(self):
        verify=IapVerifier(self.http,{})
        backend=Mock(side_effect=AssertionError('closed portal must not call reader'))
        client=portal_app(verify,backend).test_client()
        self.assertEqual(client.get('/',headers={'X-Goog-IAP-JWT-Assertion':self.token()}).status_code,401)
        backend.assert_not_called()
        reader=Mock()
        # Service authentication succeeds, but the valid human assertion is still denied.
        client=reader_app(reader,verify,lambda token:require(token=='Bearer service')).test_client()
        self.assertEqual(client.get('/v1/records',headers={'Authorization':'Bearer service',
            'X-Lite-Review-IAP':self.token()}).status_code,401)
        self.assertEqual(reader.mock_calls,[])
    def test_absent_or_malformed_roles_never_become_bootstrap_or_default_access(self):
        for roles in (None,[],False,{SUB:[]},{SUB:['admin']},{'user@example.test':list(CAPS)}):
            with self.subTest(roles=roles),self.assertRaises(ReviewError):IapVerifier(self.http,roles)
    def test_invalid_signature_algorithm_audience_issuer_subject_and_time(self):
        tokens=[None,'MMH-JWT',jwt.encode(self.claims,'synthetic'*8,algorithm='HS256',headers={'kid':'synthetic-key'}),
            jwt.encode(self.claims,ec.generate_private_key(ec.SECP256R1()),algorithm='ES256',headers={'kid':'synthetic-key'})]
        for changes in ({'aud':'mmh'},{'iss':'https://demo.invalid'},{'sub':'accounts.google.com:other'},
            {'exp':int(time.time())-10},{'iat':int(time.time())+100},{'exp':int(time.time())+3600}):tokens.append(self.token(**changes))
        for token in tokens:
            with self.subTest(token=type(token).__name__),self.assertRaises(ReviewError):self.verify(token)
    def test_forged_unsigned_email_does_not_authenticate(self):
        f=Fixture();app=portal_app(self.verify,Mock())
        r=app.test_client().get('/',headers={'X-Goog-Authenticated-User-Email':'jack.hou@gmail.com'})
        self.assertEqual(r.status_code,401)
    def test_reader_requires_expected_service_and_rechecks_human(self):
        f=Fixture();url='https://woundai-lite-review-reader-example.a.run.app'
        serviceclaims=dict(iss='https://accounts.google.com',aud=url,sub='123',email=PORTAL_SA,email_verified=True,iat=int(time.time())-1,exp=int(time.time())+100)
        service=ReaderIdentity(lambda token,aud:serviceclaims,url,'123')
        client=reader_app(f.backend,self.verify,service).test_client()
        headers={'Authorization':'Bearer synthetic','X-Lite-Review-IAP':self.token()}
        self.assertEqual(client.get('/v1/records',headers=headers).status_code,200)
        self.assertEqual(client.get('/v1/records',headers={'X-Lite-Review-IAP':self.token()}).status_code,401)
        self.assertEqual(client.get('/v1/records',headers={'Authorization':'Bearer synthetic','X-Lite-Review-IAP':'forged'}).status_code,401)
        for key,value in (('email','mmh@project.iam.gserviceaccount.com'),('sub','456'),('aud','https://other.run.app'),('email_verified',False)):
            old=serviceclaims[key];serviceclaims[key]=value
            self.assertEqual(client.get('/v1/records',headers=headers).status_code,401);serviceclaims[key]=old
    def test_revision_preview_is_explicit_and_does_not_change_reader_query(self):
        f=Fixture();calls=[]
        def backend(path,params,iap,image):
            calls.append((path,params));return f.backend.detail(self.verify(iap),f.rid)
        client=portal_app(self.verify,backend).test_client()
        headers={'X-Goog-IAP-JWT-Assertion':self.token()}
        for revision in ('0','1','2'):
            response=client.get('/records/'+f.rid+'?revision='+revision,headers=headers)
            self.assertEqual(response.status_code,200)
            self.assertIn('value="'+revision+'" selected',response.text)
            self.assertIn('（台灣時間）',response.text)
            self.assertEqual(calls[-1][1],{})
        for revision in ('3','-1','01','10000000','1&revision=2','bad'):
            self.assertEqual(client.get('/records/'+f.rid+'?revision='+revision,headers=headers).status_code,400)

    def test_real_two_hop_portal_html_and_readonly_routes(self):
        f=Fixture();reader=reader_app(f.backend,self.verify,lambda token:require(token=='Bearer service','unauthorized',401)).test_client()
        def hop(path,params,iap,image):
            response=reader.get(path,query_string=params,headers={'Authorization':'Bearer service','X-Lite-Review-IAP':iap})
            require(response.status_code==200);return response.data if image else response.json
        app=portal_app(self.verify,hop);client=app.test_client();headers={'X-Goog-IAP-JWT-Assertion':self.token()}
        for path in ('/','/records/'+f.rid,'/records/'+f.rid+'/image'):
            response=client.get(path,headers=headers);self.assertEqual(response.status_code,200)
            self.assertIn('no-store',response.headers['Cache-Control']);self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
        body=client.get('/records/'+f.rid,headers=headers).text
        self.assertIn('LiDAR 表面積估算',body);self.assertIn('相機方向投影估算',body)
        self.assertNotIn('<script>',body);self.assertIn('AI 邊界',body)
        for path in ('/api/v1/lite/segment','/api/v1/medical/consent','/api/v1/users/session','/records/'+f.rid+'/delete'):
            self.assertEqual(client.post(path,headers=headers).status_code,404)
        self.assertEqual(client.get('/?bucket=mmh',headers=headers).status_code,400)
        self.assertEqual(client.get('/?source=all&source=synthetic',headers=headers).status_code,400)

class RuntimeBoundary(unittest.TestCase):
    def test_portal_preserves_signed_token_separate_from_platform_header(self):
        from Backend.LiteReview.runtime import Backend
        from unittest.mock import MagicMock
        http=Mock();response=Mock(status_code=200,headers={'Content-Type':'application/json'})
        response.iter_content.return_value=[b'{"records":[]}']
        context=MagicMock();context.__enter__.return_value=response;http.get.return_value=context
        token=Mock(return_value='signed.synthetic.token')
        backend=Backend(http,'https://woundai-lite-review-reader-example.run.app',token)
        self.assertEqual(backend('/v1/records',{},'human-iap',False),{'records':[]})
        sent=http.get.call_args.kwargs['headers']
        self.assertEqual(sent['Authorization'],'Bearer signed.synthetic.token')
        self.assertEqual(sent['X-Serverless-Authorization'],sent['Authorization'])
        self.assertEqual(sent['X-Lite-Review-IAP'],'human-iap')
        token.assert_called_once();self.assertFalse(http.get.call_args.kwargs['allow_redirects'])
        with self.assertRaises(ReviewError):backend('https://mmh.invalid/',{},'human-iap',False)
        self.assertEqual(http.get.call_count,1)

class Rollout(unittest.TestCase):
    def test_plan_is_nonexecuting_and_environment_specific(self):
        value=plan('a'*40,{'file':'b'*64})
        self.assertFalse(value['execution_authorized']);self.assertFalse(value['cleanup']['enabled'])
        self.assertFalse(value['cleanup']['created']);self.assertIsNone(value['cleanup']['policy'])
        self.assertFalse(value['review']['allUsers']);self.assertIsNone(value['review']['allowed_subjects'])
        self.assertTrue(value['lite_catalogue']['never_run_medical_backfill'])
        self.assertTrue(value['medical_migration']['no_media_delete'])
        self.assertIn('OAuth',value['cleanup']['scheduler_auth'])
    def test_catalogue_cli_preserves_input_and_refuses_wrong_digest_or_overwrite(self):
        import tempfile,subprocess,sys
        root=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.json';out=Path(tmp)/'catalogue.json'
            data=encoded(dict(schema='lite-review-catalogue/1',captured_at=int(time.time()),records=[]))
            source.write_bytes(data)
            args=[sys.executable,'-B',str(root/'tools/build_lite_review_catalogue.py'),'--input',str(source),'--output',str(out),'--expected-sha256']
            failed=subprocess.run(args+['0'*64],capture_output=True)
            self.assertNotEqual(failed.returncode,0);self.assertFalse(out.exists())
            done=subprocess.run(args+[hashlib.sha256(data).hexdigest()],capture_output=True)
            self.assertEqual(done.returncode,0,done.stderr)
            self.assertEqual(out.stat().st_mode & 0o777,0o600)
            self.assertEqual(source.read_bytes(),data);self.assertEqual(out.read_bytes(),data)
            again=subprocess.run(args+[hashlib.sha256(data).hexdigest()],capture_output=True)
            self.assertNotEqual(again.returncode,0);self.assertEqual(out.read_bytes(),data)

    def test_plan_rejects_unpinned_source(self):
        for commit,files in [('HEAD',{'a':'b'*64}),('a'*40,{}),('a'*40,{'a':'x'})]:
            with self.assertRaises(ValueError):plan(commit,files)
    def test_packaging_excludes_other_app_bootstrap(self):
        root=Path(__file__).resolve().parents[2]
        text=(root/'Backend/LiteReview/Dockerfile').read_text()
        self.assertNotIn('COPY Backend/Flask',text);self.assertNotIn('COPY . ',text)
        self.assertIn('--require-hashes',text);self.assertIn('USER 65532:65532',text)

class RealSDKRead(unittest.TestCase):
    def test_sdk_get_has_precondition_but_no_historical_generation_selector(self):
        from urllib.parse import urlparse,parse_qs
        from requests import Response,Request
        from urllib3.response import HTTPResponse
        from google.cloud.storage import Client
        from google.auth.credentials import AnonymousCredentials
        import base64,google_crc32c
        data=b'synthetic-current-payload';digest=hashlib.sha256(data).hexdigest();calls=[]
        test=self
        class HTTP:
            is_mtls=False
            def request(self,method,url,**kwargs):
                calls.append((method,url));q=parse_qs(urlparse(url).query)
                if q.get('alt')==['media']:
                    test.assertNotIn('generation',q);test.assertEqual(q.get('ifGenerationMatch'),['7']);body=data
                else:
                    body=encoded(dict(name=object_name(OWNER,'legacy','image',IID),bucket=MEDIA,generation='7',size=str(len(data)),
                        metadata={'lite-fence':'1','owner':OWNER,'kind':'image','state':'live','sha256':digest}))
                response=Response();response.status_code=200;response.url=url
                response.request=Request(method,url).prepare();response._content=body;response._content_consumed=True
                response.headers['content-type']='application/octet-stream' if q.get('alt') else 'application/json'
                response.headers['content-length']=str(len(body))
                if q.get('alt'):response.headers['x-goog-hash']='crc32c='+base64.b64encode(google_crc32c.value(data).to_bytes(4,'big')).decode()
                response.raw=HTTPResponse(body=io.BytesIO(body),headers=response.headers,preload_content=False)
                return response
        client=Client(project='synthetic',credentials=AnonymousCredentials(),_http=HTTP())
        source=Source(client.bucket(MEDIA),client.bucket(STATE))
        obj=dict(kind='image',identifier=IID,generation=7,bytes=len(data),sha256=digest)
        self.assertEqual(source.read(dict(owner=OWNER,epoch='legacy'),obj),data)
        self.assertEqual(len(calls),2);self.assertTrue(all(m=='GET' for m,_ in calls))

if __name__=='__main__':unittest.main(verbosity=2)
