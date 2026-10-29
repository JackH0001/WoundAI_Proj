"""Bounded, read-only account disposition inventory over actual authorities.

This is an internal planning tool, not a deletion authorization or a complete
data-subject export. References retain generations and hashes, never values or
credentials. Frozen grants identify candidate installations; research admission
and old namespaces remain independent and must not be inferred from Apple login.
"""
import re
import uuid
from lite_account_deletion import AccountDeletion
from lite_account_identity import IdentityRejected
from lite_attest_state import StateUnavailable
from lite_disposition_progress import aliases
from lite_reconsent import Reconsent, encoded, epoch
from lite_service_subscription_links import ServiceSubscriptionLinks, stable_subscription_key
from lite_subscription_migration import StableSubscriptionLinks
from lite_subscription_executor import key
from lite_subscription_runtime import SubscriptionRuntime
from lite_usage_ledger import digest, _hex
import hashlib


POLICY = dict(v=1, purpose='account-disposition-planning', max_grants=5,
    max_research_epochs_per_installation=16, deletion_authorized=False,
    retained_audit_policy='pending_basis_duration_and_access_review',
    local_records='preserve', subscription_billing='separate_Apple_cancellation')
PENDING = ['historical_versions_and_backups', 'metadata_writer_drain',
    'research_media_and_legacy_association', 'retention_basis_and_duration',
    'live_provider_acceptance']
_UNREAD = object()


