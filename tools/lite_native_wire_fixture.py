"""Loopback-only Swift/Python contract fixture. Never imported by an App image.

Apple, KMS and inference are synthetic. Native request bodies are forwarded
unchanged through real TCP; a test-only middleware adds synthetic App Attest
assertions, which the normal backend verifies. This is NOT Apple Sandbox/TLS QA.
"""
import base64
import copy
import hashlib
import io
import json
import re
from pathlib import Path
import secrets
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Backend/Flask'), str(ROOT / 'engineering/phase2')]
from werkzeug.serving import make_server, WSGIRequestHandler
from werkzeug.wrappers import Request, Response
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from test_lite_native_backend import NativeBackendTests, rows, free_grant
from test_lite_signed_client import b64
from test_lite_research_ingest_flow import ResearchIngestFlowTests
from lite_raw_depth_store import unpack
from lite_reconsent import Reconsent, epoch


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args): pass


def stored_rows_hash(store):
    # Preserve exact SQLite key/data bytes and revision in comparison evidence;
    # neither decoded grant contents nor private material enters the report.
    def binary(value):
        if isinstance(value, bytes): return {'bytes_hex': value.hex()}
        raise TypeError('unsupported stored evidence type')
    return hashlib.sha256(json.dumps(rows(store), sort_keys=True,
        separators=(',', ':'), default=binary).encode()).hexdigest()


