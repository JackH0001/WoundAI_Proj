#!/usr/bin/env python3
"""Signing keys the service refuses to invent.

app.py used to read its keys like this:

    SECRET_KEY=os.environ.get('FLASK_SECRET_KEY', <a constant in this repo>),
    JWT_SECRET_KEY=os.environ.get('JWT_SECRET_KEY', <a constant in this repo>),

A default that ships in a public repository is not a default. It is a published
signing key. Nothing refused to start without the real one, so the symptom of a
missing variable was a service that came up and looked healthy.

On 2026-09-20 an audit found that deploy_cloudrun.ps1 supplies JWT_SECRET_KEY
from Secret Manager but never supplies FLASK_SECRET_KEY, so the deployed service
was running on the published Flask constant. It was inert: this app never
imports flask.session, so Flask's SECRET_KEY signs nothing today. But "inert" is
a property of the current call sites, not of the configuration. One session[...]
or one flash() would have made it live, and nothing would have said so.

This module removes the constant. No code path here returns a value that is
committed to this repository.

The missing-variable behaviour differs by context on purpose:

  * serving   -> raise. A serving process with no key must not start. A random
                 key would be worse than a loud failure: tokens would stop
                 validating across instances and across restarts, which reads as
                 an intermittent login bug rather than a misconfiguration.
  * otherwise -> a fresh random key per process, and a warning. Development and
                 tests keep working, and the value is never the same twice, so
                 it cannot become something people rely on.
"""
import logging
import os
import secrets

_LOG = logging.getLogger(__name__)

# Cloud Run sets K_SERVICE for every revision. WOUNDAI_STORE=gcs means this
# process is wired to real cloud storage, which is the other way a process can
# be serving for real. Test processes never carry either: the isolation layer in
# tools/windows/run_python_tests.py strips WOUNDAI_STORE before any test runs.
SERVING_MARKERS = ("K_SERVICE",)
SERVING_STORE = "gcs"


def is_serving(env=None) -> bool:
    """True when this process is serving traffic rather than developing."""
    source = os.environ if env is None else env
    for marker in SERVING_MARKERS:
        if (source.get(marker) or "").strip():
            return True
    return (source.get("WOUNDAI_STORE") or "").strip().lower() == SERVING_STORE


def resolve_secret(name: str, purpose: str, env=None, warn=None) -> str:
    """Return the configured secret, or fail closed.

    Never returns a literal from this repository. `env` and `warn` are injectable
    so the behaviour can be tested without mutating the real process.
    """
    source = os.environ if env is None else env
    value = (source.get(name) or "").strip()
    if value:
        return value

    if is_serving(source):
        raise RuntimeError(
            "%s is not set and this process is serving. Refusing to start: a "
            "generated key would make sessions fail across instances, and a "
            "constant key would be readable by anyone with the source. Set %s "
            "(Cloud Run: --set-secrets %s=<secret-name>:latest). Purpose: %s"
            % (name, name, name, purpose))

    generated = secrets.token_urlsafe(48)
    (warn or _LOG.warning)(
        "%s is not set; generated a random key for this process only. Tokens "
        "will not survive a restart and will not be valid in another process. "
        "Purpose: %s", name, purpose)
    return generated
