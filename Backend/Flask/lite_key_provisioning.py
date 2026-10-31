"""Isolated bounded account-key provisioner. No decrypt, delete, IAM or auto-activation.

Only verified lifecycle grant members are eligible, never arbitrary user IDs.
A reviewed finite window and a durable total POST budget bound new resources.
Cloud Run invocation IAM and separate provider credentials remain deployment gates.
"""
import base64
import copy
from flask import Flask, jsonify, request
from audit_chain_contract import loads_json_object_strict
from lite_account_lifecycle import AccountLifecycle, AccountClosing
from lite_attest_state import StateUnavailable, CAS_ATTEMPTS
from lite_grant_key_custody import GoogleGrantKeyProvider
from lite_key_readiness import PATH
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest, _hex, _subject


def tag(value):return base64.b32encode(bytes.fromhex(value)).decode().rstrip('=').lower()


class CreateOnlyKeyProvider:
    """Only exact GETs and a create with deterministic cryptoKeyId; no retries."""
    def __init__(self, *, session, keyring):
        self.names=GoogleGrantKeyProvider(session=session,keyring=keyring)
        self.session,self.keyring=session,keyring
    def version(self,scope,person):return self.names.version(scope,person)
    def spec(self,scope,person):
        return dict(purpose='ENCRYPT_DECRYPT',versionTemplate=dict(
            algorithm='GOOGLE_SYMMETRIC_ENCRYPTION',protectionLevel='SOFTWARE'),
            importOnly=False,destroyScheduledDuration='2592000s',
            labels={'woundai-use':'apple-grant-v1','woundai-scope':tag(scope),'woundai-person':tag(person)})
    def call(self,method,path,body=None,*,params=None):
        try:
            r=self.session.request(method,'https://cloudkms.googleapis.com/v1/'+path,
                json=body,params=params,timeout=(5,10),allow_redirects=False)
            if r.status_code in (404,409):return r.status_code,None
            if r.status_code!=200 or len(r.content)>32768:raise ValueError()
            return 200,loads_json_object_strict(r.content.decode())
        except Exception:raise StateUnavailable('key provisioning provider unavailable; preserve intent') from None
    def inspect(self,scope,person):
        version=self.version(scope,person);name=version.rsplit('/cryptoKeyVersions/',1)[0]
        status,row=self.call('GET',name)
        if status==404:return None
        if status!=200:raise StateUnavailable('account key read unconfirmed')
        spec=self.spec(scope,person)
        if (row.get('name')!=name or any(row.get(k,False if k=='importOnly' else None)!=v for k,v in spec.items())
                or any(row.get(k) for k in ('rotationPeriod','nextRotationTime','cryptoKeyBackend'))
                or type(row.get('primary')) is not dict or row['primary'].get('name')!=version):
            raise StateUnavailable('existing account key policy differs; never replace')
        status,ver=self.call('GET',version)
        if (status!=200 or ver.get('name')!=version or ver.get('state')!='ENABLED'
                or ver.get('algorithm')!='GOOGLE_SYMMETRIC_ENCRYPTION' or ver.get('protectionLevel')!='SOFTWARE'
                or ver.get('reimportEligible',False) is not False
                or any(ver.get(k) for k in ('importJob','importTime','importFailureReason'))):
            raise StateUnavailable('account key version not usable; never rotate or restore')
        return dict(version=version,policy_sha256=digest(spec))
    def create(self,scope,person):
        version=self.version(scope,person);ident=version.split('/cryptoKeys/')[1].split('/')[0]
        status,_=self.call('POST',self.keyring+'/cryptoKeys',self.spec(scope,person),
                          params=dict(cryptoKeyId=ident,skipInitialVersionCreation='false'))
        if status not in (200,409):raise StateUnavailable('account key creation unconfirmed')


