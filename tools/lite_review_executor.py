#!/usr/bin/env python3
"""B1 bounded transaction controller. No network transport or credential loading.

Only a provider-specific adapter may implement the typed operations below. This
module is deliberately NOT a cloud deployment CLI: the live adapter, its request
manifest and explicit authorization must be separately reviewed before use.
"""
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Protocol

PROJECT = 'woundai-jackh001'
REGION = 'asia-east1'
IMAGE = 'sha256:23a38262ac509da954bc3b313ab8ffb34fade9fb39770d6c2ad7dc91b7f80d07'
APP = '065c3289819f905cd7581680a42e3c686678c23a'
STEPS = ('create_audit_bucket', 'create_audit_role', 'audit_writer', 'audit_readback',
         'catalogue_version', 'reader_version', 'portal_version', 'reader_revision',
         'portal_revision', 'oauth_binding', 'iap_enable', 'iap_agent', 'owner_grant')
CLOSE = ('revoke_owner', 'restore_portal_traffic', 'restore_reader_traffic',
         'revoke_iap_agent', 'restore_iap_settings', 'restore_iap_enabled',
         'revoke_audit_writer', 'revoke_audit_readback')


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sha(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


class Stop(RuntimeError):
    pass


def need(value, why):
    if not value:
        raise Stop(why)


class Adapter(Protocol):
    # Adapter must implement exact request/resource allowlists and bounded calls.
    # Return sanitized booleans/receipts; do not return tokens or raw policies.
    def preflight(self, plan): ...
    def apply(self, step): ...
    def observe(self, step): ...
    def close(self, step): ...
    def verify_closed(self): ...
    def verify_owner(self): ...


@dataclass(frozen=True)
class Limits:
    forward_calls: int = 32
    closure_calls: int = 20
    seconds: int = 1800
    page_views: int = 5


class Journal:
    """Durable append-only local journal; a crash cannot restart forward work."""
    def __init__(self, path, plan_hash, create=False):
        self.path = Path(path)
        self.plan_hash = plan_hash
        self.events = []
        self.lock = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            os.close(self.lock)
            raise Stop('another operator owns this journal') from None
        try:
            if create:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
                parent = os.open(self.path.parent, os.O_RDONLY)
                try: os.fsync(parent)
                finally: os.close(parent)
                self.append('begin')
            else:
                need(not self.path.is_symlink(), 'journal symlink rejected')
                need(self.path.stat().st_mode & 0o077 == 0, 'journal must be private')
                raw = self.path.read_bytes()
                need(raw.endswith(b'\n'), 'truncated journal: manual containment required')
                prev = '0' * 64
                for line in raw.splitlines():
                    row = json.loads(line)
                    expected = row.pop('digest')
                    need(row.get('previous') == prev and row.get('plan') == plan_hash and
                         row.get('sequence') == len(self.events) and sha(row) == expected,
                         'journal integrity failure: manual containment required')
                    row['digest'] = expected
                    self.events.append(row)
                    prev = expected
                need(self.events and self.events[0]['event'] == 'begin', 'invalid journal start')
        except Exception:
            os.close(self.lock)
            raise

    def append(self, event, step=None, result=None):
        # No arbitrary exception text, response bodies, policy or secrets persisted.
        need(event in {'begin', 'intent', 'observed', 'stop', 'closing', 'closed_step',
                       'closure_failed', 'closed', 'owner_verified', 'ready'}, 'invalid event')
        need(step is None or step in STEPS + CLOSE + ('preflight', 'verify_closed', 'owner'),
             'unknown operation')
        need(result is None or result in {'confirmed', 'absent', 'unknown', 'failed', 'not_needed'},
             'unsanitized result')
        row = dict(sequence=len(self.events), previous=self.events[-1]['digest'] if self.events else '0'*64,
                   plan=self.plan_hash, event=event, step=step, result=result)
        row['digest'] = sha(row)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(row) + b'\n'); stream.flush(); os.fsync(stream.fileno())
        self.events.append(row)


def validate_plan(plan, authorization, now, recovery=False):
    need(set(plan) == {'schema', 'source', 'ci_head', 'app', 'image', 'project', 'region',
                      'phase', 'catalogue_records', 'source_grants', 'owner_hash', 'expires',
                      'baseline_hash', 'oauth_receipt_hash', 'steps'}, 'unexpected plan fields')
    need(plan['schema'] == 'lite-b1-controller/1' and plan['phase'] == 'B1_empty_owner_login',
         'B2 C D prohibited')
    need(plan['project'] == PROJECT and plan['region'] == REGION and
         plan['app'] == APP and plan['image'] == IMAGE, 'scope or image drift')
    need(plan['source'] == plan['ci_head'] and len(plan['source']) == 40 and
         all(c in '0123456789abcdef' for c in plan['source']), 'source CI mismatch')
    need(plan['steps'] == list(STEPS) and plan['catalogue_records'] == [] and plan['source_grants'] == [],
         'source data capability prohibited')
    for key in ('owner_hash', 'baseline_hash', 'oauth_receipt_hash'):
        need(isinstance(plan[key], str) and len(plan[key]) == 64 and
             all(c in '0123456789abcdef' for c in plan[key]), 'missing verified evidence hash')
    need(type(plan['expires']) is int and (recovery or now < plan['expires'] <= now + 86400),
         'expired or unbounded plan')
    need(authorization == sha(plan), 'explicit exact-plan authorization required')


