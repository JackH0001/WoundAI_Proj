"""Candidate GCS generation fences for crash-safe Lite withdrawal.

Used by the public Lite candidate HTTP adapter (not deployed). Every write must first register its exact ObjectRef in
an owner admission CAS journal. Withdrawal closes that journal, then seals ALL
registered refs plus completed objects. A missing object is sealed too: deleting
it would let a delayed if_generation_match=0 request recreate sensitive bytes.

Permanent zero-byte markers retain only scope/state. Bucket versioning, soft
delete, retention and lifecycle deletion must be disabled/validated separately.
No IAM, owner authentication, journal admission or full withdrawal is implied by
this primitive. Legacy put_blob and shared ledgers must never bypass it.
"""
from dataclasses import dataclass
import hashlib
import re
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud.storage.blob import Blob
from lite_attest_state import StateUnavailable

PREFIX = 'lite_fenced/v1'
MAX_BYTES = 24 * 1024 * 1024
KINDS = frozenset({'rgbd', 'image', 'metadata', 'depth', 'confidence', 'index', 'label'})
ATTEMPTS = 8


class _NoDeleteBlob(Blob):
    # The SDK's resumable CRC failure handler calls self.delete() without a
    # generation precondition. That could erase a concurrently installed seal.
    # Keep the object for our journal-driven cleanup; never run that deletion.
    def delete(self, *args, **kwargs):
        raise StateUnavailable('automatic object deletion forbidden for fenced writes')


class ObjectSealed(ValueError): pass
class GenerationConflict(ValueError): pass


@dataclass(frozen=True)
class ObjectRef:
    owner: str
    kind: str
    identifier: str

    @property
    def name(self):
        if (type(self.owner) is not str or re.fullmatch(r'[0-9a-f]{32}', self.owner) is None or
                type(self.kind) is not str or self.kind not in KINDS or
                type(self.identifier) is not str or re.fullmatch(r'(?:[0-9a-f]{16}|[0-9a-f]{32}|[0-9a-f]{64})', self.identifier) is None):
            raise ValueError('invalid fenced object scope')
        return f'{PREFIX}/{self.owner}/{self.kind}/{self.identifier}'


@dataclass(frozen=True)
class PinnedWrite:
    bucket: str
    ref: ObjectRef
    generation: int


@dataclass(frozen=True)
class ObjectSnapshot:
    generation: int
    size: int
    state: str
    sha256: str


