"""Create a local, explicit-file Lite build context; never upload or deploy.

The manifest identifies bytes, including uncommitted candidate work. It is not
release approval. A deployment must separately bind reviewed source, IAM and
image digest. No recursive copy of runtime data or arbitrary models is allowed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

FLASK_FILES = (
    'Dockerfile', '.dockerignore', '.gcloudignore', 'requirements.txt', 'requirements.lock',
    'app.py', 'model_preprocessing.py', 'runtime_golden.py', 'image_canonical.py', 'runtime_secrets.py',
    'api_lite.py', 'api_flywheel.py', 'api_users.py', 'api_console.py', 'auth_users.py', 'institution_context.py',
    'store.py', 'audit_chain_contract.py', 'consent_staging.py',
    'lite_attest_assertion.py', 'lite_attest_budget.py', 'lite_attest_config.py',
    'lite_attest_enrollment.py', 'lite_attest_http.py', 'lite_attest_receipt.py',
    'lite_attest_registration.py', 'lite_attest_request.py', 'lite_attest_state.py',
    'lite_bucket_policy.py', 'lite_ledger_purge.py', 'lite_privacy_state.py',
    'lite_fenced_objects.py', 'lite_fenced_manifest.py', 'lite_fenced_store.py',
    'lite_raw_depth_contract.py', 'lite_raw_depth_store.py', 'lite_service_profile.py',
    'privacy/haarcascade_frontalface_default.xml', 'privacy/README.md',
    'certificates/Apple_App_Attestation_Root_CA.pem', 'certificates/AppleRootCA-G3.pem',
)
VENDOR_FILES = (
    'phase2/wound_classifier.py', 'phase1/clinical_rules.py', 'phase2/aruco_calibrate.py',
    'phase2/verify_area_sheet.py', 'phase2/color_calib.py', 'phase0/preprocessing.json',
)
# Exact artifacts previously verified for demo release 4005f39, also cross-checked
# against engineering/phase0/model_registry.json. These are integrity pins, not
# evidence of model quality, license review or approval for public release.
MODEL_SHA256 = {
    'student_fp16.onnx': '6fa9c340c458d2b1f4ca022c6516aaffbe686adcc84d3292c4bb5f43850ebacb',
    'a_unet.onnx': 'b05b3d17c5f8b9716cf36bac1027d8ab6728b3490fc3ce331673a57296286ae4',
    'unetpp.onnx': 'aa6c9619c68c7d267c93c3ca0838dabcb8fe8572fd313036fca311377bb8f2fc',
}


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def checked_source(base, relative):
    path = base / relative
    for part in (path, *path.parents):
        if part == base:
            break
        if part.is_symlink():
            raise ValueError('symlink source refused: ' + relative)
    if not path.is_file() or not path.resolve().is_relative_to(base):
        raise ValueError('missing or unsafe source: ' + relative)
    return path


def stage(root, models, destination):
    root, models = Path(root).resolve(), Path(models).resolve()
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination must not exist')
    if destination.resolve().is_relative_to(root) or destination.resolve().is_relative_to(models):
        raise ValueError('build context must be outside source and model directories')
    mapping = {name: (root, 'Backend/Flask/' + name) for name in FLASK_FILES}
    mapping.update({'vendor/' + Path(name).name: (root, 'engineering/' + name) for name in VENDOR_FILES})
    mapping.update({'models/' + name: (models, name) for name in MODEL_SHA256})
    # Validate all inputs before creating an output directory.
    sources = {name: checked_source(base, rel) for name, (base, rel) in mapping.items()}
    for name, expected in MODEL_SHA256.items():
        if digest(sources['models/' + name]) != expected:
            raise ValueError('model digest mismatch: ' + name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.lite-stage-', dir=destination.parent) as temporary:
        staged = Path(temporary) / 'context'; staged.mkdir()
        files = {}
        for name, source in sorted(sources.items()):
            target = staged / name; target.parent.mkdir(parents=True, exist_ok=True)
            before = digest(source)
            shutil.copyfile(source, target)
            after = digest(target)
            if before != after or digest(source) != before:
                raise ValueError('source changed during packaging: ' + name)
            if name.startswith('models/') and after != MODEL_SHA256[Path(name).name]:
                raise ValueError('model changed during packaging: ' + name)
            files[name] = {'sha256': after, 'bytes': target.stat().st_size}
        # Catch changes to early-copied files while later files were being copied.
        if any(digest(source) != files[name]['sha256'] for name, source in sources.items()):
            raise ValueError('source changed during packaging')
        def git(*args):
            result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, check=True)
            return result.stdout.strip()
        manifest = {'schema': 'lite.build-context/1', 'git_head': git('rev-parse', 'HEAD'),
                    'working_tree_dirty': bool(git('status', '--porcelain')), 'files': files,
                    'release_approved': False, 'scope': 'local build context only; not a container image or deployment'}
        (staged / 'build-context-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n')
        staged.rename(destination)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = stage(args.repo, args.models, args.output)
    print(json.dumps({'files': len(manifest['files']), 'working_tree_dirty': manifest['working_tree_dirty'],
                      'release_approved': False, 'output': str(args.output)}, indent=2))


if __name__ == '__main__':
    main()
