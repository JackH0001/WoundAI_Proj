#!/usr/bin/env python3
"""Run every WoundAI engineering test file as an isolated process.

The repository contains script-style, unittest-style and pytest-compatible
tests.  Process isolation preserves their intended working-directory and
environment behavior and prevents module/global state leaking between files.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--quick", action="store_true", help="run the legacy CI subset only")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    repo = args.repo.resolve()
    out = args.out.resolve()
    logs = out / "python"
    logs.mkdir(parents=True, exist_ok=True)

    tests = sorted(
        p for p in repo.glob("engineering/**/test_*.py")
        if p.relative_to(repo).as_posix() not in INTEGRATION_TESTS
    )
    if args.quick:
        tests = [p for p in tests if p.relative_to(repo).as_posix() in CI_TESTS]

    env = os.environ.copy()
    search = [
        repo / "engineering" / "phase0",
        repo / "engineering" / "phase1",
        repo / "engineering" / "phase2",
        repo / "Backend" / "Flask",
    ]
    env["PYTHONPATH"] = os.pathsep.join(map(str, search))
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    temp_root = out / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = str(temp_root)
    env["TMP"] = str(temp_root)

    results: list[dict[str, object]] = []
    started = time.time()
    for index, test in enumerate(tests, 1):
        rel = test.relative_to(repo).as_posix()
        log_path = logs / (rel.replace("/", "__") + ".log")
        print(f"[{index:02d}/{len(tests):02d}] {rel}", flush=True)
        t0 = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, str(test)],
                cwd=repo,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=args.timeout,
            )
            output = proc.stdout
            code = proc.returncode
            status = "passed" if code == 0 else "failed"
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + f"\nTIMEOUT after {args.timeout}s\n"
            code = 124
            status = "timeout"
        log_path.write_text(output, encoding="utf-8")
        result = {
            "test": rel,
            "status": status,
            "exit_code": code,
            "seconds": round(time.time() - t0, 3),
            "log": str(log_path),
        }
        results.append(result)
        print(f"       {status.upper()} ({result['seconds']}s)", flush=True)

    summary = {
        "repo": str(repo),
        "python": sys.version,
        "mode": "quick" if args.quick else "all",
        "total": len(results),
        "passed": sum(r["status"] == "passed" for r in results),
        "failed": sum(r["status"] != "passed" for r in results),
        "seconds": round(time.time() - started, 3),
        "results": results,
    }
    (out / "python-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Python summary: {summary['passed']}/{summary['total']} passed; "
        f"{summary['failed']} failed; {summary['seconds']}s"
    )
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
