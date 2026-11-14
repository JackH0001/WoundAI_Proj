"""Finite, signed private disposition commands; no default route or activation.

One pinned manifest bounds accounts, time and total reserved dispatches. A lost
completed reply is read back. Unknown execution is never retried automatically:
an independent signed drain observation and a new operator command are required.
Cloud Run invoker IAM and a supervisor remain deployment requirements.
"""
import base64
import copy
import math
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge
from audit_chain_contract import loads_json_object_strict
from lite_account_identity import IdentityRejected
from lite_attest_state import StateUnavailable
from lite_disposition_worker import AccountDispositionWorker, LANES, PENDING
from lite_store_locations import location, overlaps
from lite_subscription_executor import canonical, key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest, _hex, _subject

PATH='/internal/lite/disposition/command'


class CommandRejected(ValueError):pass
class ExecutionUncertain(StateUnavailable):pass


def validate_manifest(manifest, *, expected_manifest, expected_source, expected_image):
    m=copy.deepcopy(manifest)
    _hex(expected_manifest);_hex(expected_source,40);_hex(expected_image)
    if (type(m) is not dict or set(m)!={'v','namespace','source','image','operation','accounts',
                'not_before','expires_at','max_commands','steps','limit','seconds'}
            or type(m['v']) is not int or m['v']!=1 or digest(m)!=expected_manifest
            or m['source']!=expected_source or m['image']!=expected_image):
        raise ValueError('explicit pinned disposition manifest required')
    _subject(m['namespace']);_hex(m['operation'])
    if (any(type(m[k]) is not int for k in ('not_before','expires_at','max_commands','steps','limit','seconds'))
            or not 0<=m['not_before']<m['expires_at']<=m['not_before']+3600
            or not 1<=m['max_commands']<=100 or not 1<=m['steps']<=100
            or m['max_commands']*m['steps']>1000 or not 1<=m['limit']<=20 or not 1<=m['seconds']<=60
            or type(m['accounts']) is not list or not 1<=len(m['accounts'])<=10):
        raise ValueError('finite account, dispatch and time bounds required')
    persons={}
    for a in m['accounts']:
        if type(a) is not dict or set(a)!={'person','plan_sha256'}:raise ValueError('exact account plan required')
        _hex(a['person']);_hex(a['plan_sha256'])
        if a['person'] in persons:raise ValueError('duplicate account')
        persons[a['person']]=a['plan_sha256']
    return m, persons


