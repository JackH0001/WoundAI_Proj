# Lite closed-deployment probe: standalone execution parity

The authorized single Cloud Build `c9f13abd-e620-4e8e-804f-fa6875f284c4`
built the container successfully, then failed its external verification script:
`ModuleNotFoundError: No module named 'Backend'`. No image was published and no
runtime service, runtime identity or Secret was created. The source archive and
logs are retained. Three build grants were removed; a delayed readback confirmed
the build service account disabled. The failure-stop instruction remains in force.

The previous GitHub check used `python -c` in `/app`, while the deployment ran
`python /verification/probe.py`. Python's first import path differs between these
forms. A container working directory is not a substitute for an explicit import
root. This was a verification-tool packaging defect; it does not establish a
problem in the runtime authorization or data-isolation implementation.

`tools/probe_lite_review_container.py` now resolves an explicit application root
before importing the independent backend. The CI runs that exact external script
with isolated Python, a different working directory, network disabled, a read-only
filesystem and non-root image user. The script is **not copied into the runtime
image** and installs no deployed authentication bypass.

The probe creates real portal/reader factories with local settings and an empty
catalogue/role map. Metadata, Google service identity, the IAP test verification key
and the storage client are offline fixtures. A signed synthetic human assertion
must still receive application 401, and source/audit object access must not occur.
This checks startup wiring and fail-closed behavior, not real Google authentication.

Local regressions reproduce the old external-import failure, execute the fixed
probe from an unrelated working directory, and reject an invalid application root.
The newly built image still needs the single-build/deployed IAM and actual token
acceptance under a separately authorized, newly pinned source/configuration plan.

MMH, Demo, Lite App, original research data, index migration, cleanup scheduling and
store distribution remain outside this change. Prior application source tests remain
separate from these operational checks; no deployment success is claimed by CI.
