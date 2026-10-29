"""Opt-in account closure fence and append-only Apple grant inventory.

One CAS root per verified Apple identity serializes grant admission with closure.
Immutable linked nodes keep each object bounded; ordinary authorization reads
only the root and grant receipt, never scans other accounts. This is NOT a data
erasure executor or a claim that an account has been deleted.
"""
from lite_account_identity import IdentityRejected
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest, _hex, _subject


class AccountClosing(IdentityRejected): pass


class AccountLifecycle:
    immutable = staticmethod(SubscriptionRuntime.immutable)
    MAX_GRANTS = 10000

    def __init__(self, *, store, audit_store, binding, clock):
        if (type(binding) is not dict or set(binding) != {'namespace','issuer','audience','identity_key_sha256'}
                or not callable(clock)):
            raise ValueError('trusted identity binding required')
        _subject(binding['namespace']); _hex(binding['identity_key_sha256'])
        if binding['issuer'] != 'https://appleid.apple.com' or type(binding['audience']) is not str:
            raise ValueError('Apple authority required')
        if store is audit_store or (getattr(store,'path',None) is not None
                and getattr(store,'path',None) == getattr(audit_store,'path',None)):
            raise ValueError('lifecycle and audit stores must be separate')
        self.store, self.audit, self.binding, self.clock = store, audit_store, dict(binding), clock
        self.scope = digest(self.binding)
        self.immutable(store, key('account-lifecycle-config',binding['namespace']),
                       dict(v=1,binding=self.binding,max_grants=self.MAX_GRANTS))

    def now(self):
        n = self.clock()
        if type(n) is not int or n < 0: raise StateUnavailable('invalid lifecycle clock')
        return n

    def _key(self, person):
        _hex(person)
        return key('account-lifecycle',digest([self.scope,person]))

    def _read(self, person):
        row = self.store.load(self._key(person))
        if row is None: return None
        c = row[1]
        try:
            if (set(c) != {'v','scope','person','phase','head','count','closure'}
                    or type(c['v']) is not int or c['v'] != 1 or c['scope'] != self.scope
                    or c['person'] != person or c['phase'] not in ('active','closing')
                    or type(c['count']) is not int or not 0 <= c['count'] <= self.MAX_GRANTS
                    or (c['head'] is None) != (c['count'] == 0)):
                raise ValueError()
            if c['head'] is not None: _hex(c['head'])
            if c['phase'] == 'active':
                if c['closure'] is not None: raise ValueError()
            else:
                d = c['closure']
                if (type(d) is not dict or set(d) != {'operation','requester','requested_at','inventory_sha256'}
                        or type(d['requested_at']) is not int or not 0 <= d['requested_at'] <= self.now()
                        or d['inventory_sha256'] != digest([self.scope,person,c['head'],c['count']])):
                    raise ValueError()
                _hex(d['operation'],32); _subject(d['requester'])
        except (TypeError,ValueError,KeyError):
            raise StateUnavailable('account lifecycle corrupt; never reset') from None
        return row

    def initialize_new(self, person, *, existing_account):
        """Only trusted verified enrollment, before the first grant exchange.

        Existing identities without inventory need an explicit backfill review.
        Presence of only a current login is never evidence of full inventory.
        """
        if type(existing_account) is not bool: raise ValueError('explicit baseline required')
        row = self._read(person)
        if row is None:
            if existing_account: raise StateUnavailable('existing account requires verified grant inventory')
            initial = dict(v=1,scope=self.scope,person=person,phase='active',head=None,count=0,closure=None)
            try: self.store.compare_exchange(self._key(person),0,initial)
            except StateUnavailable: pass
        return self.require_active(person)

    def require_active(self, person):
        row = self._read(person)
        if row is None: raise StateUnavailable('account grant inventory required')
        if row[1]['phase'] != 'active': raise AccountClosing('account deletion pending')
        return row

    def _member_key(self, person, grant_id):
        return key('account-grant-member',digest([self.scope,person,grant_id]))

    def _node(self, person, node_id):
        _hex(node_id)
        row = self.store.load(key('account-grant-node',node_id))
        if row is None: raise StateUnavailable('account grant inventory node missing')
        n = row[1]
        try:
            if (set(n) != {'v','scope','person','grant_id','installation','operation','previous','position'}
                    or type(n['v']) is not int or n['v'] != 1 or digest(n) != node_id
                    or n['scope'] != self.scope or n['person'] != person
                    or type(n['position']) is not int or not 1 <= n['position'] <= self.MAX_GRANTS
                    or (n['previous'] is None) != (n['position'] == 1)):
                raise ValueError()
            _subject(n['installation']); _hex(n['operation'],32); _hex(n['grant_id'])
            if n['grant_id'] != digest([self.binding['namespace'],n['installation'],n['operation']]): raise ValueError()
            if n['previous'] is not None: _hex(n['previous'])
        except (TypeError,ValueError,KeyError):
            raise StateUnavailable('account grant inventory node corrupt') from None
        return n

    def member(self, person, grant_id):
        _hex(person); _hex(grant_id)
        row = self.audit.load(self._member_key(person,grant_id))
        if row is None: return None
        r = row[1]
        if (type(r) is not dict or set(r) != {'v','node'} or type(r['v']) is not int or r['v'] != 1):
            raise StateUnavailable('grant membership receipt corrupt')
        node = self._node(person,r['node'])
        if node['grant_id'] != grant_id: raise StateUnavailable('grant membership differs')
        return node

    def _find(self, person, root, grant_id, anchor):
        """Only enrollment/recovery scans this bounded single-account chain."""
        cursor, count = root['head'], root['count']
        while count > anchor['count']:
            n = self._node(person,cursor)
            if n['position'] != count: raise StateUnavailable('grant inventory sequence differs')
            if n['grant_id'] == grant_id: return cursor,n
            cursor,count = n['previous'],count-1
        if count != anchor['count'] or cursor != anchor['head']: raise StateUnavailable('grant inventory incomplete')
        return None

    def reserve_grant(self, person, installation, operation):
        _subject(installation); _hex(operation,32)
        grant_id = digest([self.binding['namespace'],installation,operation])
        for _ in range(CAS_ATTEMPTS):
            rev,c = self.require_active(person)
            found = self.member(person,grant_id)
            if found is not None:
                self.require_active(person)
                return found
            anchor_key = key('account-grant-anchor',digest([self.scope,person,grant_id]))
            anchor = self.store.load(anchor_key)
            if anchor is None:
                self.store.compare_exchange(anchor_key,0,dict(v=1,person=person,grant_id=grant_id,
                                                              head=c['head'],count=c['count']))
                anchor = self.store.load(anchor_key)
            if anchor is None: raise StateUnavailable('grant admission anchor unconfirmed')
            a = anchor[1]
            if (set(a) != {'v','person','grant_id','head','count'} or type(a['v']) is not int or a['v'] != 1
                    or a['person'] != person or a['grant_id'] != grant_id or type(a['count']) is not int
                    or not 0 <= a['count'] <= c['count'] or (a['head'] is None) != (a['count'] == 0)):
                raise StateUnavailable('grant admission anchor corrupt')
            if a['head'] is not None: _hex(a['head'])
            recovered = self._find(person,c,grant_id,a)
            if recovered:
                node_id,n = recovered
            else:
                if c['count'] == self.MAX_GRANTS: raise StateUnavailable('grant inventory capacity requires maintenance')
                n = dict(v=1,scope=self.scope,person=person,grant_id=grant_id,installation=installation,
                         operation=operation,previous=c['head'],position=c['count']+1)
                node_id = digest(n)
                self.immutable(self.store,key('account-grant-node',node_id),n)
                try: won = self.store.compare_exchange(self._key(person),rev,dict(c,head=node_id,count=c['count']+1))
                except StateUnavailable: won = False
                if not won: continue  # Resolve committed-but-unacknowledged root on retry.
            self.immutable(self.audit,self._member_key(person,grant_id),dict(v=1,node=node_id))
            self.require_active(person)
            return n
        raise StateUnavailable('grant inventory admission unconfirmed')

    def close(self, person, installation, operation):
        """Trusted authenticated coordinator only; no erasure or reopen here."""
        _subject(installation); _hex(operation,32)
        for _ in range(CAS_ATTEMPTS):
            row = self._read(person)
            if row is None: raise StateUnavailable('verified account inventory required')
            rev,c = row
            if c['phase'] == 'closing':
                if c['closure']['operation'] != operation or c['closure']['requester'] != installation:
                    raise AccountClosing('another account deletion request is already pending')
                return self.progress(person,installation,operation)
            d = dict(operation=operation,requester=installation,requested_at=self.now(),
                     inventory_sha256=digest([self.scope,person,c['head'],c['count']]))
            try: self.store.compare_exchange(self._key(person),rev,dict(c,phase='closing',closure=d))
            except StateUnavailable: pass
        raise StateUnavailable('account closure unconfirmed; retry saved request')

    def progress(self, person, installation, operation):
        """May be read after service login is blocked; caller still needs App Attest."""
        _subject(installation); _hex(operation,32)
        row = self._read(person)
        if row is None or row[1]['phase'] != 'closing': raise IdentityRejected('saved deletion request required')
        c = row[1]; d = c['closure']
        if d['requester'] != installation or d['operation'] != operation: raise IdentityRejected('deletion owner mismatch')
        receipt = dict(v=1,state='account_closing',**d,grant_count=c['count'],data_disposition='not_started',
                       apple_revocation='not_completed',account_deleted=False)
        self.immutable(self.audit,key('account-closure-intent',digest([self.scope,person])),receipt)
        return receipt

    def page(self, person, *, cursor=None, limit=50):
        """Trusted executor cursor; frozen only after closure, no public list API."""
        if type(limit) is not int or not 1 <= limit <= 100: raise ValueError('bounded inventory page required')
        row = self._read(person)
        if row is None or row[1]['phase'] != 'closing': raise IdentityRejected('frozen closure inventory required')
        c = row[1]; cursor = c['head'] if cursor is None else cursor
        items = []
        while cursor is not None and len(items) < limit:
            n = self._node(person,cursor)
            if n['position'] > c['count']: raise StateUnavailable('inventory cursor outside closure')
            if items and items[-1]['position']-1 != n['position']: raise StateUnavailable('inventory gap')
            items.append(n); cursor = n['previous']
        return dict(items=items,next_cursor=cursor,inventory_sha256=c['closure']['inventory_sha256'],total=c['count'])
