"""Strict review identities and a finite, generation-bound discovery catalogue."""
from dataclasses import dataclass
import hashlib
import json
import re

MEDIA = 'woundai-lite-media-421209514056'
STATE = 'woundai-lite-security-421209514056'
AUDIT = 'woundai-lite-review-audit-421209514056'
IAP_AUDIENCE = '/projects/421209514056/locations/asia-east1/services/woundai-lite-review'
PORTAL_SA = 'woundai-lite-review@woundai-jackh001.iam.gserviceaccount.com'
CAPS = frozenset({'lite.review.metadata', 'lite.review.media'})

class ReviewError(Exception):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)

def require(ok, code='review_data_unverified', status=503):
    if not ok: raise ReviewError(code, status)

def hexid(value, length):
    return type(value) is str and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None

def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def decode(data):
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result)
            result[k] = v
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, RecursionError):
        raise ReviewError('review_data_unverified') from None

@dataclass(frozen=True)
class Viewer:
    subject: str
    capabilities: frozenset

    def check(self, cap):
        require(cap in self.capabilities, 'review_permission_denied', 403)

def object_name(owner, epoch, kind, identifier):
    require(hexid(owner, 32) and (epoch == 'legacy' or hexid(epoch, 32)))
    require(kind in {'image', 'metadata', 'label', 'rgbd', 'index', 'depth', 'confidence'})
    require(any(hexid(identifier, n) for n in (16, 32, 64)))
    path = f'lite_fenced/v1/{owner}/{kind}/{identifier}'
    return path if epoch == 'legacy' else f'lite_epochs/v1/{epoch}/' + path

def state_keys(owner, epoch):
    object_name(owner, epoch, 'image', '0'*16)
    base = b'woundlite/write-plans/1\0' + MEDIA.encode() + b'\0' + owner.encode()
    fence = hashlib.sha256(base if epoch == 'legacy' else base+b'\0epoch\0'+epoch.encode()).hexdigest()
    renewal = b'woundlite/reconsent/1\0'+MEDIA.encode()+b'\0'+owner.encode()+b'\0'
    return fence, hashlib.sha256(renewal+b'active').hexdigest(), renewal

class Catalogue:
    """Approved snapshot is discovery only. Every view rechecks source and consent."""
    def __init__(self, value):
        require(type(value) is dict and set(value)=={'schema','captured_at','records'})
        require(value['schema']=='lite-review-catalogue/1' and type(value['captured_at']) is int and value['captured_at']>0)
        require(type(value['records']) is list and len(value['records'])<=2000)
        self.rows = []
        self.by_id = {}
        for original in value['records']:
            row = decode(encoded(original))
            require(set(row)=={'owner','epoch','image_id','source','objects'})
            require(hexid(row['image_id'],16) and row['source'] in {'synthetic','unverified'})
            require(type(row['objects']) is list and 2<=len(row['objects'])<=103)
            names = set(); kinds = []
            for obj in row['objects']:
                require(type(obj) is dict and set(obj)=={'kind','identifier','generation','bytes','sha256'})
                name = object_name(row['owner'],row['epoch'],obj['kind'],obj['identifier'])
                require(name not in names and type(obj['generation']) is int and obj['generation']>0)
                require(type(obj['bytes']) is int and 0<obj['bytes']<=24*1024*1024 and hexid(obj['sha256'],64))
                require(obj['kind'] in {'image','metadata','rgbd','label'})
                require(obj['kind']=='label' or obj['identifier']==row['image_id'])
                names.add(name); kinds.append(obj['kind'])
            require(kinds.count('image')==1 and kinds.count('metadata')==1 and kinds.count('rgbd')<=1 and kinds.count('label')<=100)
            rid = hashlib.sha256(encoded([row['owner'],row['epoch'],row['image_id']])).hexdigest()
            require(rid not in self.by_id)
            row['record_id']=rid; self.rows.append(row); self.by_id[rid]=row
        self.rows.sort(key=lambda r:r['record_id'])
        self.captured_at=value['captured_at']; self.digest=hashlib.sha256(encoded(value)).hexdigest()

    def get(self, rid):
        require(rid in self.by_id, 'record_not_in_review_snapshot',404)
        return self.by_id[rid]
