# Private worker startup: verify identity before storage

Scope: local implementation and synthetic acceptance only. Public App behavior,
live permissions and existing cloud records are unchanged.

Previously the factory supplied a configured identity to the SDK, compared
that configured name back to itself, and used an unmetered control session.
Startup now uses Cloud Run's [attached service account email and token
endpoints](https://docs.cloud.google.com/run/docs/container-contract#metadata-server).
It checks the observed email before requesting a token. Both metadata responses
must identify Google, must not redirect and must fit in 16 KiB each. Missing,
unknown or short-lived credentials end the attempt. The token is kept only in
memory, used within its wall/monotonic lifetime, never renewed by the SDK.
The intent is a short worker execution; a long-lived, self-renewing public
service must not reuse this credential policy.

The reviewed config hash now covers the new bootstrap_budget object:

```json
{"requests":96,"send_bytes":65536,"receive_bytes":8388608,"seconds":60}
```

Limits per start: 2–128 HTTP requests, 1 byte–1 MiB of request bodies,
32 KiB–8 MiB of reserved response capacity, and 1–120 seconds. These are
ceilings, not a measured recommended production setting. Each response's
capacity is reserved before sending, even for errors. Both identity reads,
bucket policy verification and the durable reservation journal use the same
meter. The control transport closes even when validation fails. Acknowledged
operational allocations use a separate durable budget; the transition does not
reload the token, reopen the control channel or refund either budget.

Synthetic tests use the production SDK with an intercepted HTTP adapter. Check
wrong identity (one metadata read, no token/GCS call), transport error or 401
(no hidden retries), four calls split 2 metadata + 2 GCS with the next denied,
and rejection of incomplete, oversized, redirected and expired responses.
Rerun: `python -m unittest -v test_lite_disposition_bootstrap test_lite_disposition_startup`.

**Limits of this evidence.** Bootstrap counters restart with the process. The
cloud runtime must independently bound starts/restarts and total wall time
before any real use. There is no claim of a hard kill, remote cancellation,
IAM acceptance, permanent data erasure or a measured cloud cost. HTTP bodies
exclude headers/TLS and storage fees. Never put a bearer, response body, real
account data or signing key into the review packet.