class AccountDataInventory:
    """Only explicit trusted composition can inspect a closed account.

    No cloud discovery, bucket listing, decryption, Apple calls, cleanup,
    research withdrawal, quota changes, or HTTP registration is performed.
    A page is an observation, not a globally atomic or exhaustive snapshot.
    """
    immutable = staticmethod(SubscriptionRuntime.immutable)

    def __init__(self, *, deletion, audit_store, research=None):
        if not isinstance(deletion, AccountDeletion):
            raise ValueError('actual account deletion authority required')
        if research is not None and not isinstance(research, Reconsent):
            raise ValueError('actual independent research authority required')
        b = deletion.bridge
        links = b.registry.subscription_links
        if not isinstance(links, (StableSubscriptionLinks, ServiceSubscriptionLinks)):
            raise ValueError('actual subscription reader required')
        self.deletion, self.b, self.links, self.research = deletion, b, links, research
        self.audit = audit_store
        self.profile = digest(dict(identity=b.config,
            subscription_scope=links.scope if isinstance(links,StableSubscriptionLinks) else links.policy,
            research_bucket=None if research is None else research.privacy.objects.bucket.name))
        self.stores = dict(identity=b.store, identity_audit=b.audit,
            registration=b.registry.store, subscription=links.store,
            subscription_audit=links.audit, grants=b.grants.store,
            lifecycle=b.lifecycle.store, lifecycle_audit=b.lifecycle.audit)
        if research is not None: self.stores['research'] = research.store
        if any(aliases(audit_store, s) for s in (*self.stores.values(), deletion.store, deletion.audit)):
            raise ValueError('inventory audit must be independent of source data')

    def _observe(self, refs, store, k, row=_UNREAD):
        # Always bind the read to the exact source generation, including absence.
        if row is _UNREAD: row = self.stores[store].load(k)
        ref = dict(store=store, key=k.hex(), generation=0 if row is None else row[0],
                   sha256=None if row is None else digest(row[1]))
        if type(ref['generation']) is not int or ref['generation'] < 0:
            raise StateUnavailable('invalid source generation')
        refs.append(ref)
        return None if row is None else row[1]

    def _plan(self, person):
        # AccountDeletion._plan calls lifecycle.progress, which can repair an
        # absent closure audit. This observer must never repair source data.
        life = self.b.lifecycle
        row = life._read(person)
        if row is None or row[1]['phase'] != 'closing':
            raise IdentityRejected('saved account closure required')
        c = row[1]; d = c['closure']
        expected = dict(v=1,state='account_closing',**d,grant_count=c['count'],
            data_disposition='not_started',apple_revocation='not_completed',account_deleted=False)
        proof = life.audit.load(key('account-closure-intent',digest([life.scope,person])))
        if proof is None or proof[1] != expected:
            raise StateUnavailable('original closure receipt required; inventory cannot repair it')
        return dict(v=1,scope=self.deletion.scope,person=person,operation=d['operation'],
            requester=d['requester'],requested_at=d['requested_at'],
            inventory_sha256=d['inventory_sha256'],head=c['head'],total=c['count'])

    def _account(self, person, refs):
        a = self._observe(refs, 'identity', key('human-account', person))
        if (type(a) is not dict or set(a) != {'v','identity_sha256','subject','token'}
                or type(a['v']) is not int or a['v'] != 1 or a['identity_sha256'] != person
                or a['subject'] != 'apple-'+person):
            raise StateUnavailable('account identity missing or corrupt')
        try:
            if str(uuid.UUID(a['token'])) != a['token']: raise ValueError()
        except (TypeError,ValueError,AttributeError):
            raise StateUnavailable('account token invalid') from None
        return a

    def _subscription(self, a, refs):
        s, t = a['subject'], a['token']
        if isinstance(self.links, StableSubscriptionLinks):
            r = self.links._read(s, t, require_policy=False)
            self._observe(refs, 'subscription', self.links.key(s), r)
            self._observe(refs, 'subscription', stable_subscription_key(self.links.policy, s, 'guard'))
            if r is not None:
                for kind in ('intent','complete'):
                    self._observe(refs, 'subscription_audit', self.links.audit_key(kind, r[1]['plan']))
            legacy = [ServiceSubscriptionLinks(store=self.links.store, audit_store=self.links.audit,
                authority=self.links.catalog[p], clock=self.links.clock) for p in self.links.legacy]
        else:
            r = None; legacy = [self.links]
        # The explicitly configured legacy policy inventory is bounded to eight.
        for reader in legacy:
            raw = reader.store.load(reader.key(s))
            link = reader._read(s, t)
            if (None if raw is None else raw[1]) != link:
                raise StateUnavailable('legacy subscription changed during observation')
            self._observe(refs, 'subscription', reader.key(s), raw)
            if link is not None:
                for kind in ('intent','complete'):
                    self._observe(refs, 'subscription_audit', reader.audit_key(kind, link['intent']))
        return dict(format='stable' if isinstance(self.links, StableSubscriptionLinks) else 'legacy',
            stable_present=r is not None, legacy_policies_inspected=len(legacy),
            disposition='retention_review_required', billing_cancelled=False)

    def _research(self, installation, refs):
        if self.research is None:
            return dict(state='authority_not_configured', complete=False, epochs=0)
        if re.fullmatch(r'[0-9a-f]{32}', installation) is None:
            return dict(state='installation_not_research_owner_format', complete=False, epochs=0)
        r = self.research
        raw = r.store.load(r.key(installation))
        version, current, event = r.load(installation)
        if version != (0 if raw is None else raw[0]):
            raise StateUnavailable('research epoch changed during observation')
        self._observe(refs, 'research', r.key(installation), raw)
        count = 0; seen = set(); first = current
        while True:
            if current in seen: raise StateUnavailable('research consent chain cycles')
            seen.add(current)
            manifest = r.privacy.scoped(current).manifest
            generation, fence = manifest._load(installation)
            self._observe(refs, 'research', manifest._key(installation),
                          None if generation == 0 else (generation, fence))
            count += 1
            if current == 'legacy': break
            record = r.store.load(r.key(installation, current))
            if record is None or event != record[1]:
                raise StateUnavailable('research consent chain missing')
            self._observe(refs, 'research', r.key(installation, current), record)
            if (event.get('action') != 'reconsent_future_only' or event.get('owner') != installation
                    or event.get('new_epoch') != current):
                raise StateUnavailable('research consent chain identity differs')
            previous = epoch(event.get('previous_epoch'))
            if previous == current: raise StateUnavailable('research consent self-reference')
            if previous == 'legacy':
                if event.get('previous_audit_sha256') is not None:
                    raise StateUnavailable('legacy consent chain anchor differs')
                event = None
            else:
                prior = r.store.load(r.key(installation, previous))
                if prior is None or hashlib.sha256(encoded(prior[1])).hexdigest() != event.get('previous_audit_sha256'):
                    raise StateUnavailable('research historical consent proof missing')
                event = prior[1]
            if count >= POLICY['max_research_epochs_per_installation']:
                return dict(state='epoch_limit_requires_followup', complete=False, epochs=count)
            current = previous
        return dict(state='linked_epoch_references_observed', complete=False,
            epochs=count, current_epoch_sha256=digest(first),
            media_inventory_complete=False, research_withdrawn_by_inventory=False)

    def _grant(self, person, node, account, refs):
        installation, op = node['installation'], node['operation']
        reg_row = self.b.registry.store.load(self.b.registry.key(installation))
        self._observe(refs, 'registration', self.b.registry.key(installation), reg_row)
        if reg_row is not None:
            reg = self.b.registry.registration(installation)
            if reg['subject'] != account['subject'] or reg['token'] != account['token']:
                raise StateUnavailable('installation points to a different account')
        session_row = self.b._read(installation)
        self._observe(refs, 'identity', self.b._key(installation), session_row)
        if session_row is not None and session_row[1]['account'] is not None:
            if session_row[1]['account'] != account:
                raise StateUnavailable('current session belongs to another account')
        # Include every historical login's family, not only the current session.
        family_key = key('human-session-family', digest([self.b.namespace, installation, op]))
        family = self._observe(refs, 'identity', family_key)
        if family is not None:
            expected = {'v','operation','account_sha256','renewal_generation','renewed_at',
                        'expires_at','family_expires_at','renewal_operation'}
            if (type(family) is not dict or set(family) != expected
                    or type(family['v']) is not int or family['v'] != 1
                    or family['operation'] != op or family['account_sha256'] != digest(account)
                    or any(type(family[n]) is not int for n in
                           ('renewal_generation','renewed_at','expires_at','family_expires_at'))
                    or not 0 <= family['renewal_generation'] <= 10000
                    or not 0 <= family['renewed_at'] <= self.b.now()
                    or not family['renewed_at'] < family['expires_at'] <= min(
                        family['family_expires_at'], family['renewed_at']+1200)
                    or (family['renewal_generation']==0) != (family['renewal_operation'] is None)):
                raise StateUnavailable('historical session family malformed or owner differs')
            if family['renewal_operation'] is not None: _hex(family['renewal_operation'],32)
        grant_row = self.b.grants._row(node['grant_id'])
        self._observe(refs, 'grants', key('apple-grant', node['grant_id']), grant_row)
        if grant_row is not None and grant_row[1]['context']['identity_sha256'] != person:
            raise StateUnavailable('grant belongs to another account')
        self._observe(refs, 'identity_audit', self.b._authorization_key(installation, op))
        self._observe(refs, 'identity_audit', key('human-verified', digest([self.b.namespace, installation, op])))
        return dict(grant_id=node['grant_id'], position=node['position'],
            installation_sha256=digest(installation), registration_present=reg_row is not None,
            grant_present=grant_row is not None, research=self._research(installation, refs))

    def inspect(self, person, *, cursor=None, limit=5):
        """A bounded internal page; a caller cursor never proves whole-account coverage."""
        if type(limit) is not int or not 1 <= limit <= POLICY['max_grants']:
            raise ValueError('inventory page must contain 1 to 5 grants')
        plan = self._plan(person)
        page = self.b.lifecycle.page(person, cursor=cursor, limit=limit)
        refs = []; account = self._account(person, refs)
        subscription = self._subscription(account, refs)
        grants = [self._grant(person, n, account, refs) for n in page['items']]
        unique = {}
        for ref in refs:
            k = (ref['store'], ref['key'])
            if k in unique and unique[k] != ref:
                raise StateUnavailable('source changed during inventory observation')
            unique[k] = ref
        refs = sorted(unique.values(), key=lambda x:(x['store'],x['key']))
        self._check_refs(refs)
        # Security-state objects have a 16 KiB bound. Keep source references in
        # content-addressed chunks, not one arbitrarily large audit document.
        chunks = []
        for start in range(0,len(refs),32):
            chunk = dict(v=1, scope=plan['scope'], references=refs[start:start+32])
            h = digest(chunk)
            self.immutable(self.audit, key('account-data-inventory-refs',h), chunk)
            chunks.append(h)
        result = dict(v=1, kind='account-data-inventory-page', scope=plan['scope'],
            closure_sha256=digest(plan), policy_sha256=digest(POLICY), profile_sha256=self.profile,
            total_grants=plan['total'],
            cursor=cursor, next_cursor=page['next_cursor'], grants=grants,
            reference_chunks=chunks, reference_count=len(refs),
            subscription=subscription, pending=PENDING.copy(), observed_at=self.b.now(),
            whole_account_inventory_complete=False, account_deleted=False, cleanup_authorized=False)
        self._check_refs(refs)
        if self._plan(person) != plan:
            raise StateUnavailable('closure inventory changed')
        identity = digest(result)
        self.immutable(self.audit, key('account-data-inventory-page', identity), result)
        return dict(report_sha256=identity, **result)

    def _check_refs(self, refs):
        for ref in refs:
            row = self.stores[ref['store']].load(bytes.fromhex(ref['key']))
            observed = (0, None) if row is None else (row[0], digest(row[1]))
            if observed != (ref['generation'], ref['sha256']):
                raise StateUnavailable('inventory source changed; inspect a new page')

    def verify_unchanged(self, report_sha256):
        """Read-back proof only, never permission to delete a referenced object."""
        refs = self.references(report_sha256)
        self._check_refs(refs)
        return dict(report_sha256=report_sha256, sources_unchanged=True, cleanup_authorized=False)

    def references(self, report_sha256):
        _hex(report_sha256)
        row = self.audit.load(key('account-data-inventory-page', report_sha256))
        if (row is None or digest(row[1]) != report_sha256 or row[1].get('policy_sha256') != digest(POLICY)
                or row[1].get('scope') != self.deletion.scope or row[1].get('profile_sha256') != self.profile):
            raise StateUnavailable('original inventory observation required')
        refs = []
        for h in row[1]['reference_chunks']:
            part = self.audit.load(key('account-data-inventory-refs',h))
            if part is None or digest(part[1]) != h or part[1].get('scope') != self.deletion.scope:
                raise StateUnavailable('inventory reference chunk missing or changed')
            refs.extend(part[1]['references'])
        if len(refs) != row[1]['reference_count']:
            raise StateUnavailable('inventory reference count differs')
        return refs
