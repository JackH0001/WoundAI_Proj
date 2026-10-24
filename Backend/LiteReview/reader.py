"""Fresh state checks bracket bounded reads; no source writes or historical fallback."""
import base64
import hashlib
import hmac
import io
import json
import math
import secrets
import time
from PIL import Image
from .contracts import MEDIA, ReviewError, require, encoded, decode, hexid, object_name, state_keys

def polygons(value, w, h):
    require(type(value) is list and len(value)<=16)
    for poly in value:
        require(type(poly) is list and 3<=len(poly)<=4000)
        for point in poly:
            require(type(point) is list and len(point)==2 and all(type(x) in (int,float) and math.isfinite(x) for x in point))
            require(0<=point[0]<w and 0<=point[1]<h)
    return value

class Consent:
    def __init__(self, source): self.source=source

    def observe(self, row):
        owner,epoch=row['owner'],row['epoch']
        fence_key,pointer_key,base=state_keys(owner,epoch)
        pointer=self.source.state(pointer_key)
        proofs={pointer_key:pointer}
        active='legacy'
        if pointer is not None:
            p=pointer[1]
            require(type(p) is dict and set(p)=={'v','owner','epoch','audit_sha256'} and
                type(p['v']) is int and p['v']==1 and p['owner']==owner and hexid(p['epoch'],32) and hexid(p['audit_sha256'],64))
            active=p['epoch']; key=hashlib.sha256(base+active.encode()).hexdigest()
            audit=self.source.state(key);proofs[key]=audit
            require(audit is not None and hashlib.sha256(encoded(audit[1])).hexdigest()==p['audit_sha256'])
            event=audit[1]
            require(type(event) is dict and set(event)=={'v','action','owner','previous_epoch','new_epoch',
                'request_sha256','consent_version','disclosure_version','previous_fence_generation',
                'previous_fence','sealed_markers','previous_audit_sha256','at'})
            require(event['owner']==owner and event['new_epoch']==active and
                event['action']=='reconsent_future_only' and type(event['v']) is int and event['v']==1 and
                (event['previous_epoch']=='legacy' or hexid(event['previous_epoch'],32)) and event['previous_epoch']!=active and
                hexid(event['request_sha256'],64) and type(event['at']) is int and event['at']>0 and
                type(event['previous_fence_generation']) is int and event['previous_fence_generation']>0 and
                type(event['sealed_markers']) is int and event['sealed_markers']>=0 and
                all(type(event[k]) is str and 0<len(event[k])<=96 for k in ('consent_version','disclosure_version')) and
                (event['previous_audit_sha256'] is None or hexid(event['previous_audit_sha256'],64)))
            old=event['previous_fence']
            require(type(old) is dict and set(old)=={'v','owner','bucket','withdrawn','writers'} and
                type(old['v']) is int and old['v']==1 and old['owner']==owner and old['bucket']==MEDIA and
                old['withdrawn'] is True and old['writers']=={})
        fence=self.source.state(fence_key);proofs[fence_key]=fence
        require(fence is not None, 'consent_state_missing')
        f=fence[1]
        require(type(f) is dict and set(f)=={'v','owner','bucket','withdrawn','writers'} and
            type(f['v']) is int and f['v']==1 and f['owner']==owner and f['bucket']==MEDIA and
            type(f['withdrawn']) is bool and type(f['writers']) is dict and len(f['writers'])<=8)
        # Validate pending writers too: unknown/corrupt state never authorizes reads.
        for token,plans in f['writers'].items():
            require(hexid(token,32) and type(plans) is dict and len(plans)<=10)
            for name,values in plans.items():
                require(type(name) is str and name.count(':')==1)
                kind,identifier=name.split(':');object_name(owner,epoch,kind,identifier)
                require(type(values) is list and len(values)==2 and type(values[0]) is int and values[0]>=0 and type(values[1]) is bool)
        status='withdrawn' if f['withdrawn'] else ('epoch_retired' if active!=epoch else ('writes_pending' if f['writers'] else 'active'))
        return status,proofs

    def unchanged(self,row,before):
        after=self.observe(row)
        require(after==before,'consent_changed_refresh_required',409)

