"""Public Lite media adapter. All research payload writes use durable GCS fences.

Clinical/demo legacy storage is separate. Security budgets remain in their own
store; this adapter accepts only Lite media and the two research ledgers.
"""
import json
import re
import secrets
from lite_attest_state import StateUnavailable
from lite_fenced_objects import FencedObjects, ObjectRef, ObjectSealed, GenerationConflict
from lite_fenced_manifest import FencedManifest
from store import ImmutableConflict

LEDGERS = {'lite_index.jsonl': 'index', 'lite_labels.jsonl': 'label'}
EXTENSIONS = {'jpg': 'image', 'json': 'metadata', 'depth.png': 'depth', 'conf.png': 'confidence'}


class FencedPrivacy:
    def __init__(self, state_store, media_bucket):
        self.objects = FencedObjects(media_bucket)
        self.manifest = FencedManifest(state_store, bucket=media_bucket.name)

    def begin_write(self, owner): return self.manifest.begin(owner)
    def finish_write(self, ticket): return self.manifest.finish(ticket)
    def is_withdrawn(self, owner): return self.manifest.is_withdrawn(owner)

    def withdraw(self, owner):
        self.manifest.withdraw(owner)
        return self.manifest.seal_pending(owner, self.objects)

    def media(self, owner, ticket=None):
        return FencedMedia(self, owner, ticket)

    def complete_withdrawal(self, owner):
        if not self.is_withdrawn(owner): raise StateUnavailable('owner admission still open')
        self.manifest.seal_pending(owner, self.objects)
        refs = self.objects.list_refs(owner)
        removed = 0
        for ref in refs:
            snapshot = self.objects._snapshot(ref)
            if snapshot is None: raise StateUnavailable('registered object disappeared')
            removed += snapshot.state == 'live'
            self.objects.seal(ref)
        # Markers must remain. Only payload absence is claimed; never delete a
        # marker and accidentally re-enable an old generation=0 create request.
        final = self.objects.list_refs(owner)
        if set(final) != set(refs): raise StateUnavailable('withdrawal inventory changed')
        for ref in final:
            snapshot = self.objects._snapshot(ref)
            if snapshot is None or snapshot.state != 'sealed' or self.objects._read(ref, snapshot) != b'':
                raise StateUnavailable('live payload remains')
        if self.manifest.withdraw(owner) != 0: raise StateUnavailable('write plans remain')
        return dict(status='deleted', anon_id=owner, deletion_scope='live_media',
                    writers_drained=False, write_fence='gcs-generation-v1',
                    live_payloads_remaining=0, retained_markers=len(final),
                    objects_cleared_this_attempt=removed, research_ledger_cleanup='live_rows_removed')


class FencedMedia:
    def __init__(self, privacy, owner, ticket):
        ObjectRef(owner, 'image', '0' * 32).name
        if ticket is not None and ticket.owner != owner: raise StateUnavailable('writer owner mismatch')
        self.privacy, self.owner, self.ticket = privacy, owner, ticket

    def _ref(self, key):
        if type(key) is not str: raise ValueError('invalid media key')
        match = re.fullmatch(r'lite/([0-9a-f]{32})/([0-9a-f]{16})\.(jpg|json|depth\.png|conf\.png)', key)
        if match:
            owner, identifier, extension = match.groups(); kind = EXTENSIONS[extension]
        else:
            match = re.fullmatch(r'lite_raw/([0-9a-f]{32})/([0-9a-f]{16})\.rgbd', key)
            if not match: raise ValueError('unsupported fenced media key')
            owner, identifier = match.groups(); kind = 'rgbd'
        if owner != self.owner: raise StateUnavailable('media owner mismatch')
        return ObjectRef(owner, kind, identifier)

    def _read(self, ref):
        try: return self.privacy.objects.read_live(ref)
        except ObjectSealed: return None

    def _write(self, ref, data):
        if self.ticket is None: raise StateUnavailable('registered writer required')
        return self.privacy.manifest.write_registered(self.ticket, self.privacy.objects, ref, data)

    def put_blob(self, key, data): self._write(self._ref(key), data)
    def get_blob(self, key): return self._read(self._ref(key))
    def get_json(self, key):
        data = self.get_blob(key)
        return None if data is None else json.loads(data)
    def exists(self, key): return self.get_blob(key) is not None

    def _create_once(self, ref, data, *, receipt_id=None):
        old = self._read(ref)
        if old is not None:
            if receipt_id is not None and json.loads(old).get('annotation_receipt_id') == receipt_id: return False
            if old != data: raise ImmutableConflict(ref.name)
            return False
        # Re-check the generation inside write_registered. It must still be 0
        # for immutable creation; reserve the original pin explicitly here.
        if self.ticket is None: raise StateUnavailable('registered writer required')
        pin = self.privacy.objects.pin(ref)
        if pin.generation != 0:
            old = self._read(ref)
            if receipt_id is not None and old is not None and json.loads(old).get('annotation_receipt_id') == receipt_id: return False
            if old != data: raise ImmutableConflict(ref.name)
            return False
        self.privacy.manifest.plan(self.ticket, pin)
        try:
            self.privacy.objects.write(pin, data)
        except GenerationConflict:
            self.privacy.manifest.acknowledge(self.ticket, pin)
            old = self._read(ref)
            if receipt_id is not None and old is not None and json.loads(old).get('annotation_receipt_id') == receipt_id: return False
            if old != data: raise ImmutableConflict(ref.name)
            return False
        self.privacy.manifest.acknowledge(self.ticket, pin)
        return True

    def put_blob_immutable(self, key, data, content_type='application/octet-stream'):
        return self._create_once(self._ref(key), data)

    def append_line(self, key, line):
        kind = LEDGERS.get(key)
        if kind is None: raise ValueError('unsupported fenced ledger')
        row = json.loads(line)
        if type(row) is not dict or row.get('anon_id') != self.owner: raise StateUnavailable('ledger owner mismatch')
        ref = ObjectRef(self.owner, kind, secrets.token_hex(16))
        self._create_once(ref, (line.rstrip('\n') + '\n').encode('utf-8'))

    def append_record_once(self, key, receipt_id, record):
        if (key != 'lite_labels.jsonl' or type(receipt_id) is not str or
                re.fullmatch(r'[0-9a-f]{16}', receipt_id) is None or
                record.get('anon_id') != self.owner or record.get('annotation_receipt_id') != receipt_id):
            raise ValueError('invalid annotation receipt scope')
        data = (json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        return self._create_once(ObjectRef(self.owner, 'label', receipt_id), data, receipt_id=receipt_id)

    def read_lines_fresh(self, key):
        kind = LEDGERS.get(key)
        if kind is None: raise ValueError('unsupported fenced ledger')
        rows = []
        for ref in self.privacy.objects.list_refs(self.owner, kind):
            data = self._read(ref)
            if data is None: continue
            row = json.loads(data)
            if type(row) is not dict or row.get('anon_id') != self.owner: raise StateUnavailable('ledger ownership invalid')
            rows.append((str(row.get('received_at', '')), ref.identifier, data.decode('utf-8').strip()))
        return [line for _, _, line in sorted(rows)]