class Executor:
    def __init__(self, plan, authorization, adapter, journal, clock=time.monotonic,
                 wall=time.time, limits=Limits(), resume=False):
        validate_plan(plan, authorization, int(wall()), recovery=resume)
        need(limits == Limits(), 'limits must not be widened')
        self.plan = plan; self.adapter = adapter; self.clock = clock; self.wall = wall
        self.limits = limits; self.started = clock(); self.forward = 0; self.closure = 0
        self.views = 0; self.ready = False; self.resume = resume
        self.journal = Journal(journal, sha(plan), create=not resume)

    def call(self, name, *args, closing=False):
        if closing:
            need(self.closure < self.limits.closure_calls, 'closure budget exhausted; manual containment required')
            self.closure += 1
        else:
            need(self.forward < self.limits.forward_calls, 'forward budget exhausted')
            need(self.clock() - self.started < self.limits.seconds and self.wall() < self.plan['expires'],
                 'acceptance deadline exceeded')
            self.forward += 1
        return getattr(self.adapter, name)(*args)

    def start(self):
        need(not self.resume and len(self.journal.events) == 1, 'single attempt; restart permits containment only')
        try:
            need(self.call('preflight', self.plan) is True, 'preflight rejected')
            for step in STEPS:
                self.journal.append('intent', step)
                try:
                    self.call('apply', step)
                except Exception:
                    # A timed-out write might already exist. Never repeat it.
                    self.journal.append('stop', step, 'unknown')
                    try:
                        state = self.call('observe', step)
                        self.journal.append('observed', step,
                            state if state in ('confirmed', 'absent') else 'unknown')
                    except Exception:
                        self.journal.append('observed', step, 'unknown')
                    raise Stop('write outcome uncertain; forward execution stopped')
                need(self.call('observe', step) == 'confirmed', 'readback did not confirm exact desired state')
                self.journal.append('observed', step, 'confirmed')
            self.ready = True
            self.journal.append('ready')
            return 'ready_for_owner'
        except Exception:
            self.journal.append('stop', result='failed')
            self.contain()
            raise Stop('B1 stopped; inspect sanitized journal and containment result') from None

    def owner_view(self):
        try:
            need(self.ready and not self.resume, 'not ready for owner')
            need(self.views < self.limits.page_views, 'owner probe budget exceeded')
            self.views += 1
            # Actual signed assertion, same owner hash, empty list, successful audit,
            # zero source reads and denied source permissions are adapter obligations.
            need(self.call('verify_owner') is True, 'owner receipt not verified')
            self.journal.append('owner_verified', 'owner', 'confirmed')
        except Exception:
            self.journal.append('stop', result='failed'); self.contain()
            raise Stop('owner verification failed; entry contained') from None

    def contain(self):
        # No mutations if preflight failed before any forward intent.
        self.ready = False
        intended = {e['step'] for e in self.journal.events if e['event'] == 'intent'}
        self.journal.append('closing')
        ok = True
        if intended:
            for step in CLOSE:
                try:
                    result = self.call('close', step, closing=True)
                    need(result in ('confirmed', 'not_needed'), 'closure not confirmed')
                    self.journal.append('closed_step', step, result)
                except Exception:
                    # One failed rollback must not prevent the remaining closures.
                    self.journal.append('closure_failed', step, 'failed'); ok = False
            try:
                need(self.call('verify_closed', closing=True) is True, 'effective closure not proved')
            except Exception:
                self.journal.append('closure_failed', 'verify_closed', 'failed'); ok = False
        if ok:
            self.journal.append('closed', result='confirmed')
        return ok


def normalized_member(value):
    # Only Google user email ASCII case is normalized, never service subjects/conditions.
    if value.startswith('user:') and value[5:].isascii():
        return 'user:' + value[5:].lower()
    return value


def remove_owned_grant(current, baseline, role, member, condition):
    """Pure etag-preserving IAM delta; never restore an entire old policy.

    Caller must perform CAS with returned etag and fresh readback; conflicts
    require another read/limited recomputation. Missing etag is not safe.
    """
    need(current.get('etag'), 'fresh IAM etag required')
    def match(binding):
        return binding.get('role') == role and binding.get('condition') == condition
    need(not any(match(b) and normalized_member(member) in
        [normalized_member(x) for x in b.get('members', [])] for b in baseline.get('bindings', [])),
        'grant existed before attempt; refuse removal')
    result = json.loads(json.dumps(current))
    for binding in result.get('bindings', []):
        if match(binding):
            binding['members'] = [x for x in binding.get('members', [])
                                  if normalized_member(x) != normalized_member(member)]
    result['bindings'] = [b for b in result.get('bindings', []) if b.get('members')]
    return result