class FencedObjects:
    """Injected trusted bucket only. A conflict never refreshes a writer's pin."""
    def __init__(self, bucket):
        if type(bucket.name) is not str or re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', bucket.name) is None:
            raise ValueError('explicit trusted bucket identity required')
        self.bucket = bucket

    def _write_blob(self, name):
        blob = self.bucket.blob(name)
        return _NoDeleteBlob(name, self.bucket) if isinstance(blob, Blob) else blob

    @staticmethod
    def _metadata(ref, state, data):
        return {'lite-fence': '1', 'owner': ref.owner, 'kind': ref.kind, 'state': state,
                'sha256': hashlib.sha256(data).hexdigest()}

    def _snapshot(self, ref):
        name = ref.name
        blob = self.bucket.blob(name)
        try:
            blob.reload(timeout=10, retry=None)
        except NotFound:
            return None
        except Exception as exc:
            raise StateUnavailable('fenced object metadata unavailable') from exc
        metadata = blob.metadata
        if (type(blob.generation) is not int or blob.generation <= 0 or
                type(blob.size) is not int or not 0 <= blob.size <= MAX_BYTES or
                type(metadata) is not dict or set(metadata) != {'lite-fence', 'owner', 'kind', 'state', 'sha256'} or
                metadata['lite-fence'] != '1' or metadata['owner'] != ref.owner or metadata['kind'] != ref.kind or
                metadata['state'] not in ('live', 'sealed') or
                type(metadata['sha256']) is not str or re.fullmatch(r'[0-9a-f]{64}', metadata['sha256']) is None or
                (metadata['state'] == 'sealed' and (blob.size != 0 or metadata['sha256'] != hashlib.sha256(b'').hexdigest())) or
                (metadata['state'] == 'live' and blob.size == 0)):
            raise StateUnavailable('invalid fenced object metadata')
        return ObjectSnapshot(blob.generation, blob.size, metadata['state'], metadata['sha256'])

    def list_refs(self, owner, kind=None):
        prefix = ObjectRef(owner, 'image', '0' * 32).name.rsplit('/', 2)[0] + '/'
        if kind is not None:
            if kind not in KINDS: raise ValueError('invalid object kind')
            prefix += kind + '/'
        try:
            listed = list(self.bucket.list_blobs(prefix=prefix, timeout=10, retry=None))
            refs = []
            for blob in listed:
                name = blob.name
                if type(name) is not str or not name.startswith(prefix):
                    raise ValueError('inventory escaped scope')
                parts = name.split('/')
                if len(parts) != 5: raise ValueError('invalid fenced path')
                ref = ObjectRef(parts[2], parts[3], parts[4])
                if ref.owner != owner or ref.name != name: raise ValueError('invalid object reference')
                refs.append(ref)
            if len(refs) != len(set(refs)): raise ValueError('duplicate inventory entries')
            return refs
        except Exception as exc:
            raise StateUnavailable('complete fenced inventory unavailable') from exc

    def pin(self, ref):
        """Read a generation BEFORE journal admission; this is not authorization."""
        snapshot = self._snapshot(ref)
        if snapshot is not None and snapshot.state == 'sealed':
            raise ObjectSealed('object permanently sealed')
        return PinnedWrite(self.bucket.name, ref, 0 if snapshot is None else snapshot.generation)

    def _read(self, ref, snapshot):
        try:
            data = self.bucket.blob(ref.name).download_as_bytes(
                if_generation_match=snapshot.generation, checksum='crc32c', timeout=10, retry=None)
        except Exception as exc:
            # In particular, a 404 after metadata is not an empty/missing object.
            raise StateUnavailable('fenced generation readback unavailable') from exc
        if type(data) is not bytes or len(data) != snapshot.size or hashlib.sha256(data).hexdigest() != snapshot.sha256:
            raise StateUnavailable('fenced object readback mismatch')
        return data

    def read_live(self, ref):
        snapshot = self._snapshot(ref)
        if snapshot is None:
            return None
        if snapshot.state == 'sealed':
            raise ObjectSealed('object permanently sealed')
        return self._read(ref, snapshot)

    def write(self, pin, data):
        """One attempt at the ORIGINAL generation. Unknown outcome keeps its journal plan."""
        if (not isinstance(pin, PinnedWrite) or pin.bucket != self.bucket.name or
                type(pin.generation) is not int or pin.generation < 0 or
                type(data) is not bytes or not 0 < len(data) <= MAX_BYTES):
            raise ValueError('invalid pinned write')
        blob = self._write_blob(pin.ref.name)
        blob.metadata = self._metadata(pin.ref, 'live', data)
        try:
            blob.upload_from_string(data, content_type='application/octet-stream',
                if_generation_match=pin.generation, checksum='crc32c', timeout=10, retry=None)
        except PreconditionFailed as exc:
            raise GenerationConflict('pinned generation changed; do not rebase') from exc
        except Exception as exc:
            raise StateUnavailable('fenced write outcome unknown; retain plan') from exc
        if type(blob.generation) is not int or blob.generation <= pin.generation:
            raise StateUnavailable('fenced write generation not confirmed')
        # Read the live snapshot, never accept a deleted historical generation as success.
        snapshot = self._snapshot(pin.ref)
        if snapshot is not None and snapshot.state == 'sealed':
            raise ObjectSealed('object sealed during write')
        if snapshot is None or snapshot.generation != blob.generation or self._read(pin.ref, snapshot) != data:
            raise StateUnavailable('fenced write readback not confirmed')
        return snapshot.generation

    def seal(self, ref):
        """Remove live payload but KEEP a permanent marker; safe to repeat after a crash.

        Caller must close owner admission before this operation. No new writer may
        capture the returned marker generation; pin() refuses it. This method is
        deliberately separate from whole-owner completion evidence.
        """
        for _ in range(ATTEMPTS):
            snapshot = self._snapshot(ref)
            if snapshot is not None and snapshot.state == 'sealed':
                if self._read(ref, snapshot) != b'':
                    raise StateUnavailable('seal contains data')
                return snapshot.generation
            expected = 0 if snapshot is None else snapshot.generation
            blob = self._write_blob(ref.name)
            blob.metadata = self._metadata(ref, 'sealed', b'')
            try:
                blob.upload_from_string(b'', content_type='application/octet-stream',
                    if_generation_match=expected, checksum='crc32c', timeout=10, retry=None)
            except PreconditionFailed:
                continue  # Only the sealer can observe/retry a competing generation.
            except Exception as exc:
                raise StateUnavailable('seal outcome unknown; retry withdrawal') from exc
            # Even an acknowledged write is not the completion evidence. Reload and
            # read back on the next pass, also handling a lost response on a retry.
        raise StateUnavailable('seal contention or confirmation exhausted')