class WireFixture:
    def __init__(self):
        NativeBackendTests.setUpClass()
        self.token = secrets.token_hex(32)
        self.h = None
        self.flow = None
        self.research_checks = []
        self.privacy_checks = []
        self.deletion_checks = []
        self.events = []
        self.completed = []
        self.clients = {}
        self.revoke_before_service = False
        self.server = make_server('127.0.0.1', 0, self, request_handler=QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return {'url': f'http://127.0.0.1:{self.server.server_port}', 'token': self.token}

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.cleanup_state()

    def cleanup_state(self):
        if self.flow: self.flow.doCleanups()
        elif self.h: self.h.doCleanups()
        self.flow = None; self.h = None

    def research_readback(self, requested_epoch='legacy'):
        if not self.flow: raise ValueError('research fixture required')
        f = self.flow
        privacy = f.privacy.scoped(epoch(requested_epoch))
        media = privacy.media(f.r.h.owner)
        refs = privacy.objects.list_refs(f.r.h.owner, kind='rgbd')
        if len(refs) != 1: raise ValueError('exactly one capture required')
        ref = refs[0]; iid = ref.identifier
        key = 'lite_raw/' + f.r.h.owner + '/' + iid + '.rgbd'
        bundle = media.get_blob(key); packet = unpack(bundle)
        meta = media.get_json(f'lite/{f.r.h.owner}/{iid}.json')
        result = dict(image_id=iid, bundle_sha256=hashlib.sha256(bundle).hexdigest(),
            rgb_sha256=hashlib.sha256(packet['jpeg']).hexdigest(),
            depth_sha256=hashlib.sha256(packet['raw_depth']).hexdigest(),
            metadata_sha256=hashlib.sha256(packet['metadata_bytes']).hexdigest(),
            metadata=packet['metadata'], ai_polygons=meta['ai_polygons'],
            index_rows=len(media.read_lines_fresh('lite_index.jsonl')),
            # Object enumeration order is not annotation order. Keep every row
            # and normalize only presentation; HTTP events below prove ordering.
            labels=sorted((json.loads(line) for line in media.read_lines_fresh('lite_labels.jsonl')),
                          key=lambda row: (row['image_id'], row['revision'])),
            media_generation=str(f.bucket.live[privacy.objects.name(ref)][0]),
            epoch=requested_epoch)
        self.research_checks.append(result)
        return result

    def privacy_readback(self, requested_epochs):
        if not self.flow or type(requested_epochs) is not list or not 1 <= len(requested_epochs) <= 3:
            raise ValueError('bounded research epochs required')
        f = self.flow; owner = f.r.h.owner
        values = []
        for value in requested_epochs:
            privacy = f.privacy.scoped(epoch(value))
            objects = []
            for ref in privacy.objects.list_refs(owner):
                saved = privacy.objects._snapshot(ref)
                payload = privacy.objects._read(ref, saved)
                objects.append(dict(kind=ref.kind, state=saved.state, bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest()))
            values.append(dict(epoch=value, withdrawn=privacy.is_withdrawn(owner), objects=objects))
        generation, current, audit = Reconsent(f.privacy).load(owner)
        result = dict(current=current, generation=generation, epochs=values,
            audit_sha256=None if audit is None else hashlib.sha256(
                json.dumps(audit, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
        self.privacy_checks.append(result)
        return result

    def snapshot(self):
        h = self.h
        # Evidence may be read after closure. Inspect the original registration
        # and ledger directly; never restore ordinary account authorization.
        registration = h.h.release.n.accounts.registration(h.owner, required=False)
        if registration is not None:
            subject = registration['subject']
            quota = h.h.release.ledger.snapshot(subject, h.h.clock() // 86400, free_grant(subject, h.h.clock()))
        else:
            quota = {}
        return dict(model_calls=h.h.model_calls, used=quota.get('used', 0),
                    attempts=quota.get('attempts', 0),
                    research_unchanged=rows(h.h.privacy.store) == self.privacy,
                    image_unchanged=hashlib.sha256(h.f.jpeg).hexdigest() == self.image_hash,
                    registered_installations=len(self.clients),
                    research_readbacks=list(self.research_checks),
                    privacy_readbacks=list(self.privacy_checks),
                    deletion_readbacks=list(self.deletion_checks),
                    routes=list(self.events))

    def deletion_readback(self, operation):
        if type(operation) is not str or re.fullmatch('[0-9a-f]{32}', operation) is None:
            raise ValueError('exact deletion operation required')
        h = self.h
        deletion = h.backend.deletion
        person = deletion._owner(h.owner, operation)
        plan = deletion._plan(person)
        job = deletion._read(person, plan)
        if job is None: raise ValueError('accepted deletion required')
        from lite_apple_oauth import REVOKE_URL
        result = dict(operation=plan['operation'], requested_at=plan['requested_at'],
            inventory_sha256=plan['inventory_sha256'], phase=deletion.lifecycle._read(person)[1]['phase'],
            grant_total=plan['total'], scan=job[1]['scan'], revoked=job[1]['revoked'],
            apple_revoke_calls=h.h.grant.calls.count(REVOKE_URL),
            research_requested=deletion.research_confirmation(person, h.owner, operation),
            service_store_sha256=stored_rows_hash(h.h.stores['operations']),
            audit_sha256=stored_rows_hash(h.h.stores['deletion_audit']))
        self.deletion_checks.append(result)
        return result

    def add_installation(self):
        """Register a second real synthetic attestation key against the same server.

        Reuse only test trust roots; the installation, private signing key and
        assertion counter are independent. No account resolver is patched.
        """
        h = self.h
        if len(self.clients) != 1: raise ValueError('one additional installation only')
        peer = NativeBackendTests()
        peer.h = h.h; peer.backend = h.backend; peer.app = h.app; peer.client = h.app.test_client()
        peer.f = h.f; peer.image_hash = h.image_hash; peer.login_serial = 200
        peer.e = copy.copy(h.e); peer.e.r = copy.copy(h.e.r)
        peer.e.r.key = ec.generate_private_key(ec.SECP256R1())
        peer.e.r.point = peer.e.r.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        peer.e.key_id = peer.e.r.key_id = hashlib.sha256(peer.e.r.point).digest()
        peer.e.challenge = h.e.service.issue_challenge(peer.e.key_id)
        peer.e.encoded = peer.e.make_proof()
        peer.key = b64(peer.e.key_id); peer.counter = 0
        peer.register()
        assert peer.owner != h.owner and peer.key != h.key
        self.clients[peer.owner] = peer
        return dict(owner=peer.owner, now=h.h.clock(), jpeg=base64.b64encode(h.f.jpeg).decode())

    def control(self, kind, body):
        if kind in ('reset', 'reset-research'):
            self.cleanup_state()
            if kind == 'reset-research':
                self.flow = ResearchIngestFlowTests(); self.flow.setUp()
                self.h = self.flow.n
                self.h.app = self.h.client.application
            else:
                self.h = NativeBackendTests(); self.h.setUp()
            self.privacy = rows(self.h.h.privacy.store)
            self.image_hash = hashlib.sha256(self.h.f.jpeg).hexdigest()
            self.events = []
            self.research_checks = []
            self.privacy_checks = []
            self.deletion_checks = []
            self.clients = {self.h.owner:self.h}
            self.revoke_before_service = False
            return dict(owner=self.h.owner, now=self.h.h.clock(),
                        research_owner=self.flow.r.h.owner if self.flow else None,
                        jpeg=base64.b64encode(self.h.f.jpeg).decode())
        if not self.h: raise ValueError('reset required')
        h = self.h
        if kind == 'research-readback': return self.research_readback(body.get('epoch', 'legacy'))
        if kind == 'research-privacy': return self.privacy_readback(body.get('epochs'))
        if kind == 'second-installation': return self.add_installation()
        if kind == 'identity':
            c = body['challenge']; sub = 'apple-human-1'
            person = h.backend.identity.events.identity(sub)
            version = h.backend.identity.grants.cipher.provider.version(h.backend.identity.lifecycle.scope, person)
            if version not in h.h.kms.states: h.h.kms.provision(version)
            signed = h.h.identity.token(c, sub=sub); h.h.grant.token = signed
            return dict(identityToken=signed, state=c['state'], user=sub,
                        authorizationCode='synthetic-code-' + c['operation'])
        if kind == 'purchase':
            a = h.h.release.n.a; a.payload['appAccountToken'] = body['token']
            return dict(transaction=a.payload['transactionId'], original=a.payload['originalTransactionId'],
                        token=body['token'], product=a.payload['productId'], environment='Sandbox', signed=a.sign(a.payload))
        if kind == 'advance':
            seconds = body['seconds']
            if type(seconds) is not int or not 0 <= seconds <= 1800: raise ValueError('bounded clock')
            h.h.release.n.a.now += seconds
            h.e.now = h.h.clock()
            return dict(now=h.h.clock())
        if kind == 'restart':
            h.backend = h.h.compose(); h.app = h.backend.create_app(); h.client = h.app.test_client()
            for client in self.clients.values():
                client.backend = h.backend; client.app = h.app; client.client = h.app.test_client()
            return dict(restarted=True)
        if kind == 'subscription-status':
            status = body.get('status')
            if type(status) is not int or status not in (1, 5): raise ValueError('active/refunded fixture only')
            h.h.release.n.a.status = status
            return dict(status=status)
        if kind == 'revoke-before-service':
            self.revoke_before_service = True
            return dict(armed=True)
        if kind == 'snapshot': return self.snapshot()
        if kind == 'deletion-readback': return self.deletion_readback(body.get('operation'))
        if kind == 'deletion-worker':
            # Test-only, one grant at a time. The production HTTP perimeter has
            # no worker route. This fixture uses fake Apple/KMS exclusively.
            if body.get('limit') != 1 or type(body.get('limit')) is not int:
                raise ValueError('one synthetic grant per explicit worker call')
            evidence = self.deletion_readback(body.get('operation'))
            person = h.backend.deletion._owner(h.owner, evidence['operation'])
            h.backend.deletion.advance(person, limit=1)
            return self.deletion_readback(evidence['operation'])
        if kind == 'finish':
            result = self.snapshot(); result['name'] = body['name']; self.completed.append(result)
            return result
        raise ValueError('unknown control')

    def __call__(self, env, start_response):
        request = Request(env)
        if env.get('REMOTE_ADDR') != '127.0.0.1' or not secrets.compare_digest(
                request.headers.get('X-Synthetic-Wire', ''), self.token):
            return Response(status=403)(env, start_response)
        privacy_delete = request.method == 'DELETE' and re.fullmatch(
            r'/api/v1/lite/data/[0-9a-f]{32}(?:/[0-9a-f]{32})?', request.path)
        if ((request.method != 'POST' and not privacy_delete) or request.content_length is None
                or request.content_length > 2 * 1024 * 1024 or (privacy_delete and request.content_length != 0)):
            return Response(status=400)(env, start_response)
        raw = request.get_data(cache=True)
        if request.path.startswith('/fixture/'):
            try:
                result = self.control(request.path.rsplit('/', 1)[1], json.loads(raw))
                return Response(json.dumps(result), content_type='application/json')(env, start_response)
            except Exception as error:
                # No identity tokens, body, credentials or exception text in logs.
                return Response(json.dumps({'fixture_error': type(error).__name__}), status=500,
                                content_type='application/json')(env, start_response)
        if not self.h or not request.path.startswith('/api/v1/lite/'):
            return Response(status=404)(env, start_response)
        path = request.path
        service = request.headers.get('X-Synthetic-Service', 'native')
        owner = request.headers.get('X-Synthetic-Installation', self.h.owner)
        if service == 'research':
            client = self.flow.r.h if self.flow and owner == self.flow.r.h.owner else None
        elif service == 'native': client = self.clients.get(owner)
        else: client = None
        if client is None: return Response(status=403)(env, start_response)
        if self.revoke_before_service and path == '/api/v1/lite/service/prepare':
            self.revoke_before_service = False
            session = self.h.backend.identity.status(self.h.owner)
            self.h.backend.identity.logout(self.h.owner, session['operation'])
        purpose = 'withdraw' if privacy_delete or '/account/deletion/' in path else 'assert'
        headers = client.headers(path, raw, request.content_type or '', method=request.method, purpose=purpose)
        for k, v in headers.items(): env['HTTP_' + k.upper().replace('-', '_')] = v
        env['wsgi.input'] = io.BytesIO(raw)
        # The actual production Flask router receives the native Swift bytes.
        def capture(status, response_headers, exc_info=None):
            event = dict(path=path, status=int(status.split()[0]),
                                    service=service,
                                    installation_sha256=hashlib.sha256(client.owner.encode()).hexdigest(),
                                    body_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
            if path == '/api/v1/lite/annotation/revision':
                value = json.loads(raw)
                event['revision'] = value.get('revision')
            self.events.append(event)
            return start_response(status, response_headers, exc_info)
        return client.app(env, capture)
