"""Remove live Lite research rows after durable owner withdrawal + writer drain.

Only two allowlisted research ledgers are mutable here. Clinical/audit logs are
never accepted. Minimal revocation markers remain; object history, backups,
exports and model artifacts are outside this receipt's scope.
"""
from contextlib import contextmanager
import json
import os
import re
import tempfile

LEDGERS = frozenset(('lite_index.jsonl', 'lite_labels.jsonl'))
REVOKED = frozenset(('withdrawal_requested', 'deleted', 'delete_incomplete'))


@contextmanager
def record_file_lock(path):
    """Same lock path as the existing idempotent annotation writer."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path + '.lock', 'a+b') as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate research row field')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('non-finite research row value')


def _rows(data):
    # Keep exact untouched bytes (including whitespace) for other owners.
    result = []
    for line in data.splitlines(keepends=True):
        if not line.strip():
            result.append((line, None)); continue
        row = json.loads(line.decode('utf-8'), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        if type(row) is not dict or type(row.get('anon_id')) is not str:
            raise ValueError('unattributable Lite research row')
        result.append((line, row))
    return result


def _remove(row, owner):
    if row is None or row['anon_id'] != owner:
        return False
    # A claimed action cannot smuggle a polygon/payload back into retained rows.
    minimal = (row.get('action') in REVOKED and
               set(row) <= {'anon_id', 'action', 'received_at'} and
               ('received_at' not in row or type(row['received_at']) is str))
    return not minimal


def _local(store, key, owner):
    path = store._p(key)
    with record_file_lock(path):
        try:
            with open(path, 'rb') as source: data = source.read()
        except FileNotFoundError:
            return 0
        rows = _rows(data)
        removed = sum(_remove(row, owner) for _, row in rows)
        if not removed:
            return 0
        retained = b''.join(line for line, row in rows if not _remove(row, owner))
        fd, temp = tempfile.mkstemp(prefix='.lite-purge-', dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(retained); output.flush(); os.fsync(output.fileno())
            os.replace(temp, path)
            if os.name != 'nt':
                directory = os.open(os.path.dirname(path), os.O_RDONLY)
                try: os.fsync(directory)
                finally: os.close(directory)
            with open(path, 'rb') as source:
                if source.read() != retained:
                    raise IOError('Lite ledger replacement readback failed')
        finally:
            if os.path.exists(temp): os.unlink(temp)
        return removed


def _gcs_inventory(store, key, owner):
    bucket, bucket_name = store._target(key)
    base = store._k(key) + '/'
    selected = []
    for listed in store._client.list_blobs(bucket_name, prefix=base):
        name, generation = listed.name, getattr(listed, 'generation', None)
        if (not name.startswith(base) or
                not re.fullmatch(r'(?:[0-9]{20}_[0-9a-f]{8}|receipt_[0-9a-f]{16})\.jsonl', name[len(base):]) or
                type(generation) is not int or generation <= 0):
            raise ValueError('invalid Lite ledger object identity')
        # Pin both the download and deletion; never act on a newer replacement.
        blob = bucket.blob(name, generation=generation)
        data = blob.download_as_bytes(if_generation_match=generation, checksum='crc32c')
        rows = _rows(data)
        count = sum(_remove(row, owner) for _, row in rows)
        if count:
            if any(row is not None and not _remove(row, owner) for _, row in rows):
                raise ValueError('mixed-owner/marker object needs separate migration')
            selected.append((name, generation, count))
    return bucket, selected


def _gcs(store, key, owner):
    from google.api_core.exceptions import NotFound
    bucket, selected = _gcs_inventory(store, key, owner)
    removed = 0
    for name, generation, count in selected:
        try:
            bucket.blob(name).delete(if_generation_match=generation)
            removed += count
        except NotFound:
            pass  # Concurrent identical cleanup; the fresh inventory below is authoritative.
    _, remaining = _gcs_inventory(store, key, owner)
    if remaining:
        raise IOError('Lite research rows remain after deletion')
    return removed


def purge_live_research_rows(store, key, owner):
    """Caller must already have permanently withdrawn and drained this owner."""
    from store import LocalStore, GcsStore
    if key not in LEDGERS or type(owner) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', owner) is None:
        raise ValueError('invalid Lite purge scope')
    if store._is_audit(key):
        raise PermissionError('protected ledger')
    if isinstance(store, LocalStore):
        return _local(store, key, owner)
    if isinstance(store, GcsStore):
        _, bucket_name = store._target(key)
        base = store._k(key) + '/'
        try:
            return _gcs(store, key, owner)
        finally:
            store._line_cache.pop((bucket_name, base), None)
    raise TypeError('unsupported Lite ledger storage')
