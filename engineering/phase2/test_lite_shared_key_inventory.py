"""Physical-version inventory using real custody/AES, synthetic GCS REST only.

No live credentials or provider. Original versions and audit rows are preserved.
The REST double deliberately supports pagination independently of the scanner.
"""
import base64
import copy
import io
import json
import socket
import unittest
from urllib.parse import unquote, urlsplit, parse_qs
from unittest.mock import patch
import requests
from requests.adapters import BaseAdapter
from lite_attest_state import GCSStateStore, MAX_STATE, StateUnavailable
from lite_grant_key_custody import crc32c
from lite_shared_key_inventory import SharedKeyInventory, GCSInventoryReader, InventoryUnavailable
from lite_subscription_executor import key
from lite_usage_ledger import digest
from test_lite_attest_state import FakeBucket
from test_lite_rotating_grant_keys import SharedFixture, V1, V2, V3
from test_lite_subscription_grace import rows
import test_lite_native_purchase as native


class InventoryFixture:
    def __init__(self,host):
        self.f=SharedFixture(host);self.bucket=FakeBucket();self.bucket.name='synthetic-custody-primary'
        self.store=GCSStateStore(self.bucket,prefix='synthetic/keys')
        for k,_,raw in rows(self.f.key_store):self.store.compare_exchange(bytes.fromhex(k),0,json.loads(raw))
        self.f.key_store=self.f.cipher.store=self.store
        self.f.login();self.person=self.f.person;self.before_key=copy.deepcopy(self.f.cipher._read(self.person)[1])
        self.envelope,self.context=self.f.snapshot()
        self.sources=[dict(id='primary',kind='primary',bucket=self.bucket.name,prefix=self.store.prefix,project_number='9999')]
        self.buckets={self.bucket.name:self.bucket};self.calls=[];self.page_size=3;self.soft={};self.soft_status=200
        self.policy_generation='1';self.empty_first=False;self.mutate=lambda p,q,m,c,b:(c,b)

    def metadata(self,b,name,g,raw,soft=False):
        item=dict(name=name,generation=str(g),size=str(len(raw)),
            crc32c=base64.b64encode(crc32c(raw).to_bytes(4,'big')).decode())
        if soft:item['softDeleteTime']='2026-11-11T00:00:00Z'
        elif b.live.get(name,(None,None))[0]!=g:item['timeDeleted']='2026-11-10T00:00:00Z'
        return item

    def get(self,path,query,*,media=False):
        self.calls.append((path,copy.deepcopy(query),media))
        b=self.buckets[path.split('/')[4]]
        if '/o/' in path:
            assert media and query['alt']=='media' and query['generation']==query['ifGenerationMatch']
            name=unquote(path.split('/o/',1)[1]);raw=b.versions.get((name,int(query['generation'])))
            code,body=(200,raw) if raw is not None else (404,None)
        elif path.endswith('/o'):
            assert not media and (('versions' in query)^('softDeleted' in query)) and 'delimiter' not in query
            soft='softDeleted' in query
            if soft and self.soft_status!=200:return self.soft_status,None
            entries=self.soft if soft else b.versions
            items=[self.metadata(b,n,g,raw,soft) for (n,g),raw in sorted(entries.items()) if n.startswith(query['prefix'])]
            token=query.get('pageToken')
            if self.empty_first and token is None:
                code,body=200,{'nextPageToken':'empty'}
            else:
                start=0 if token in (None,'empty') else int(token)
                stop=start+self.page_size;body={'items':items[start:stop]}
                if stop<len(items):body['nextPageToken']=str(stop)
                code=200
        else:
            assert not media
            code,body=200,dict(name=b.name,projectNumber='9999',metageneration=self.policy_generation,
                versioning={'enabled':True},softDeletePolicy={'retentionDurationSeconds':'604800'})
        return self.mutate(path,query,media,code,copy.deepcopy(body))

    def scanner(self,**kw):
        return SharedKeyInventory(cipher=self.f.cipher,sources=self.sources,expected_sources=digest(self.sources),
            get=self.get,**kw)

    def add_mirror(self):
        b=FakeBucket();b.name='synthetic-custody-mirror';b.versions=copy.deepcopy(self.bucket.versions);b.live=copy.deepcopy(self.bucket.live)
        self.buckets[b.name]=b
        self.sources.append(dict(self.sources[0],id='mirror',kind='mirror',bucket=b.name))
        return b

    def close_and_rotate(self):
        self.f.login('device-two','apple-human-2');other=self.f.person
        self.f.close_revoke();self.f.cipher.destroy_after_revocation(self.f.d,self.person)
        self.f.rotate_to(V2)
        self.f.cipher.rewrap(other,expected_record_sha256=digest(self.f.cipher._read(other)[1]))
        return other

    def independent_counts(self):
        # Direct JSON enumeration; does not call the inventory classification.
        result={}
        for b in self.buckets.values():
            for (name,g),raw in b.versions.items():
                c=json.loads(raw);r=c.get('record',c)
                if r.get('wrapped_dek'):
                    kind='historical' if b.live.get(name,(None,None))[0]!=g else 'current' if name==self.store._name(self.f.cipher._key(r['person'])) else 'proposals'
                    counts=result.setdefault(r['version'],dict(current=0,historical=0,proposals=0));counts[kind]+=1
        return result


class InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):native.legacy.LinksTests.setUpClass()
    def setUp(self):
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'));p.start();self.addCleanup(p.stop)
        self.x=InventoryFixture(self);self.f=self.x.f
    def assert_incomplete(self,result,reason=None):
        self.assertFalse(result['all_selected_material_observed']);self.assertFalse(result['kms_destruction_authorized'])
        if reason:self.assertIn(reason,result['sources'][0].get('reason',''))
    def test_closed_and_rewrapped_current_rows_do_not_hide_old_dependencies(self):
        self.x.close_and_rotate();r=self.x.scanner().inspect()
        self.assertTrue(r['all_selected_material_observed'],r)
        self.assertEqual(r['observed_wrapped_dependencies'],self.x.independent_counts())
        self.assertEqual(r['observed_wrapped_dependencies'][V1]['current'],0)
        self.assertGreater(r['observed_wrapped_dependencies'][V1]['historical'],0)
        self.assertGreater(r['observed_wrapped_dependencies'][V1]['proposals'],0)
    def test_read_only_preserves_every_payload_audit_key_and_original_envelope(self):
        self.x.close_and_rotate()
        before=copy.deepcopy(self.x.bucket.versions),copy.deepcopy(self.x.bucket.live),rows(self.f.key_audit),self.f.kms.calls.copy(),rows(self.f.g.store)
        r=self.x.scanner().inspect()
        self.assertTrue(r['all_selected_material_observed'],r)
        self.assertEqual(before,(self.x.bucket.versions,self.x.bucket.live,rows(self.f.key_audit),self.f.kms.calls,rows(self.f.g.store)))
        for k in ('complete_global_inventory_verified','external_backups_verified','all_writers_drained','kms_destruction_authorized'):
            self.assertIs(r[k],False)
        for k in ('provider_mutations','kms_calls','plaintext_deks_read'):self.assertEqual(r[k],0)
        self.assertNotIn(self.x.before_key['wrapped_dek'],json.dumps(r));self.assertNotIn('private-refresh-token',json.dumps(r))
    def test_uncommitted_proposal_is_found(self):
        source=digest(self.x.before_key);self.f.rotate_to(V2);cas=self.x.store.compare_exchange
        def fail(k,g,v):
            if k==self.f.cipher._key(self.x.person):raise StateUnavailable('synthetic CAS unavailable')
            return cas(k,g,v)
        with patch.object(self.x.store,'compare_exchange',side_effect=fail),self.assertRaises(StateUnavailable):
            self.f.cipher.rewrap(self.x.person,expected_record_sha256=source)
        r=self.x.scanner().inspect();self.assertTrue(r['all_selected_material_observed'],r)
        self.assertEqual(r['observed_wrapped_dependencies'],self.x.independent_counts())
        self.assertEqual(r['observed_wrapped_dependencies'][V2],dict(current=0,historical=0,proposals=1))
    def test_account_outside_previous_rewrap_batch_is_not_skipped(self):
        self.x.close_and_rotate();self.f.rotate_to(V1);self.f.login('third','apple-human-3')
        r=self.x.scanner().inspect();self.assertTrue(r['all_selected_material_observed'],r)
        self.assertEqual(r['observed_wrapped_dependencies'][V1]['current'],1)
    def test_mirror_old_copy_keeps_old_dependency(self):
        self.x.add_mirror();self.x.close_and_rotate();r=self.x.scanner().inspect()
        self.assertTrue(r['all_selected_material_observed'],r)
        self.assertEqual(r['observed_wrapped_dependencies'],self.x.independent_counts())
        self.assertEqual(r['observed_wrapped_dependencies'][V1]['current'],1)
    def test_soft_deleted_payload_cannot_be_called_complete(self):
        (name,g),raw=next(iter(self.x.bucket.versions.items()));self.x.soft[(name,g+1000)]=raw
        r=self.x.scanner().inspect();self.assert_incomplete(r)
        self.assertEqual(r['sources'][0]['status'],'soft_payloads_unreadable');self.assertEqual(r['sources'][0]['soft_count'],1)
        self.assertFalse(any('softDeleted' in q and media for _,q,media in self.x.calls))
    def test_soft_policy_zero_and_400_are_unknown_not_zero(self):
        self.x.soft_status=400
        self.x.mutate=lambda p,q,m,c,b:(c,dict(b,softDeletePolicy={'retentionDurationSeconds':'0'})) if '/o' not in p else (c,b)
        r=self.x.scanner().inspect();self.assert_incomplete(r)
        self.assertEqual(r['sources'][0]['status'],'soft_inventory_unknown');self.assertIsNone(r['sources'][0]['soft_count'])
    def test_bucket_forbidden_is_not_an_empty_inventory(self):
        self.x.mutate=lambda *a:(403,None)
        self.assert_incomplete(self.x.scanner().inspect(),'not proof of absence')
    def test_generation_precondition_or_missing_version_is_incomplete(self):
        for code in (404,412,429,503):
            with self.subTest(code=code):
                self.x.mutate=lambda p,q,m,c,b:(code,None) if m else (c,b)
                self.assert_incomplete(self.x.scanner().inspect(),'pinned generation')
    def test_checksum_mismatch_is_not_silently_accepted(self):
        self.x.mutate=lambda p,q,m,c,b:(c,b[:-1]+b'!') if m else (c,b)
        self.assert_incomplete(self.x.scanner().inspect(),'checksum')
    def test_missing_original_audit_is_incomplete(self):
        with patch.object(self.f.key_audit,'load',return_value=None):self.assert_incomplete(self.x.scanner().inspect())
    def test_original_audit_failure_is_redacted(self):
        with patch.object(self.f.key_audit,'load',side_effect=RuntimeError('synthetic sensitive transport')):
            r=self.x.scanner().inspect();self.assert_incomplete(r);self.assertNotIn('sensitive',json.dumps(r))
    def test_unknown_file_is_not_skipped(self):
        self.x.bucket.blob('synthetic/keys/unknown.json').upload_from_string(b'{}',if_generation_match=0)
        self.assert_incomplete(self.x.scanner().inspect(),'unknown object')
    def test_unknown_format_is_not_skipped(self):
        self.x.store.compare_exchange(b'\xff'*32,0,dict(v=999,wrapped_dek='hidden-copy'))
        self.assert_incomplete(self.x.scanner().inspect(),'unrecognized')
    def test_valid_row_at_wrong_slot_is_not_accepted(self):
        self.x.store.compare_exchange(b'\xff'*32,0,self.x.before_key)
        self.assert_incomplete(self.x.scanner().inspect(),'wrong slot')
    def test_hold_is_visible_without_permission_to_delete(self):
        def holds(p,q,m,c,b):
            if not m and p.endswith('/o'):
                for item in b.get('items',[]):item['temporaryHold']=True
            return c,b
        self.x.mutate=holds;r=self.x.scanner().inspect()
        self.assertTrue(r['all_selected_material_observed'],r);self.assertTrue(all(x['held'] for x in r['sources'][0]['records']))
    def test_short_empty_page_with_token_is_not_end(self):
        self.x.empty_first=True;r=self.x.scanner().inspect()
        self.assertTrue(r['all_selected_material_observed'],r)
        self.assertEqual(len(r['sources'][0]['records']),len(self.x.bucket.versions))
    def test_duplicate_generation_is_rejected(self):
        def duplicate(p,q,m,c,b):
            if p.endswith('/o') and b.get('items'):b['items'].append(copy.deepcopy(b['items'][0]))
            return c,b
        self.x.mutate=duplicate;self.assert_incomplete(self.x.scanner().inspect(),'duplicate')
    def test_repeated_page_token_is_rejected(self):
        self.x.mutate=lambda p,q,m,c,b:(c,dict(items=[],nextPageToken='0')) if p.endswith('/o') else (c,b)
        self.assert_incomplete(self.x.scanner().inspect(),'repeated page')
    def test_page_budget_exhaustion_is_incomplete(self):self.assert_incomplete(self.x.scanner(max_pages=1).inspect(),'page bound')
    def test_object_budget_exhaustion_is_incomplete(self):self.assert_incomplete(self.x.scanner(max_objects=1).inspect(),'object bound')
    def test_byte_budget_exhaustion_is_incomplete(self):self.assert_incomplete(self.x.scanner(max_bytes=1).inspect(),'byte budget')
    def test_operation_budget_exhaustion_is_incomplete(self):self.assert_incomplete(self.x.scanner(max_operations=1).inspect(),'operation bound')
    def test_time_budget_exhaustion_is_incomplete(self):
        times=iter([100,100,400]);self.assert_incomplete(self.x.scanner(clock=lambda:next(times)).inspect(),'time bound')
    def test_second_scan_detects_new_copy_created_after_payload_reads(self):
        changed=[]
        def change(p,q,m,c,b):
            if m and not changed:
                changed.append(True);self.x.store.compare_exchange(b'\xff'*32,0,dict(v=999))
            return c,b
        self.x.mutate=change;self.assert_incomplete(self.x.scanner().inspect(),'inventory changed')
    def test_policy_change_detected(self):
        changed=[]
        def change(p,q,m,c,b):
            if m and not changed:changed.append(True);self.x.policy_generation='2'
            return c,b
        self.x.mutate=change;self.assert_incomplete(self.x.scanner().inspect(),'policy changed')
    def test_primary_anchor_omission_not_empty_success(self):
        name=self.x.store._name(key('account-grant-cipher-config',self.f.cipher.scope))
        self.x.bucket.versions={k:v for k,v in self.x.bucket.versions.items() if k[0]!=name}
        self.assert_incomplete(self.x.scanner().inspect(),'mode anchor')
    def test_duplicate_json_key_rejected_even_matching_checksum(self):
        self.x.bucket.blob('synthetic/keys/'+'f'*64+'.json').upload_from_string(b'{"v":1,"v":2}',if_generation_match=0)
        self.assert_incomplete(self.x.scanner().inspect(),'content or audit')
    def test_missing_original_closed_tombstone_audit_blocks_inventory(self):
        self.x.close_and_rotate();load=self.f.key_audit.load
        def missing(k):
            r=load(k)
            return None if r and r[1].get('schema')=='shared-proposal-tombstone/1' else r
        with patch.object(self.f.key_audit,'load',side_effect=missing):self.assert_incomplete(self.x.scanner().inspect(),'audit differs')
    def test_source_manifest_is_pinned_and_overlaps_are_rejected(self):
        options=dict(cipher=self.f.cipher,sources=self.x.sources,expected_sources='f'*64,get=self.x.get)
        with self.assertRaises(ValueError):SharedKeyInventory(**options)
        sources=self.x.sources+[dict(self.x.sources[0],id='overlap',kind='mirror',prefix='synthetic/keys/backup')]
        with self.assertRaises(ValueError):SharedKeyInventory(**dict(options,sources=sources,expected_sources=digest(sources)))
    def test_wrong_project_cannot_be_scanned(self):
        self.x.mutate=lambda p,q,m,c,b:(c,dict(b,projectNumber='1111')) if '/o' not in p else (c,b)
        self.assert_incomplete(self.x.scanner().inspect(),'identity differs')
    def test_reusing_same_inventory_object_rejected(self):
        scan=self.x.scanner();scan.inspect()
        with self.assertRaises(InventoryUnavailable):scan.inspect()