class AccountKeyProvisioner:
    immutable=staticmethod(SubscriptionRuntime.immutable)
    def __init__(self, *, enabled=False, manifest, expected_manifest, lifecycle, provider, store, audit_store, clock):
        _hex(expected_manifest)
        m=copy.deepcopy(manifest)
        if (enabled is not True or type(m) is not dict or digest(m)!=expected_manifest
                or set(m)!={'v','namespace','scope','keyring','source','image','operation','not_before','expires_at','max_creates'}
                or type(m['v']) is not int or m['v']!=1
                or any(type(m[k]) is not int for k in ('not_before','expires_at','max_creates'))
                or not 0<=m['not_before']<m['expires_at']<=m['not_before']+3600
                or not 1<=m['max_creates']<=1000):
            raise ValueError('explicit fixed, time- and count-bounded provisioning plan required')
        _subject(m['namespace']);_hex(m['source'],40)
        for k in ('scope','image','operation'):_hex(m[k])
        if (not isinstance(lifecycle,AccountLifecycle) or not isinstance(provider,CreateOnlyKeyProvider)
                or m['scope']!=lifecycle.scope or m['keyring']!=provider.keyring or not callable(clock)):
            raise ValueError('matching provisioning authority required')
        # Separate physical stores. There is no list API and no per-batch account allowlist.
        from lite_native_backend import location,overlaps
        locations=[location(s) for s in (store,audit_store,lifecycle.store,lifecycle.audit)]
        if any(overlaps(a,b) for i,a in enumerate(locations) for b in locations[i+1:]):
            raise ValueError('provisioning, lifecycle and audit must be separate')
        self.manifest,self.identity,self.life,self.provider=m,expected_manifest,lifecycle,provider
        self.store,self.audit,self.clock=store,audit_store,clock
        self.immutable(self.audit,key('key-provision-plan',self.identity),m)
    def _job_key(self,person):return key('key-provision-job',digest([self.life.scope,person]))
    def _body(self,body):
        if (type(body) is not dict or set(body)!={'v','scope','person','grant','version'}
                or type(body['v']) is not int or body['v']!=1 or body['scope']!=self.life.scope):
            raise ValueError('exact verified account request required')
        _hex(body['person']);_hex(body['grant'])
        if body['version']!=self.provider.version(body['scope'],body['person']):raise ValueError('wrong account key')
        return body['person'],body['grant']
    def _eligible(self,person,grant):
        self.life.require_active(person)
        if self.life.member(person,grant) is None:raise StateUnavailable('verified grant membership required')
    def _job(self,person):
        row=self.store.load(self._job_key(person))
        if row is None:return None
        c=row[1]
        if (type(c) is not dict or set(c)!={'v','scope','person','version','phase'}
                or type(c['v']) is not int or c['v']!=1 or c['scope']!=self.life.scope or c['person']!=person
                or c['version']!=self.provider.version(self.life.scope,person)
                or c['phase'] not in ('checking','creating','ready','closed')):
            raise StateUnavailable('key provisioning state corrupt; never reset')
        return row
    def _window(self):
        n=self.clock();m=self.manifest
        if type(n) is not int or not m['not_before']<=n<m['expires_at']:
            raise StateUnavailable('key creation window closed')
    def _reserve(self,person,grant):
        self._window();k=key('key-provision-budget',self.identity)
        for _ in range(CAS_ATTEMPTS):
            old=self.store.load(k);count=0 if old is None else old[1].get('used')
            if (old is not None and (old[1]!={'v':1,'manifest':self.identity,'used':count}
                    or type(old[1].get('v')) is not int or type(count) is not int
                    or not 0<=count<=self.manifest['max_creates'])):
                raise StateUnavailable('provisioning budget corrupt')
            if count>=self.manifest['max_creates']:raise StateUnavailable('provisioning creation budget exhausted')
            try:won=self.store.compare_exchange(k,0 if old is None else old[0],dict(v=1,manifest=self.identity,used=count+1))
            except StateUnavailable:raise StateUnavailable('creation budget reservation unconfirmed') from None
            if won:
                entry=dict(v=1,manifest=self.identity,slot=count+1,scope=self.life.scope,person=person,grant=grant,
                    version=self.provider.version(self.life.scope,person))
                self.immutable(self.audit,key('key-provision-create-intent',digest([self.identity,count+1])),entry)
                return entry
        raise StateUnavailable('provisioning budget contention')
    def _phase(self,person,phase):
        for _ in range(CAS_ATTEMPTS):
            row=self._job(person)
            if row is None:raise StateUnavailable('provisioning job missing')
            if row[1]['phase']=='closed':raise AccountClosing('key provisioning fenced by account closure')
            if row[1]['phase']==phase:return
            # A concurrent successful readback must not be regressed to creating.
            if phase=='creating' and row[1]['phase']=='ready':return
            try:self.store.compare_exchange(self._job_key(person),row[0],dict(row[1],phase=phase))
            except StateUnavailable:pass
        raise StateUnavailable('key provisioning checkpoint unconfirmed')
    def ensure(self,body):
        person,grant=self._body(body);self._eligible(person,grant)
        row=self._job(person)
        if row is None:
            self._window()
            initial=dict(v=1,scope=self.life.scope,person=person,version=body['version'],phase='checking')
            try:self.store.compare_exchange(self._job_key(person),0,initial)
            except StateUnavailable:pass
            row=self._job(person)
            if row is None:raise StateUnavailable('provisioning intent unconfirmed')
        if row[1]['phase']=='closed':raise AccountClosing('key provisioning closed')
        observed=self.provider.inspect(self.life.scope,person)
        if observed is not None and row[1]['phase']=='checking':
            # Re-read: another worker may have advanced and created after our first read.
            if self._job(person)[1]['phase']=='checking':
                raise StateUnavailable('unowned pre-existing key; explicit reconciliation required')
        if observed is None:
            if row[1]['phase']=='ready':raise StateUnavailable('ready key missing; never recreate')
            self._phase(person,'creating')
            self._reserve(person,grant)
            self._eligible(person,grant);self._window()
            # Deterministic create-only ID: even simultaneous requests cannot
            # make version 2 or an alternative key after an uncertain response.
            self.provider.create(self.life.scope,person)
            observed=self.provider.inspect(self.life.scope,person)
            if observed is None:raise StateUnavailable('created key not yet visible; retry readback')
        self.immutable(self.audit,key('key-provision-observed',digest([self.life.scope,person])),
            dict(v=1,scope=self.life.scope,person=person,**observed))
        try:self._eligible(person,grant)
        except AccountClosing:
            self.immutable(self.audit,key('key-provision-orphaned',digest([self.life.scope,person])),
                dict(v=1,scope=self.life.scope,person=person,version=body['version'],reason='account_closing',
                     destroyed=False,cleanup_required=True))
            self._phase(person,'closed')
            raise
        self._phase(person,'ready');self._eligible(person,grant)
        return dict(v=1,state='ready',request_sha256=digest(body),version=body['version'])
    def create_app(self):
        """Private service only. External Cloud Run IAM must allow only the web SA.

        Not attached to the app's public routing table or native package. This
        factory does not configure IAM or authenticate an arbitrary public caller.
        """
        app=Flask('lite-private-key-provisioner',static_folder=None)
        app.config['MAX_CONTENT_LENGTH']=4096
        @app.post(PATH)
        def prepare():
            try:
                if request.mimetype!='application/json':raise ValueError()
                body=loads_json_object_strict(request.get_data().decode())
                return jsonify(self.ensure(body))
            except (ValueError,AccountClosing):return jsonify(error='key_request_rejected'),409
            except StateUnavailable:return jsonify(error='key_preparation_pending',apple_code_exchanged=False),503
        @app.after_request
        def private(r):r.headers['Cache-Control']='no-store';return r
        return app