class DispositionExecutor:
    immutable=staticmethod(SubscriptionRuntime.immutable)

    def __init__(self,*,enabled=False,manifest,expected_manifest,expected_source,expected_image,
                 worker,store,audit_store,operator_keys,drain_keys,clock=time.time):
        if enabled is not True or not isinstance(worker, AccountDispositionWorker) or not callable(clock):
            raise ValueError('explicit worker enablement required')
        m, persons = validate_manifest(manifest, expected_manifest=expected_manifest,
            expected_source=expected_source, expected_image=expected_image)
        def checked(keys):
            if type(keys) is not dict or not 1<=len(keys)<=4:raise ValueError('bounded verification keys required')
            for n,b in keys.items():
                _subject(n)
                if type(b) is not bytes or len(b)!=32:raise ValueError('Ed25519 public key required')
            return dict(keys)
        self.operators,self.drainers=checked(operator_keys),checked(drain_keys)
        if set(self.operators.values())&set(self.drainers.values()):raise ValueError('independent drain signer required')
        p=worker.p;d=p.deletion;r=worker.r.w
        peers=(worker.store,worker.audit,worker.s.jobs,worker.s.audit,worker.r.jobs,worker.r.audit,
            p.store,p.audit,p.results.store,p.results.audit,p.results.ops.store,p.results.ops.ledger.store,
            p.cipher.store,p.cipher.audit,d.store,d.audit,d.lifecycle.store,d.lifecycle.audit,d.grants.store,
            d.grants.audit,d.bridge.store,d.bridge.audit,d.bridge.registry.store,r.store,r.audit,r.c.store,r.c.audit,r.r.store)
        own=(location(store),location(audit_store))
        media=[('gcs',worker.r.bucket.name,prefix.rstrip('/')) for prefix in worker.r.policy['prefixes']]
        if overlaps(*own) or any(overlaps(a,b) for a in own for b in [location(x) for x in peers]+media):
            raise ValueError('executor state and audit must be independent')
        self.m,self.identity,self.w,self.store,self.audit,self.clock=m,expected_manifest,worker,store,audit_store,clock
        self.accounts=persons
        # Same namespace cannot silently install a new manifest/budget. Read-only
        # construction; actual acceptance appends this marker on first execution.
        old=store.load(key('disposition-executor-binding',m['namespace']))
        if old is not None and old[1]!=self._binding():raise ValueError('executor namespace already bound')

    def _binding(self):return dict(v=1,manifest=self.identity)
    def _root_key(self):return key('disposition-executor-state',self.m['namespace'])
    def _now(self):
        n=self.clock()
        if type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<2**53:
            raise StateUnavailable('trusted executor clock unavailable')
        return n
    def _window(self):
        if not self.m['not_before']<=self._now()<self.m['expires_at']:
            raise CommandRejected('execution window closed')

    def _verify(self,envelope,*,drain=False,check_time=True):
        try:
            if (type(envelope) is not dict or set(envelope)!={'key_id','payload','signature'}
                    or type(envelope['payload']) is not dict or len(canonical(envelope))>16384):raise ValueError()
            keys=self.drainers if drain else self.operators
            Ed25519PublicKey.from_public_bytes(keys[envelope['key_id']]).verify(
                base64.b64decode(envelope['signature'],validate=True),canonical(envelope['payload']))
            p=copy.deepcopy(envelope['payload'])
            fields={'kind','manifest','command','expected_state','issued_at','expires_at'}
            fields|={'drained'} if drain else {'action','person','replaces'}
            if (set(p)!=fields or p['kind']!=('lite-disposition-drain/1' if drain else 'lite-disposition-command/1')
                    or p['manifest']!=self.identity or any(type(p[n]) is not int for n in ('issued_at','expires_at'))
                    or not 0<=p['issued_at']<p['expires_at']
                    or check_time and not p['issued_at']<=self._now()<p['expires_at']
                    or not 0<p['expires_at']-p['issued_at']<=(60 if drain else 300)):raise ValueError()
            _hex(p['command'],32);_hex(p['expected_state'])
            if drain:
                if p['drained'] is not True:raise ValueError()
            else:
                if p['action'] not in ('inspect','run','resume'):raise ValueError()
                _hex(p['person'])
                if p['replaces'] is not None:_hex(p['replaces'],32)
                if (p['action']=='resume')!=(p['replaces'] is not None):raise ValueError()
            return p
        except Exception:
            raise CommandRejected('invalid, unknown or expired signed command') from None

    def _account(self,person):
        if person not in self.accounts:raise CommandRejected('account outside reviewed manifest')
        if digest(self.w._plan(person))!=self.accounts[person]:raise CommandRejected('account plan changed')

    def _read(self):
        row=self.store.load(self._root_key())
        binding=self.store.load(key('disposition-executor-binding',self.m['namespace']))
        if row is None:
            # Never reset a missing budget. An interrupted first acceptance that
            # only wrote the binding also stops for operator investigation.
            if binding is not None:raise StateUnavailable('bound executor state missing; never reset budget')
            return None
        j=row[1]
        if (binding is None or binding[1]!=self._binding() or type(j) is not dict
                or set(j)!={'v','manifest','revision','used','commands'} or type(j['v']) is not int or j['v']!=1
                or j['manifest']!=self.identity or type(j['revision']) is not int or not 1<=j['revision']<=300
                or type(j['used']) is not int or type(j['commands']) is not dict
                or not 1<=len(j['commands'])<=self.m['max_commands']
                or j['used']!=len(j['commands'])*self.m['steps']):raise StateUnavailable('executor checkpoint corrupt')
        active=0;replaced=[]
        for name,c in j['commands'].items():
            _hex(name,32)
            if (type(c) is not dict or set(c)!={'request','phase','receipt','replaces'}
                    or c['phase'] not in ('running','uncertain','complete','superseded')):
                raise StateUnavailable('executor command corrupt')
            _hex(c['request'])
            proof=self.audit.load(key('disposition-command',digest([self.identity,name])))
            if proof is None or digest(proof[1])!=c['request']:raise StateUnavailable('original command proof missing')
            p=self._verify(proof[1],check_time=False)
            if p['command']!=name or p['manifest']!=self.identity or p['replaces']!=c['replaces']:
                raise StateUnavailable('command binding differs')
            active+=c['phase'] in ('running','uncertain')
            if (c['receipt'] is not None)!=(c['phase']=='complete'):raise StateUnavailable('command result missing')
            if c['receipt'] is not None:
                _hex(c['receipt']);r=self.audit.load(key('disposition-execution-receipt',c['receipt']))
                if r is None or digest(r[1])!=c['receipt'] or r[1]['command']!=name:
                    raise StateUnavailable('original execution receipt missing')
            if c['replaces'] is not None:
                old=c['replaces']
                if old==name or old not in j['commands'] or j['commands'][old]['phase']!='superseded':
                    raise StateUnavailable('invalid supersession')
                replaced.append(old)
                observation=self.audit.load(key('disposition-execution-drain',c['request']))
                if observation is None:raise StateUnavailable('original drain proof missing')
                observed=self._verify(observation[1],drain=True,check_time=False)
                if observed['command']!=old or observed['expected_state']!=p['expected_state']:
                    raise StateUnavailable('drain proof binding differs')
                seen={name};cursor=old
                while cursor is not None:
                    if cursor in seen:raise StateUnavailable('cyclic execution history')
                    seen.add(cursor);cursor=j['commands'][cursor]['replaces']
        if active>1 or len(replaced)!=len(set(replaced)) or set(replaced)!={n for n,c in j['commands'].items() if c['phase']=='superseded'}:
            raise StateUnavailable('executor ownership differs')
        for kind,value in (('plan',self.m),('checkpoint',j)):
            p=self.audit.load(key('disposition-executor-'+kind,digest(value)))
            if p is None or p[1]!=value:raise StateUnavailable('original executor audit missing')
        return row

    @staticmethod
    def _state(row):return digest(None if row is None else row[1])

    def _save(self,old,j,*,claim=False):
        self.immutable(self.audit,key('disposition-executor-checkpoint',digest(j)),j)
        try:won=self.store.compare_exchange(self._root_key(),0 if old is None else old[0],j)
        except StateUnavailable:
            # A claim's lost response cannot prove which concurrent caller won.
            won=False
        if claim and not won:raise ExecutionUncertain('claim not exclusively confirmed; inspect before recovery')
        saved=self._read()
        if saved is None or saved[1]!=j:raise ExecutionUncertain('execution ownership unconfirmed')
        return saved

    def _safe_worker(self,person):
        raw=self.w.status(person)
        return dict(v=1,revision=raw['revision'],paused=raw['paused'],control_revision=raw['control_revision'],
            states={n:raw['states'][n] for n in LANES},pending_lanes=list(PENDING)+(['shared_key_retirement'] if self.w.p.shared else []),
            account_deleted=False,physical_backups_erased=False,observed_scope_only=True)

    def inspect(self,person):
        self._account(person);row=self._read();j=None if row is None else row[1]
        return dict(v=1,manifest=self.identity,state_sha256=self._state(row),
            used_dispatch_budget=0 if j is None else j['used'],
            max_dispatch_budget=self.m['steps']*self.m['max_commands'],
            commands={} if j is None else {n:c['phase'] for n,c in j['commands'].items()},
            worker=self._safe_worker(person),account_deleted=False)

    def _owned(self,name,request_hash):
        row=self._read()
        return row is not None and row[1]['commands'].get(name)==dict(
            request=request_hash,phase='running',receipt=None,replaces=row[1]['commands'].get(name,{}).get('replaces'))

    def execute(self,envelope,*,drain=None):
        p=self._verify(envelope);self._account(p['person'])
        if p['action']=='inspect':
            if drain is not None:raise CommandRejected('inspect cannot accept drain evidence')
            return self.inspect(p['person'])
        self._window();row=self._read();name=p['command'];rh=digest(envelope)
        j=None if row is None else row[1]
        if j and name in j['commands']:
            old=j['commands'][name]
            if old['request']!=rh:raise CommandRejected('command ID already bound')
            if old['phase']=='complete':
                return copy.deepcopy(self.audit.load(key('disposition-execution-receipt',old['receipt']))[1])
            raise ExecutionUncertain('command pending; inspect before reviewed recovery')
        if p['expected_state']!=self._state(row):raise CommandRejected('executor state changed; inspect again')
        active=[] if j is None else [n for n,c in j['commands'].items() if c['phase'] in ('running','uncertain')]
        if p['action']=='resume':
            observation=self._verify(drain,drain=True)
            if (active!=[p['replaces']] or observation['command']!=p['replaces']
                    or observation['expected_state']!=p['expected_state']):
                raise CommandRejected('drain evidence does not match interrupted execution')
            previous=self.audit.load(key('disposition-command',digest([self.identity,p['replaces']])))
            if previous is None or previous[1]['payload']['person']!=p['person']:
                raise CommandRejected('resume must retain the original account')
        elif active or drain is not None:raise CommandRejected('another execution requires reviewed recovery')
        commands={} if j is None else copy.deepcopy(j['commands'])
        if len(commands)>=self.m['max_commands']:raise CommandRejected('finite execution budget exhausted')
        self.immutable(self.audit,key('disposition-executor-plan',self.identity),self.m)
        self.immutable(self.store,key('disposition-executor-binding',self.m['namespace']),self._binding())
        self.immutable(self.audit,key('disposition-command',digest([self.identity,name])),copy.deepcopy(envelope))
        if drain is not None:self.immutable(self.audit,key('disposition-execution-drain',rh),copy.deepcopy(drain))
        if p['replaces'] is not None:commands[p['replaces']]['phase']='superseded'
        commands[name]=dict(request=rh,phase='running',receipt=None,replaces=p['replaces'])
        claimed=dict(v=1,manifest=self.identity,revision=1 if j is None else j['revision']+1,
            used=len(commands)*self.m['steps'],commands=commands)
        self._save(row,claimed,claim=True)
        try:
            # Authorization is checked again immediately before dispatch and at
            # each worker cancellation boundary. Existing in-flight RPCs still
            # require real supervisor drain before a signed recovery is valid.
            self._verify(envelope);self._window();self._account(p['person'])
            if drain is not None:self._verify(drain,drain=True)
            def stopped():
                return (not self._owned(name,rh) or self._now()>=min(p['expires_at'],self.m['expires_at']))
            result=self.w.run(p['person'],apply=True,max_steps=self.m['steps'],limit=self.m['limit'],
                max_seconds=min(self.m['seconds'],p['expires_at']-self._now(),self.m['expires_at']-self._now()),cancelled=stopped)
            safe=self._safe_worker(p['person'])
            receipt=dict(v=1,manifest=self.identity,command=name,request_sha256=rh,worker=safe,
                reserved_dispatches=self.m['steps'],dispatches=result['dispatches'],stop_reason=result['stop_reason'],
                deadline_is_cooperative=True,account_deleted=False,physical_backups_erased=False)
            self.immutable(self.audit,key('disposition-execution-receipt',digest(receipt)),receipt)
            live=self._read()
            if not self._owned(name,rh):raise ExecutionUncertain('execution superseded')
            value=copy.deepcopy(live[1]);value['revision']+=1
            value['commands'][name]=dict(value['commands'][name],phase='complete',receipt=digest(receipt))
            self._save(live,value)
            return receipt
        except Exception:
            live=self._read()
            if self._owned(name,rh):
                value=copy.deepcopy(live[1]);value['revision']+=1;value['commands'][name]['phase']='uncertain'
                self._save(live,value)
            raise ExecutionUncertain('execution incomplete; preserve command and inspect') from None

    def create_app(self):
        """Only for a separate IAM-private service with pinned trusted composition.

        Signature verification is additional to service-level invoker IAM. This
        factory does not configure IAM, choose credentials or grant cleanup rights.
        """
        app=Flask('lite-private-disposition',static_folder=None);app.config['MAX_CONTENT_LENGTH']=32768
        @app.before_request
        def route_guard():
            if request.method!='POST' or request.path!=PATH:return jsonify(error='route_not_supported'),404
        @app.post(PATH)
        def command():
            try:
                if request.mimetype!='application/json':raise ValueError()
                b=loads_json_object_strict(request.get_data().decode())
                if set(b)!={'command','drain'}:raise ValueError()
                return jsonify(self.execute(b['command'],drain=b['drain']))
            except RequestEntityTooLarge:return jsonify(error='disposition_command_too_large'),413
            except (ValueError,KeyError,TypeError,IdentityRejected):return jsonify(error='disposition_command_rejected'),409
            except StateUnavailable:return jsonify(error='disposition_execution_unconfirmed',retry_automatically=False),503
            except Exception:return jsonify(error='disposition_internal_error',retry_automatically=False),500
        @app.after_request
        def private(r):r.headers['Cache-Control']='no-store';return r
        return app
