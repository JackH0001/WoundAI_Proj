"""Physical store identity and narrow views; no credential discovery or I/O."""
from pathlib import Path
from lite_attest_state import SQLiteStateStore, GCSStateStore, StateUnavailable


class LimitedStateStore:
    """Additional application guard, not a replacement for bucket IAM."""
    def __init__(self, store, *, mode):
        if not isinstance(store, (SQLiteStateStore, GCSStateStore)) or mode not in ('read', 'create'):
            raise ValueError('explicit concrete store and narrow mode required')
        self.store, self.mode = store, mode

    def load(self, key): return self.store.load(key)

    def compare_exchange(self, key, revision, value):
        if self.mode != 'create' or type(revision) is not int or revision != 0:
            raise StateUnavailable('read-only or create-only storage boundary')
        return self.store.compare_exchange(key, revision, value)


def location(store):
    if isinstance(store, LimitedStateStore): store = store.store
    if isinstance(store, SQLiteStateStore):
        return ('sqlite', str(Path(store.path).resolve()))
    if isinstance(store, GCSStateStore) and type(store.bucket.name) is str and store.bucket.name:
        return ('gcs', store.bucket.name, store.prefix.strip('/'))
    raise ValueError('explicit SQLite or named GCS state store required')


def overlaps(a, b):
    if a[:2] != b[:2]: return False
    if a[0] == 'sqlite': return True
    return a[2] == b[2] or not a[2] or not b[2] or a[2].startswith(b[2]+'/') or b[2].startswith(a[2]+'/')