class ReadOnlyAdapter(BaseAdapter):
    def __init__(self,status=200,body=b'{}'):self.status,self.body,self.calls=status,body,[]
    def send(self,request,**kw):
        self.calls.append((request,kw));r=requests.Response();r.status_code=self.status;r.url=request.url
        r.request=request;r.raw=io.BytesIO(self.body);return r
    def close(self):pass


class InventoryRESTAdapter(BaseAdapter):
    """Exercise actual requests URL/params preparation without a network socket."""
    def __init__(self,fixture):self.fixture,self.calls=fixture,[]
    def send(self,request,**kw):
        self.calls.append(request);u=urlsplit(request.url)
        assert request.method=='GET' and u.hostname=='storage.googleapis.com' and request.body is None
        q={k:v[0] for k,v in parse_qs(u.query).items()};media=q.get('alt')=='media'
        status,body=self.fixture.get(u.path,q,media=media)
        raw=body if media and status==200 else json.dumps(body).encode()
        r=requests.Response();r.status_code=status;r.url=request.url;r.request=request;r.raw=io.BytesIO(raw);return r
    def close(self):pass


class WireTests(unittest.TestCase):
    def reader(self,status=200,body=b'{}'):
        s=requests.Session();s.trust_env=False;a=ReadOnlyAdapter(status,body);s.mount('https://',a)
        self.addCleanup(s.close);return GCSInventoryReader(s),a
    def test_real_requests_preparation_preserves_generation_pinning_and_get(self):
        read,a=self.reader(body=b'abc');r=read('/storage/v1/b/synthetic-custody/o/synthetic%2Fkeys%2Fa.json',
            {'alt':'media','generation':'123','ifGenerationMatch':'123'},media=True)
        self.assertEqual(r,(200,b'abc'));req,kw=a.calls[0]
        self.assertEqual(req.method,'GET');self.assertIsNone(req.body);self.assertEqual(kw['timeout'],(5,10));self.assertTrue(kw['stream'])
        u=urlsplit(req.url);self.assertEqual(u.hostname,'storage.googleapis.com');self.assertIn('%2F',u.path)
        self.assertEqual(parse_qs(u.query),{'alt':['media'],'generation':['123'],'ifGenerationMatch':['123']})
    def test_redirect_is_not_followed(self):
        read,a=self.reader(302);self.assertEqual(read('/storage/v1/b/synthetic-custody',{}),(302,None));self.assertEqual(len(a.calls),1)
    def test_streaming_payload_and_metadata_limits(self):
        for media,n in ((True,MAX_STATE+1),(False,262145)):
            with self.subTest(media=media):
                read,_=self.reader(body=b'X'*n)
                with self.assertRaises(InventoryUnavailable):read('/storage/v1/b/synthetic-custody',{},media=media)
    def test_arbitrary_endpoint_and_path_are_refused_without_request(self):
        read,a=self.reader()
        for path in ('https://elsewhere/','/storage/v1/b/a/o/../../secrets','/v1/projects/real/keys'):
            with self.assertRaises(ValueError):read(path,{})
        self.assertEqual(a.calls,[])
    def test_error_body_is_not_read_or_leaked(self):
        read,a=self.reader(403,b'private-error-material');self.assertEqual(read('/storage/v1/b/synthetic-custody',{}),(403,None))
    def test_metadata_duplicate_fields_rejected(self):
        read,_=self.reader(body=b'{"items":[],"items":[1]}')
        with self.assertRaises(InventoryUnavailable):read('/storage/v1/b/synthetic-custody',{})
