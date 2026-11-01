# Test the WoundAILite native service image on Linux

Use a clean commit, an isolated local Linux engine with linux/amd64 support and the approved three model weights. The production staging script checks the input model digests. The HTTP probe uses only generated images, local SQLite test stores, and synthetic identity, storage, KMS and clock providers. It exercises the packaged factory and the real inference engine over Gunicorn TCP.

## Reproduce from a checkout

Run in the repository root. Supply MODEL_DIR outside the checkout. This sample uses a temporary build context and publishes no container ports.

```sh
set -eu
source_sha=$(git rev-parse HEAD)
test -z "$(git status --porcelain)"
stage=$(mktemp -d)
python3 tools/stage_lite_native_candidate.py --repo . --models "$MODEL_DIR" --output "$stage/native"
chmod -R u=rwX,go= "$stage/native"
docker build --platform linux/amd64 -t "lite-native-qa:$source_sha" "$stage/native"
docker run --rm --platform linux/amd64 --read-only --network none --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  --memory 2g --cpus 2 -e QA_SOURCE="$source_sha" -e WOUNDAI_STORE=local \
  -e PYTHONPATH=/app:/qa \
  -v "$PWD/engineering/phase2:/qa:ro" \
  -v "$PWD/tools/probe_lite_native_image.py:/probe.py:ro" \
  "lite-native-qa:$source_sha" python -B /probe.py
```

On success the final JSON reports one model call and one unit used across the original request, result lookup and replay. It also checks the synthetic research state before and after the request and requires a nonempty polygon result for the seeded synthetic image. The image has no tests in its build context. The mounted fixture files supply the client and external doubles. The service cannot use the host network or credentials.

For the separate key and disposition workers use the two image jobs in `.github/workflows/lite-prelaunch.yml`. Each stages its own source allowlist, restricts host file modes before building and checks that missing deployment pins stop startup. The package checks run as the same non-root user as the service.

An actual TestFlight device, Apple Sandbox and a separately authorized candidate deployment are required to validate real attestation, Apple purchases and cloud permissions. Model output on a generated image measures pipeline operation only.
