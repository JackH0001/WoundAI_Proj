"""Fixed Lite bucket adapters: source get only, separate audit create only."""
import hashlib
from google.api_core.exceptions import NotFound
from .contracts import MEDIA, STATE, AUDIT, require, encoded, decode, object_name, hexid, ReviewError

class Source:
    def __init__(self, media, state):
        require(media.name==MEDIA and state.name==STATE,'review_bucket_scope_mismatch')
        self._media,self._state=media,state

    def state(self,key):
        require(hexid(key,64))
        blob=self._state.blob('lite_security/v1/'+key+'.json')
        try:
            try:blob.reload(timeout=10,retry=None)
            except NotFound:return None
            require(type(blob.generation) is int and blob.generation>0 and type(blob.size) is int and 0<blob.size<=16384)
            data=self._state.blob(blob.name).download_as_bytes(if_generation_match=blob.generation,checksum='crc32c',timeout=10,retry=None)
            require(len(data)==blob.size)
            return blob.generation,decode(data)
        except ReviewError:raise
        except Exception:raise ReviewError('review_state_unavailable') from None

    def check(self,row,obj):
        blob=self._media.blob(object_name(row['owner'],row['epoch'],obj['kind'],obj['identifier']))
        try:
            blob.reload(timeout=10,retry=None)
            require(blob.generation==obj['generation'] and blob.size==obj['bytes'], 'source_changed_refresh_snapshot',409)
            require(blob.metadata=={'lite-fence':'1','owner':row['owner'],'kind':obj['kind'],'state':'live','sha256':obj['sha256']},'source_not_live_or_unverified',409)
            return blob
        except ReviewError:raise
        except Exception:raise ReviewError('review_source_unavailable') from None

    def read(self,row,obj):
        blob=self.check(row,obj)
        try:
            # Current object with precondition, never blob(generation=historical).
            # reload() populates blob.generation; using that same Blob for a GET
            # would select a historical generation in the real SDK. Use a fresh
            # unversioned Blob plus a current-generation precondition instead.
            data=self._media.blob(blob.name).download_as_bytes(if_generation_match=obj['generation'],checksum='crc32c',timeout=10,retry=None)
            require(type(data) is bytes and len(data)==obj['bytes'] and hashlib.sha256(data).hexdigest()==obj['sha256'])
            return data
        except ReviewError:raise
        except Exception:raise ReviewError('review_source_unavailable') from None

class AccessAudit:
    def __init__(self,bucket):
        require(bucket.name==AUDIT,'review_audit_scope_mismatch');self._bucket=bucket
    def append(self,event):
        require(hexid(event['request_id'],32))
        data=encoded(event);require(len(data)<32768)
        blob=self._bucket.blob('lite_review_access/v1/'+event['request_id']+'.json')
        blob.upload_from_string(data,content_type='application/json',if_generation_match=0,checksum='crc32c',timeout=10,retry=None)
        require(type(blob.generation) is int and blob.generation>0)
