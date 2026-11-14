"""Signed, bounded rewrap preparation, NOT a shared-key destruction executor.

No route, default enablement, credential discovery or KMS lifecycle permission.
Independent drain signatures authorize recovery of an uncertain dispatch only;
they never attest to complete account / generation / backup inventory.
"""
import base64
import copy
import math
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from lite_attest_state import StateUnavailable
from lite_rotating_grant_keys import RotatingGrantCipher
from lite_store_locations import location, overlaps
from lite_subscription_executor import canonical, key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest, _hex


class PreparationRejected(ValueError): pass


class SharedKEKPreparation:
    immutable = staticmethod(SubscriptionRuntime.immutable)

    def __init__(self, *, enabled=False, manifest, expected_manifest, expected_source, cipher, store,
                 audit_store, operator_key, drain_key, clock=time.time):
        if enabled is not True or not isinstance(cipher, RotatingGrantCipher) or not callable(clock):
            raise ValueError('explicit shared-key preparation required')
        _hex(expected_manifest); _hex(expected_source,40)
        m = copy.deepcopy(manifest)
        try:
            if (set(m) != {'v','scope','source_commit','rotation','source_version','target_version',
                          'accounts','not_before','expires_at','max_steps','max_dispatches'}
                    or type(m['v']) is not int or m['v'] != 1 or digest(m) != expected_manifest
                    or m['source_commit'] != expected_source
                    or m['scope'] != cipher.scope or m['target_version'] != cipher.provider.write_version):
                raise ValueError()
            _hex(m['source_commit'],40); _hex(m['rotation'])
            for name in ('source_version','target_version'): cipher.provider._name(m[name])
            if int(m['target_version'].rsplit('/',1)[1]) <= int(m['source_version'].rsplit('/',1)[1]):
                raise ValueError()
            if (any(type(m[n]) is not int for n in ('not_before','expires_at','max_steps','max_dispatches'))
                    or not 0 <= m['not_before'] < m['expires_at'] <= m['not_before']+3600
                    or not 1 <= m['max_steps'] <= 5 or not 1 <= m['max_dispatches'] <= 32
                    or type(m['accounts']) is not list or not 1 <= len(m['accounts']) <= 50):
                raise ValueError()
            people=[]
            for a in m['accounts']:
                if type(a) is not dict or set(a) != {'person','source_sha256'}: raise ValueError()
                _hex(a['person']); _hex(a['source_sha256']); people.append(a['person'])
            if people != sorted(set(people)): raise ValueError()
            if (type(operator_key) is not bytes or type(drain_key) is not bytes
                    or len(operator_key) != 32 or len(drain_key) != 32 or operator_key == drain_key):
                raise ValueError()
        except (KeyError,TypeError,ValueError):
            raise ValueError('exact bounded preparation manifest and separate signers required') from None
        own = [location(store),location(audit_store)]
        peers = [location(s) for s in (cipher.store,cipher.audit,cipher.lifecycle.store,cipher.lifecycle.audit)]
        if overlaps(*own) or any(overlaps(a,b) for a in own for b in peers):
            raise ValueError('preparation state and audit must be independent')
        self.m,self.identity,self.cipher = m,expected_manifest,cipher
        self.store,self.audit,self.clock = store,audit_store,clock
        self.operator,self.drainer = operator_key,drain_key

    def _key(self): return key('shared-kek-preparation',self.identity)
    def _proof(self,j): return key('shared-kek-preparation-state',digest(j))
    def _now(self):
        n=self.clock()
        if type(n) not in (int,float) or not math.isfinite(n) or not 0 <= n < 2**53:
            raise StateUnavailable('trusted preparation clock unavailable')
        return n

    def _signed(self,envelope,*,drain=False):
        try:
            if (type(envelope) is not dict or set(envelope) != {'payload','signature'}
                    or len(canonical(envelope)) > 8192): raise ValueError()
            p=envelope['payload']
            Ed25519PublicKey.from_public_bytes(self.drainer if drain else self.operator).verify(
                base64.b64decode(envelope['signature'],validate=True),canonical(p))
            fields={'kind','manifest','command','expected_state','issued_at','expires_at'}
            fields |= {'dispatch_drained'} if drain else {'action'}
            if (type(p) is not dict or set(p) != fields
                    or p['kind'] != ('shared-kek-dispatch-drain/1' if drain else 'shared-kek-prepare/1')
                    or p['manifest'] != self.identity
                    or any(type(p[n]) is not int for n in ('issued_at','expires_at'))
                    or not 0 <= p['issued_at'] <= self._now() < p['expires_at']
                    or not 0 < p['expires_at']-p['issued_at'] <= (60 if drain else 300)):
                raise ValueError()
            _hex(p['command'],32); _hex(p['expected_state'])
            if drain:
                if p['dispatch_drained'] is not True: raise ValueError()
            elif p['action'] not in ('advance','resume'): raise ValueError()
            return copy.deepcopy(p)
        except Exception:
            raise PreparationRejected('invalid or expired preparation signature') from None

    def _read(self):
        row=self.store.load(self._key())
        started=self.audit.load(key('shared-kek-preparation-started',self.identity))
        if row is None:
            if started is not None: raise StateUnavailable('preparation checkpoint missing; never reset')
            return None
        j=row[1]
        try:
            if (set(j) != {'v','manifest','revision','phase','command','commands','cursor','results'}
                    or type(j['v']) is not int or j['v'] != 1 or j['manifest'] != self.identity
                    or type(j['revision']) is not int or j['revision'] < 1
                    or j['phase'] not in ('running','uncertain','waiting','complete')
                    or type(j['cursor']) is not int or j['cursor'] != len(j['results'])
                    or not 0 <= j['cursor'] <= len(self.m['accounts'])
                    or type(j['commands']) is not list or not 1 <= len(j['commands']) <= self.m['max_dispatches']
                    or len(set(j['commands'])) != len(j['commands']) or j['command'] != j['commands'][-1]
                    or (j['phase']=='complete') != (j['cursor']==len(self.m['accounts']))): raise ValueError()
            for command in j['commands']:
                _hex(command)
                proof=self.audit.load(key('shared-kek-preparation-command',command))
                if proof is None or digest(proof[1]) != command: raise ValueError()
            for i,r in enumerate(j['results']):
                if (set(r) != {'person','state','receipt_sha256'} or r['person'] != self.m['accounts'][i]['person']
                        or r['state'] not in ('rewrapped','closed')): raise ValueError()
                _hex(r['receipt_sha256'])
                proof=self.audit.load(key('shared-kek-preparation-account',r['receipt_sha256']))
                if proof is None or digest(proof[1]) != r['receipt_sha256']: raise ValueError()
            proof=self.audit.load(self._proof(j))
            if proof is None or proof[1] != j: raise ValueError()
        except (KeyError,TypeError,ValueError):
            raise StateUnavailable('preparation checkpoint or audit corrupt') from None
        return row

    @staticmethod
    def _state(row): return digest(None if row is None else row[1])

    def _save(self,row,j,*,claim=False):
        self.immutable(self.audit,self._proof(j),j)
        try: won=self.store.compare_exchange(self._key(),0 if row is None else row[0],j)
        except StateUnavailable: won=False
        if claim and not won:
            raise StateUnavailable('dispatch ownership uncertain; independent drain required')
        saved=self._read()
        if saved is None or saved[1] != j: raise StateUnavailable('preparation checkpoint unconfirmed')
        return saved

    def inspect(self):
        row=self._read(); j=None if row is None else row[1]
        return dict(schema='shared-kek-preparation-status/1',manifest=self.identity,
            state_sha256=self._state(row),phase='not_started' if j is None else j['phase'],
            cursor=0 if j is None else j['cursor'],selected_accounts=len(self.m['accounts']),
            dispatches=0 if j is None else len(j['commands']),
            last_command=None if j is None else j['command'],
            selected_batch_complete=j is not None and j['phase']=='complete',
            complete_inventory_verified=False,all_writers_drained=False,
            kms_destruction_authorized=False,account_deleted=False)

    def _window(self,envelope):
        self._signed(envelope)
        if not self.m['not_before'] <= self._now() < self.m['expires_at']:
            raise PreparationRejected('preparation execution window closed')

    def _check_account(self,a):
        row=self.cipher._read(a['person'])
        if row is None: raise StateUnavailable('selected account key missing')
        c=row[1]
        if c['phase']=='closed':
            receipt=self.cipher.closure_receipt(a['person'],c['closure'])
            self.cipher.validate_closure_receipt(a['person'],c['closure'],receipt)
            return 'closed',receipt
        if digest(c)==a['source_sha256'] and c['version']==self.m['source_version']:
            return 'pending',None
        # Version equality alone cannot prove that this is our original rewrap.
        ident=digest([self.cipher.scope,a['person'],a['source_sha256'],self.m['target_version']])
        r=self.cipher.audit.load(key('shared-grant-rewrap-result',ident))
        if (c['version']==self.m['target_version'] and r is not None
                and r[1]==dict(v=1,operation=ident,source_sha256=a['source_sha256'],target_sha256=digest(c))):
            return 'rewrapped',r[1]
        # A committed current row can precede the immutable completion receipt.
        proposal=self.cipher.store.load(key('shared-grant-rewrap-proposal',ident))
        if proposal is not None and type(proposal[1]) is dict and proposal[1].get('record')==c:
            return 'pending',None
        raise StateUnavailable('selected source changed; never infer or overwrite')

    def execute(self,envelope,*,drain=None):
        p=self._signed(envelope); self._window(envelope)
        row=self._read(); j=None if row is None else row[1]; rh=digest(envelope)
        if j is not None and rh in j['commands']:
            if rh==j['command'] and j['phase'] in ('running','uncertain'):
                raise StateUnavailable('dispatch already pending; independent drain required')
            return self.inspect()  # Read-only acknowledgement, never rerun an old command.
        if p['expected_state'] != self._state(row): raise PreparationRejected('checkpoint changed; inspect first')
        active=j is not None and j['phase'] in ('running','uncertain')
        if p['action']=='resume':
            d=self._signed(drain,drain=True)
            if not active or d['expected_state']!=p['expected_state'] or d['command']!=p['command']:
                raise PreparationRejected('independent drain must bind this exact recovery')
        elif active or drain is not None: raise PreparationRejected('pending dispatch requires signed recovery')
        if j is not None and j['phase']=='complete': return self.inspect()
        used=[] if j is None else j['commands']
        if len(used)>=self.m['max_dispatches']: raise PreparationRejected('reviewed dispatch budget exhausted')
        # Detect a wrong source anywhere in this bounded batch before installing
        # the global monotonic write fence or dispatching the first rewrap.
        for a in self.m['accounts']: self._check_account(a)
        if self.cipher.provider.describe(self.m['target_version'])!='ENABLED':
            raise StateUnavailable('target key unavailable; no write fence installed')
        self.immutable(self.audit,key('shared-kek-preparation-plan',self.identity),self.m)
        self.immutable(self.audit,key('shared-kek-preparation-command-id',digest([self.identity,p['command']])),
                       dict(request_sha256=rh))
        self.immutable(self.audit,key('shared-kek-preparation-command',rh),copy.deepcopy(envelope))
        if drain is not None:
            self.immutable(self.audit,key('shared-kek-preparation-drain',rh),copy.deepcopy(drain))
        updated=dict(v=1,manifest=self.identity,revision=1 if j is None else j['revision']+1,
            phase='running',command=rh,commands=used+[rh],cursor=0 if j is None else j['cursor'],
            results=[] if j is None else copy.deepcopy(j['results']))
        self.immutable(self.audit,key('shared-kek-preparation-started',self.identity),dict(manifest=self.identity))
        row=self._save(row,updated,claim=True)
        try:
            self._window(envelope)
            self.cipher.fence_write_version(self.m['source_version'],target=self.m['target_version'],rotation=self.m['rotation'])
            stop=min(row[1]['cursor']+self.m['max_steps'],len(self.m['accounts']))
            while row[1]['cursor']<stop:
                self._window(envelope)
                if self._read()!=row: raise StateUnavailable('dispatch ownership changed')
                a=self.m['accounts'][row[1]['cursor']]
                state,receipt=self._check_account(a)
                if state=='pending':
                    receipt=self.cipher.rewrap(a['person'],expected_record_sha256=a['source_sha256'])
                    state='rewrapped'
                self._window(envelope)
                self.immutable(self.audit,key('shared-kek-preparation-account',digest(receipt)),receipt)
                result=dict(person=a['person'],state=state,receipt_sha256=digest(receipt))
                j=row[1]; cursor=j['cursor']+1
                updated=dict(j,revision=j['revision']+1,cursor=cursor,results=j['results']+[result],
                    phase='complete' if cursor==len(self.m['accounts']) else 'waiting' if cursor==stop else 'running')
                row=self._save(row,updated)
            return self.inspect()
        except Exception:
            # Never roll a surviving account back onto the fenced version.
            # A live/unknown dispatch is not automatically rerun after timeout.
            current=self._read()
            if current is not None and current[1]['command']==rh and current[1]['phase']=='running':
                self._save(current,dict(current[1],phase='uncertain',revision=current[1]['revision']+1))
            raise
