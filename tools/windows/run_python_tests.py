#!/usr/bin/env python3
"""Run every WoundAI engineering test file as an isolated process.

The repository contains script-style, unittest-style and pytest-compatible
tests.  Process isolation preserves their intended working-directory and
environment behavior and prevents module/global state leaking between files.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid


CI_TESTS = {
    "engineering/phase0/test_ssot_golden.py",
    "engineering/phase0/test_eval_harness.py",
    "engineering/phase1/test_phase1.py",
    "engineering/phase1/test_clinical_rules.py",
    "engineering/phase2/test_phase2.py",
    "engineering/phase2/test_phase2_pipeline.py",
    "engineering/phase2/test_wound_classifier.py",
    "engineering/phase2/test_calibration.py",
    "engineering/phase2/test_geometry.py",
    "engineering/phase2/test_measure.py",
    "engineering/phase2/test_app_fastapi.py",
    "engineering/phase2/test_annotation_workflow.py",
    "engineering/phase2/test_mask_refine.py",
    "engineering/phase2/test_aruco_calibrate.py",
    "engineering/phase2/test_api_service.py",
    "engineering/phase2/test_app.py",
}

# Requires a separately launched Flask server and writes a full flywheel
# lifecycle. Run-WindowsValidation.ps1 executes it in an isolated runtime.
INTEGRATION_TESTS = {"engineering/phase2/test_backend_http.py"}
_STRIPPED_ENVIRONMENT_KEYS = set("""
ADMIN_PASSWORD JWT_SECRET_KEY FLASK_SECRET_KEY CARE_RECEIPT_SECRET
WOUNDAI_HTTP_TEST_ADMIN_PASSWORD WOUNDAI_HTTP_TEST_PASSWORD WOUNDAI_API_TOKEN
WOUNDAI_PW LITE_IP_SALT GOOGLE_APPLICATION_CREDENTIALS GOOGLE_CLOUD_PROJECT
CLOUDSDK_CONFIG AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
AZURE_CLIENT_ID AZURE_CLIENT_SECRET AZURE_TENANT_ID HTTP_PROXY HTTPS_PROXY ALL_PROXY
""".split())


def atomic_json(path: Path, value: dict) -> None:
    """Replace a JSON record atomically so a prior PASS cannot survive a crash."""
    temporary = path.with_name(".%s.%s.tmp" % (path.name, uuid.uuid4().hex))
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def ensure_output_root_safe(repo: Path, out: Path) -> None:
    """Refuse repo-local evidence unless Git ignores the entire untracked root."""
    try:
        relative = out.relative_to(repo)
    except ValueError:
        return
    if relative == Path("."):
        raise RuntimeError("validation output root cannot be the repository root")
    git = ["git", "-c", "safe.directory=" + repo.as_posix(),
           "--no-optional-locks", "-C", str(repo)]
    tracked = subprocess.run(
        git + ["ls-files", "-z", "--", relative.as_posix()],
        check=True, capture_output=True,
    ).stdout
    if tracked:
        raise RuntimeError(
            "validation output root contains tracked repository paths: %s" % relative.as_posix())
    ignored = subprocess.run(
        git + ["check-ignore", "-q", "--no-index", "--", relative.as_posix()],
        check=False, capture_output=True,
    )
    if ignored.returncode != 0:
        raise RuntimeError(
            "validation output inside the repository must be wholly git-ignored: %s"
            % relative.as_posix())


def sanitized_test_environment(source) -> dict:
    """Copy an operator environment without cloud storage routing.

    Case-insensitive matching also handles dictionaries supplied by Windows
    harnesses. Preserve unrelated settings for each harness to override.
    """
    env = dict(source)
    for key in list(env):
        upper = key.upper()
        if upper == "WOUNDAI_STORE" or upper.startswith("WOUNDAI_GCS_") \
                or upper == "WOUNDAI_AUDIT_BUCKET" \
                or upper == "WOUNDAI_REQUIRE_FUNCTIONAL_TESTS" \
                or upper in _STRIPPED_ENVIRONMENT_KEYS:
            env.pop(key)
    env["WOUNDAI_REQUIRE_FUNCTIONAL_TESTS"] = "1"
    return env


def source_snapshot(repo: Path) -> dict:
    """Bind evidence to HEAD, every tracked diff and every untracked file."""
    git = ["git", "-c", "safe.directory=" + repo.as_posix(), "--no-optional-locks", "-C", str(repo)]
    def read(*args):
        return subprocess.run(git + list(args), check=True, capture_output=True).stdout
    tracked_diff = read("diff", "--binary", "HEAD", "--")
    untracked = sorted(filter(None, read(
        "ls-files", "--others", "--exclude-standard", "-z"
    ).decode("utf-8").split("\0")))
    hashes = {}
    for rel in untracked:
        digest = hashlib.sha256()
        with (repo / rel).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes[rel] = digest.hexdigest()
    return {
        "head": read("rev-parse", "HEAD").decode().strip(),
        "tracked_diff_sha256": hashlib.sha256(tracked_diff).hexdigest(),
        "tracked_diff_bytes": len(tracked_diff),
        "untracked_sha256": hashes,
    }


def stop_process_tree(process) -> None:
    """Stop the test and descendants after a timeout, including venv wrappers."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            killed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, check=False,
            )
            if killed.returncode != 0 and process.poll() is None:
                process.kill()
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        process.kill()
        process.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--quick", action="store_true", help="run the legacy CI subset only")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    repo = args.repo.resolve()
    out = args.out.resolve()
    ensure_output_root_safe(repo, out)
    out.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    run_name = "run-%s-%s" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), run_id)
    run_dir = out / run_name
    run_dir.mkdir(exist_ok=False)
    logs = run_dir / "python"
    logs.mkdir()
    latest_summary = out / "python-summary.json"
    atomic_json(latest_summary, {
        "schema": "woundai.python-validation/2",
        "state": "running",
        "run_id": run_id,
        "run_directory": run_name,
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })

    tests = sorted(
        p for p in repo.glob("engineering/**/test_*.py")
        if p.relative_to(repo).as_posix() not in INTEGRATION_TESTS
    )
    if args.quick:
        tests = [p for p in tests if p.relative_to(repo).as_posix() in CI_TESTS]

    env = sanitized_test_environment(os.environ)
    search = [
        repo / "engineering" / "phase0",
        repo / "engineering" / "phase1",
        repo / "engineering" / "phase2",
        repo / "Backend" / "Flask",
    ]
    env["PYTHONPATH"] = os.pathsep.join(map(str, search))
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    temp_root = run_dir / "temp"
    temp_root.mkdir()
    env["TEMP"] = str(temp_root)
    env["TMP"] = str(temp_root)

    results: list[dict[str, object]] = []
    snapshot_before = source_snapshot(repo)
    started = time.time()
    for index, test in enumerate(tests, 1):
        rel = test.relative_to(repo).as_posix()
        log_path = logs / (rel.replace("/", "__") + ".log")
        runtime = temp_root / ("%03d-%s" % (index, test.stem))
        runtime.mkdir(parents=True, exist_ok=False)
        test_env = dict(env)
        test_env["WOUNDAI_RUNTIME_DIR"] = str(runtime)
        test_env["WOUNDAI_FLYWHEEL_DIR"] = str(runtime / "flywheel")
        profile = runtime / "profile"
        profile.mkdir()
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            test_env[key] = str(profile)
        test_env["XDG_CONFIG_HOME"] = str(profile / "config")
        test_env["CLOUDSDK_CONFIG"] = str(profile / "gcloud")
        test_env["GOOGLE_APPLICATION_CREDENTIALS"] = str(profile / "no-google-credentials.json")
        test_env["AWS_SHARED_CREDENTIALS_FILE"] = str(profile / "no-aws-credentials")
        test_env["AZURE_CONFIG_DIR"] = str(profile / "azure")
        test_env["NO_PROXY"] = "127.0.0.1,::1"
        print(f"[{index:02d}/{len(tests):02d}] {rel}", flush=True)
        t0 = time.time()
        tree = ast.parse(test.read_text(encoding="utf-8-sig"))
        has_entrypoint = any(isinstance(n, ast.If) and "__name__" in ast.unparse(n.test)
                             for n in tree.body)
        pytest_only = not has_entrypoint and any(
            isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in tree.body)
        command = ([sys.executable, "-m", "pytest", str(test), "-q", "-p", "no:cacheprovider",
                    "--junitxml=" + str(log_path.with_suffix(".xml"))]
                   if pytest_only else [sys.executable, str(test)])
        try:
            proc = subprocess.Popen(
                command,
                cwd=repo,
                env=test_env,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=(os.name != "nt"),
            )
            output, _ = proc.communicate(timeout=args.timeout)
            code = proc.returncode
            status = "passed" if code == 0 else "failed"
        except subprocess.TimeoutExpired as exc:
            stop_process_tree(proc)
            output, _ = proc.communicate()
            output = (output or "") + f"\nTIMEOUT after {args.timeout}s\n"
            code = 124
            status = "timeout"
        except Exception as exc:  # preserve a complete evidence record
            output = "RUNNER ERROR: %s\n" % type(exc).__name__
            code = 125
            status = "failed"
        log_path.write_text(output, encoding="utf-8")
        result = {
            "test": rel,
            "runner": "pytest" if pytest_only else "script-entrypoint",
            "status": status,
            "exit_code": code,
            "seconds": round(time.time() - t0, 3),
            "log": str(log_path),
        }
        results.append(result)
        print(f"       {status.upper()} ({result['seconds']}s)", flush=True)

    cleanup_error = None
    try:
        shutil.rmtree(temp_root)
    except OSError as exc:
        cleanup_error = type(exc).__name__
    temporary_runtime_removed = not temp_root.exists()
    snapshot_after = source_snapshot(repo)
    stable = snapshot_before == snapshot_after
    tests_passed = all(r["status"] == "passed" for r in results)
    validation_passed = tests_passed and stable and temporary_runtime_removed
    summary = {
        "schema": "woundai.python-validation/2",
        "state": "passed" if validation_passed else "failed",
        "run_id": run_id,
        "run_directory": run_name,
        "repo": str(repo),
        "python": sys.version,
        "mode": "quick" if args.quick else "all",
        "total": len(results),
        "passed": sum(r["status"] == "passed" for r in results),
        "failed": sum(r["status"] != "passed" for r in results),
        "seconds": round(time.time() - started, 3),
        "results": results,
        "source_snapshot_before": snapshot_before,
        "source_snapshot_same_after": stable,
        "temporary_runtime_removed": temporary_runtime_removed,
        "cleanup_error": cleanup_error,
        "does_not_establish": ["iOS build", "Android UI/HTTP end-to-end", "deployment image golden bytes",
                               "cloud configuration or live service state", "clinical readiness"],
    }
    run_summary = run_dir / "python-summary.json"
    atomic_json(run_summary, summary)
    atomic_json(latest_summary, summary)
    print(
        f"Python summary: {summary['passed']}/{summary['total']} passed; "
        f"{summary['failed']} failed; {summary['seconds']}s"
    )
    if not stable:
        print("SOURCE CHANGED DURING VALIDATION: results cannot sign off the current tree")
    if not temporary_runtime_removed:
        print("TEMPORARY RUNTIME CLEANUP FAILED: results cannot be signed off")
    return 0 if validation_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
