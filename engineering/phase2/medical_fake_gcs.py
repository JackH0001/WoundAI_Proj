"""Offline SDK boundary double, NOT a GCS server/emulator or acceptance claim.

Uses the production GcsStore methods with no credentials or network. Models
create-only CAS, pinned generations, noncurrent/soft versions and object holds.
Policy/API errors are injected by tests (including the real policy-zero 400);
metadata alone is not a complete emulation of the server's capability rules.
"""
import threading
from types import SimpleNamespace
from google.api_core.exceptions import NotFound, PreconditionFailed, Forbidden, BadRequest
from store import GcsStore, AUDIT_RETENTION_SECONDS


class Client:
    def __init__(self):
        self.rows = {}; self.generation = 0; self.lock = threading.RLock()
        self.uploads = []; self.deletes = []; self.lists = []
        self.before_upload = None; self.before_delete = None; self.before_list = None
        self.buckets = {}

    def bucket(self, name):
        if name not in self.buckets: self.buckets[name] = Bucket(self, name)
        return self.buckets[name]

    def list_blobs(self, name, prefix='', versions=False, soft_deleted=False, max_results=None, **kw):
        if self.before_list: self.before_list(name, prefix, versions, soft_deleted)
        if versions and soft_deleted: raise ValueError('incompatible GCS list modes')
        with self.lock:
            self.lists.append((name, prefix, versions, soft_deleted))
            rows = [SimpleNamespace(name=n, generation=g, size=len(r['data']))
                    for (b, n, g), r in sorted(self.rows.items()) if b == name and n.startswith(prefix)
                    and (r['state'] == 'soft' if soft_deleted else r['state'] in ('live', 'noncurrent') if versions else r['state'] == 'live')]
            return rows if max_results is None else rows[:max_results]

    def restore_soft(self, bucket, name, generation):
        with self.lock:
            old = self.rows[bucket, name, generation]
            assert old['state'] == 'soft'
            self.bucket(bucket).blob(name).upload_from_string(old['data'])


class Bucket:
    def __init__(self, client, name):
        self.client, self.name = client, name
        self._properties = {}
    def reload(self, **kw): pass
    def get_iam_policy(self, **kw): return SimpleNamespace(bindings=self.bindings)
    def test_iam_permissions(self, permissions, **kw):
        # Reproduce the real GCS API boundary, not just set intersection: the
        # whole probe fails on UBLA even when the caller has no ACL permissions.
        if (self._properties.get('iamConfiguration', {}).get('uniformBucketLevelAccess', {}).get('enabled')
                and set(permissions) & {'storage.objects.getIamPolicy', 'storage.objects.setIamPolicy'}):
            raise BadRequest('Cannot test storage.objects.getIamPolicy or storage.objects.setIamPolicy on buckets with uniform bucket-level access enabled')
        return set(permissions) & self.permissions
    def blob(self, name, generation=None): return Blob(self, name, generation)


class Blob:
    def __init__(self, bucket, name, generation):
        self.bucket, self.name, self.generation = bucket, name, generation
    def _key(self):
        c = self.bucket.client
        if self.generation is not None:
            key = (self.bucket.name, self.name, self.generation)
            if key in c.rows and c.rows[key]['state'] != 'soft': return key
        else:
            for key, row in c.rows.items():
                if key[:2] == (self.bucket.name, self.name) and row['state'] == 'live': return key
        raise NotFound('offline synthetic missing object')
    @property
    def size(self): return len(self.bucket.client.rows[self._key()]['data'])
    def exists(self, **kw):
        with self.bucket.client.lock:
            try: self._key(); return True
            except NotFound: return False
    def reload(self, if_generation_match=None, **kw):
        with self.bucket.client.lock:
            key = self._key()
            if if_generation_match is not None and key[2] != if_generation_match:
                raise PreconditionFailed('offline read generation changed')
            self.generation = key[2]
    def download_as_bytes(self, **kw):
        with self.bucket.client.lock:
            key = self._key()
            if kw.get('if_generation_match') is not None and key[2] != kw['if_generation_match']:
                raise PreconditionFailed('offline download generation changed')
            return self.bucket.client.rows[key]['data']
    def upload_from_string(self, data, content_type=None, if_generation_match=None, **kw):
        c = self.bucket.client
        if c.before_upload: c.before_upload(self, data, if_generation_match)
        with c.lock:
            live = Blob(self.bucket, self.name, None)
            if if_generation_match == 0 and live.exists(): raise PreconditionFailed('offline CAS lost')
            if if_generation_match not in (None, 0) and (not live.exists() or live._key()[2] != if_generation_match):
                raise PreconditionFailed('offline replacement generation changed')
            if live.exists(): c.rows[live._key()]['state'] = 'noncurrent'
            c.generation += 1
            c.rows[self.bucket.name, self.name, c.generation] = dict(data=data, state='live', hold=False)
            c.uploads.append((self.bucket.name, self.name, if_generation_match))
    def delete(self, if_generation_match=None, **kw):
        c = self.bucket.client
        if c.before_delete: c.before_delete(self, if_generation_match)
        with c.lock:
            key = self._key()
            if if_generation_match != key[2]: raise PreconditionFailed('offline wrong delete generation')
            if c.rows[key]['hold']: raise Forbidden('offline object hold')
            c.deletes.append((key, if_generation_match))
            soft = int(self.bucket._properties.get('softDeletePolicy', {}).get('retentionDurationSeconds', 0))
            if soft: c.rows[key]['state'] = 'soft'
            else: del c.rows[key]


def make_store():
    c = Client()
    s = GcsStore.__new__(GcsStore)  # never invokes ADC/client construction
    s._client = c; s.prefix = 'synthetic'
    s._bucket_name, s._audit_bucket_name = 'fake-media', 'fake-audit'
    s._bucket, s._audit_bucket = c.bucket(s._bucket_name), c.bucket(s._audit_bucket_name)
    s._security_bucket = None
    # Production policy gate still runs, against explicitly fake metadata.
    s._audit_bucket._properties = {'retentionPolicy': {'retentionPeriod': str(AUDIT_RETENTION_SECONDS), 'isLocked': True}}
    return s