class Reader:
    PAGE=50
    def __init__(self, catalogue, source, audit, cursor_key, clock=time.time):
        require(type(cursor_key) is bytes and len(cursor_key)>=32)
        self.catalogue,self.source,self.audit,self.key,self.clock=catalogue,source,audit,cursor_key,clock
        self.consent=Consent(source)

    def _audit(self,viewer,action,ids):
        event=dict(schema='lite-review-access/1',request_id=secrets.token_hex(16),at=int(self.clock()),
            actor=viewer.subject,purpose='internal_research_review',action=action,
            records=ids,catalogue_sha256=self.catalogue.digest)
        try: self.audit.append(event)
        except Exception: raise ReviewError('review_audit_unavailable') from None

    def _cursor(self,viewer,source,offset):
        data=encoded(dict(viewer=viewer.subject,caps=sorted(viewer.capabilities),source=source,
            catalogue=self.catalogue.digest,offset=offset,expires=int(self.clock())+600))
        return base64.urlsafe_b64encode(data).decode().rstrip('=')+'.'+hmac.new(self.key,data,'sha256').hexdigest()

    def page(self,viewer,source='all',cursor=None):
        viewer.check('lite.review.metadata');require(source in {'all','synthetic','unverified'},'invalid_filter',400)
        offset=0
        if cursor:
            try:
                require(type(cursor) is str and len(cursor)<2048)
                token,mac=cursor.split('.')
                data=base64.b64decode(token+'='*(-len(token)%4),altchars=b'-_',validate=True)
                require(hmac.compare_digest(mac,hmac.new(self.key,data,'sha256').hexdigest()))
                value=decode(data);offset=value['offset']
                require(set(value)=={'viewer','caps','source','catalogue','offset','expires'} and
                    value['viewer']==viewer.subject and value['caps']==sorted(viewer.capabilities) and
                    value['source']==source and value['catalogue']==self.catalogue.digest and
                    type(value['expires']) is int and self.clock()<value['expires']<=self.clock()+600 and
                    type(offset) is int and offset>=0)
            except Exception: raise ReviewError('invalid_or_expired_cursor',400) from None
        selected=[r for r in self.catalogue.rows if source=='all' or r['source']==source]
        require(offset<=len(selected),'invalid_or_expired_cursor',400)
        states=[];rows=[]
        for row in selected[offset:offset+self.PAGE]:
            try:
                before=self.consent.observe(row);states.append((row,before))
                status=before[0]
            except ReviewError: status='state_unverified'
            rows.append({k:row[k] for k in ('record_id','image_id','epoch','source')} | {'status':status})
        self._audit(viewer,'list',[r['record_id'] for r in rows])
        for row,state in states:self.consent.unchanged(row,state)
        nxt=offset+self.PAGE
        return dict(records=rows,next_cursor=self._cursor(viewer,source,nxt) if nxt<len(selected) else None,
            captured_at=self.catalogue.captured_at,catalogue_sha256=self.catalogue.digest,
            completeness='snapshot_only',media_allowed='lite.review.media' in viewer.capabilities)

    def _read(self,row,obj):
        return self.source.read(row,obj)

    def _metadata(self,row):
        obj=next(x for x in row['objects'] if x['kind']=='metadata')
        require(obj['bytes']<=1024*1024)
        value=decode(self._read(row,obj))
        require(type(value) is dict and value.get('anon_id')==row['owner'] and value.get('image_id')==row['image_id'] and value.get('research_consent') is True)
        w,h=value.get('image_w'),value.get('image_h')
        require(all(type(x) is int and 0<x<=4096 for x in (w,h)))
        return value

    def _finish(self,viewer,row,before,action):
        self._audit(viewer,action,[row['record_id']])
        for obj in row['objects']: self.source.check(row,obj)
        self.consent.unchanged(row,before)

    def detail(self,viewer,rid):
        viewer.check('lite.review.metadata');row=self.catalogue.get(rid)
        before=self.consent.observe(row)
        if before[0]!='active':
            self._audit(viewer,'status',[rid]);self.consent.unchanged(row,before)
            return dict(record_id=rid,status=before[0],media_allowed=False,permanent_erasure='not_verified')
        meta=self._metadata(row);w,h=meta['image_w'],meta['image_h']
        revisions=[];seen=set()
        require(sum(o['bytes'] for o in row['objects'] if o['kind']=='label')<=2*1024*1024)
        for obj in row['objects']:
            if obj['kind']!='label':continue
            require(obj['bytes']<=140000)
            label=decode(self._read(row,obj));rev=label.get('revision')
            require(type(rev) is int and 1<=rev<=1000000 and rev not in seen)
            slot=hashlib.sha256(json.dumps([row['owner'],row['image_id'],rev],separators=(',',':')).encode()).hexdigest()[:16]
            require(label.get('anon_id')==row['owner'] and label.get('image_id')==row['image_id'] and
                label.get('image_w')==w and label.get('image_h')==h and obj['identifier']==slot and
                label.get('annotation_receipt_id')==slot and hexid(label.get('payload_sha256'),64))
            require(label.get('source') in {'manual','ai'} and label.get('label_grade')==('lay' if label['source']=='manual' else 'ai_unverified'))
            poly=polygons(label.get('polygons'),w,h)
            item=dict(revision=rev,source=label['source'],label_grade=label['label_grade'],payload_sha256=label['payload_sha256'])
            for k in ('surface_cm2','projected_cm2'):
                value=label.get(k);require(type(value) in (int,float) and math.isfinite(value) and 0<=value<=100000)
                item[k]=value
            if 'lite.review.media' in viewer.capabilities:item['polygons']=poly
            seen.add(rev);revisions.append(item)
        revisions.sort(key=lambda x:x['revision'])
        rgbd=next((o for o in row['objects'] if o['kind']=='rgbd'),None)
        receipt=meta.get('raw_depth_receipt')
        depth='not_in_snapshot'
        if rgbd:
            image=next(o for o in row['objects'] if o['kind']=='image')
            require(type(receipt) is dict and receipt.get('schema')=='lite.rgbd.receipt/1' and
                receipt.get('installation_id')==row['owner'] and receipt.get('capture_id')==row['image_id'] and
                receipt.get('bundle_sha256')==rgbd['sha256'] and receipt.get('bundle_bytes')==rgbd['bytes'] and receipt.get('rgb_sha256')==image['sha256'])
            depth='receipt_and_generation_bound_not_geometry_verified'
        elif receipt is not None:depth='receipt_present_bundle_missing_from_snapshot'
        result=dict(record_id=rid,status='active',image_id=row['image_id'],epoch=row['epoch'],source=row['source'],
            image_w=w,image_h=h,revisions=revisions,depth_status=depth,training_admission='not_evaluated',
            captured_at=self.catalogue.captured_at,media_allowed='lite.review.media' in viewer.capabilities,
            objects=[{k:o[k] for k in ('kind','generation','bytes','sha256')} for o in row['objects']])
        if result['media_allowed']:result['ai_polygons']=polygons(meta.get('ai_polygons',[]),w,h)
        self._finish(viewer,row,before,'detail');return result

    def image(self,viewer,rid):
        viewer.check('lite.review.media');row=self.catalogue.get(rid);before=self.consent.observe(row)
        require(before[0]=='active','media_access_blocked',410)
        meta=self._metadata(row)
        obj=next(x for x in row['objects'] if x['kind']=='image');require(obj['bytes']<=12*1024*1024)
        data=self._read(row,obj)
        try:
            with Image.open(io.BytesIO(data)) as image:
                require(image.format=='JPEG' and image.size==(meta['image_w'],meta['image_h']))
                image.verify()
        except Exception:raise ReviewError('image_content_unverified') from None
        self._finish(viewer,row,before,'image');return data
