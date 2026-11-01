"""Opt-in per-account service slots; admission and closure share one CAS root.

No store listing, global scan, media copy or implicit legacy backfill. Admission
indexes even a slot whose later operation creation never succeeds. A cleanup
worker can therefore seal it before a delayed create-only writer arrives.
"""
from lite_attest_state import CAS_ATTEMPTS, StateUnavailable
from lite_account_identity import IdentityRejected
from lite_subscription_executor import key
from lite_usage_ledger import _hex, digest


class AccountServiceInventory:
    MAX_OPERATIONS = 100000

    def __init__(self,lifecycle): self.life=lifecycle

    def member_key(self,person,operation):
        return key('account-service-member',digest([self.life.scope,person,operation]))

    def node(self,person,node_id):
        _hex(node_id)
        row=self.life.store.load(key('account-service-node',node_id))
        if row is None: raise StateUnavailable('service inventory node missing')
        n=row[1]
        try:
            if (type(n) is not dict or set(n)!={'v','scope','person','operation','previous','position'}
                    or type(n['v']) is not int or n['v']!=1 or digest(n)!=node_id
                    or n['scope']!=self.life.scope or n['person']!=person
                    or type(n['position']) is not int or not 1<=n['position']<=self.MAX_OPERATIONS
                    or (n['previous'] is None)!=(n['position']==1)): raise ValueError()
            _hex(n['operation'],32)
            if n['previous'] is not None: _hex(n['previous'])
        except (KeyError,ValueError,TypeError): raise StateUnavailable('service inventory corrupt') from None
        return n

    def member(self,person,operation):
        _hex(person);_hex(operation,32)
        member=self.life.audit.load(self.member_key(person,operation))
        if member is None: return None
        m=member[1]
        if type(m) is not dict or set(m)!={'v','node'} or type(m['v']) is not int or m['v']!=1:
            raise StateUnavailable('service membership corrupt')
        n=self.node(person,m['node'])
        if n['operation']!=operation: raise StateUnavailable('service member differs')
        return n

    def reserve(self,person,operation):
        _hex(person);_hex(operation,32)
        life=self.life
        for _ in range(CAS_ATTEMPTS):
            rev,root=life.require_active(person)
            n=self.member(person,operation)
            if n is not None:
                life.require_active(person)
                return n
            anchor_key=key('account-service-anchor',digest([life.scope,person,operation]))
            anchor=life.store.load(anchor_key)
            if anchor is None:
                life.store.compare_exchange(anchor_key,0,dict(v=1,person=person,operation=operation,
                    head=root['service_head'],count=root['service_count']))
                anchor=life.store.load(anchor_key)
            if anchor is None: raise StateUnavailable('service admission anchor unconfirmed')
            a=anchor[1]
            if (type(a) is not dict or set(a)!={'v','person','operation','head','count'}
                    or type(a['v']) is not int or a['v']!=1 or a['person']!=person or a['operation']!=operation
                    or type(a['count']) is not int or not 0<=a['count']<=root['service_count']
                    or (a['head'] is None)!=(a['count']==0)): raise StateUnavailable('service anchor corrupt')
            if a['head'] is not None: _hex(a['head'])
            cursor,count=root['service_head'],root['service_count'];found=None
            while count>a['count']:
                n=self.node(person,cursor)
                if n['position']!=count: raise StateUnavailable('service inventory gap')
                if n['operation']==operation: found=(cursor,n);break
                cursor,count=n['previous'],count-1
            if found is None:
                if cursor!=a['head'] or count!=a['count']: raise StateUnavailable('service anchor not in inventory')
                if root['service_count']==self.MAX_OPERATIONS: raise StateUnavailable('service catalog maintenance required')
                n=dict(v=1,scope=life.scope,person=person,operation=operation,
                       previous=root['service_head'],position=root['service_count']+1)
                node_id=digest(n)
                life.immutable(life.store,key('account-service-node',node_id),n)
                try: won=life.store.compare_exchange(life._key(person),rev,
                            dict(root,service_head=node_id,service_count=root['service_count']+1))
                except StateUnavailable: won=False
                if not won: continue
            else: node_id,n=found
            life.immutable(life.audit,self.member_key(person,operation),dict(v=1,node=node_id))
            life.require_active(person)
            return n
        raise StateUnavailable('service admission contention; retry original operation')

    def page(self,person,*,cursor=None,limit=50):
        if type(limit) is not int or not 1<=limit<=100: raise ValueError('bounded service page required')
        row=self.life._read(person)
        if row is None or row[1]['phase']!='closing': raise IdentityRejected('frozen service inventory required')
        root=row[1];cursor=root['service_head'] if cursor is None else cursor;items=[]
        while cursor is not None and len(items)<limit:
            n=self.node(person,cursor)
            if n['position']>root['service_count'] or (items and items[-1]['position']-1!=n['position']):
                raise StateUnavailable('service cursor outside frozen inventory')
            items.append(n);cursor=n['previous']
        return dict(items=items,next_cursor=cursor,total=root['service_count'],
                    inventory_sha256=root['closure']['inventory_sha256'])
