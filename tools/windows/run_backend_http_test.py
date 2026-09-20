#!/usr/bin/env python3
"""Run synthetic HTTP checks against a private, disposable local Flask server.

The cloud-capable app is launched with a sanitized environment, an explicit
LocalStore, random secrets, a dynamically bound loopback port and a per-run
response marker. No operator cloud environment is passed to either child.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import time

from run_python_tests import sanitized_test_environment, source_snapshot


ROOT = Path(__file__).resolve().parents[2]

# Deliberately small: children need enough operating-system context to load the
# selected interpreter and native wheels, not the operator's login/session.
_PASSTHROUGH_ENV = {
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "OS",
    "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL",
    "PROCESSOR_REVISION", "NUMBER_OF_PROCESSORS", "VIRTUAL_ENV",
    "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LANG", "LC_ALL", "TZ",
}
_SERVER_ONLY_SECRETS = set(
    "ADMIN_PASSWORD JWT_SECRET_KEY FLASK_SECRET_KEY CARE_RECEIPT_SECRET".split()
)


def stop_child_tree(process, worker_pid=None):
    """Best-effort, non-throwing termination of a redirector and its worker."""
    targets = []
    if type(worker_pid) is int and worker_pid > 0 and worker_pid != os.getpid():
        targets.append(worker_pid)
    if process.poll() is None and process.pid not in targets:
        targets.append(process.pid)
    targets_stopped = True
    if os.name == "nt":
        # A Windows venv python.exe is a launcher with a second Python PID.
        # Terminating only Popen.pid can leave the actual server alive.
        for pid in targets:
            stopped = False
            try:
                completed = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=15, check=False)
                stopped = completed.returncode == 0
            except (OSError, subprocess.SubprocessError):
                # Cleanup must not hide the primary validation failure. The
                # direct Popen handle below remains a final local fallback.
                pass
            if not stopped and pid != process.pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    stopped = True
                except ProcessLookupError:
                    stopped = True
                except OSError:
                    pass
            targets_stopped = targets_stopped and stopped
    elif process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
    return process.poll() is not None and targets_stopped


def _atomic_json(path: Path, value: dict, token: str) -> None:
    pending = path.with_name(".%s.%s.pending" % (path.name, token))
    pending.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(pending, path)


def _start_run(out: Path, run_id: str):
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = out / ("run-%s-%s" % (stamp, run_id))
    run_dir.mkdir(parents=False, exist_ok=False)
    result = {
        "schema": "woundai.local-http-validation/2",
        "state": "running",
        "passed": False,
        "run_id": run_id,
        "run_directory": run_dir.name,
        "synthetic_data_only": True,
        "storage": "local",
        "cloud_storage_variables_inherited": False,
    }
    _atomic_json(run_dir / "http-summary.json", result, run_id)
    # Replacing this before any child starts prevents an older PASS record from
    # surviving a crash or timeout and masquerading as the current run.
    _atomic_json(out / "http-summary.json", result, run_id)
    return run_dir, result


def _redact(text: str, secret_values) -> str:
    for value in sorted((str(v) for v in secret_values if v), key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~-]+", "Bearer [REDACTED]", text)
    return text


def _secret_values(env) -> list[str]:
    values = [env.get(key, "") for key in
              (_SERVER_ONLY_SECRETS | {"WOUNDAI_HTTP_TEST_PASSWORD"})]
    try:
        care = json.loads(env.get("CARE_RECEIPT_SECRET", "{}"))
        values.extend(str(item.get("secret_b64", ""))
                      for item in care.get("keys", {}).values())
    except (AttributeError, TypeError, ValueError):
        pass
    return [str(value) for value in values if value]


def _log_evidence(path: Path, secret_values) -> dict:
    try:
        if not path.exists():
            path.write_text("", encoding="utf-8")
        clean = _redact(path.read_text(encoding="utf-8", errors="replace"), secret_values)
        path.write_text(clean, encoding="utf-8")
        return {"file": path.name, "available": True,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    except OSError:
        return {"file": path.name, "available": False, "error": "log evidence unavailable"}


def _record_cleanup(result: dict, runtime: Path | None) -> bool:
    removed = runtime is None or not runtime.exists()
    result["temporary_runtime_removed"] = removed
    if not removed:
        result["passed"] = False
        result.setdefault("error_type", "TemporaryCleanupError")
        result["cleanup_error"] = "private HTTP runtime was not removed"
    return removed


def _record_source_comparison(result: dict, before, after) -> bool:
    unchanged = before is not None and before == after
    result["source_snapshot_after"] = after
    result["source_unchanged"] = unchanged
    if not unchanged:
        result["passed"] = False
        result.setdefault("error_type", "SourceChangedError")
        result["source_error"] = "source snapshot changed during HTTP validation"
    return unchanged


def _communicate_client(process, timeout: int, worker_pid=None):
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return stdout or "", stderr or "", False
    except subprocess.TimeoutExpired:
        stop_child_tree(process, worker_pid=worker_pid)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except (OSError, subprocess.SubprocessError):
            stdout, stderr = "", ""
        return stdout or "", stderr or "", True


def build_environment(source, runtime: Path, run_id: str):
    sanitized = sanitized_test_environment(source)
    by_upper = {str(key).upper(): str(value) for key, value in sanitized.items()}
    env = {key: by_upper[key] for key in _PASSTHROUGH_ENV if key in by_upper}
    profile = runtime / "profile"
    roaming = profile / "AppData" / "Roaming"
    local = profile / "AppData" / "Local"
    config = runtime / "config"
    for directory in (profile, roaming, local, config, runtime / "tmp"):
        directory.mkdir(parents=True, exist_ok=True)
    admin_password = secrets.token_urlsafe(32)
    env.update({
        "WOUNDAI_STORE": "local",
        "WOUNDAI_REQUIRE_FUNCTIONAL_TESTS": "1",
        "WOUNDAI_RUNTIME_DIR": str(runtime),
        "WOUNDAI_FLYWHEEL_DIR": str(runtime / "flywheel"),
        "WOUNDAI_LOCAL_TEST_RUN": run_id,
        "ADMIN_PASSWORD": admin_password,
        "WOUNDAI_HTTP_TEST_PASSWORD": admin_password,
        "JWT_SECRET_KEY": secrets.token_urlsafe(48),
        "FLASK_SECRET_KEY": secrets.token_urlsafe(48),
        "CARE_RECEIPT_SECRET": json.dumps({"active_kid": "http-synthetic",
            "keys": {"http-synthetic": {"secret_b64": secrets.token_urlsafe(48)}}}),
        "PYTHONPATH": os.pathsep.join(str(p) for p in (
            ROOT / "Backend" / "Flask", ROOT / "engineering" / "phase0",
            ROOT / "engineering" / "phase1", ROOT / "engineering" / "phase2")),
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TEMP": str(runtime / "tmp"), "TMP": str(runtime / "tmp"),
        "TMPDIR": str(runtime / "tmp"),
        "HOME": str(profile), "USERPROFILE": str(profile),
        "APPDATA": str(roaming), "LOCALAPPDATA": str(local),
        "CLOUDSDK_CONFIG": str(config / "gcloud"),
        "XDG_CONFIG_HOME": str(config / "xdg"),
        "XDG_CACHE_HOME": str(runtime / "cache"),
        "AWS_CONFIG_FILE": str(config / "aws-config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(config / "aws-credentials"),
        "AZURE_CONFIG_DIR": str(config / "azure"),
        "KUBECONFIG": str(config / "kubeconfig"),
        "NETRC": str(config / "netrc"),
        "NO_PROXY": "127.0.0.1,::1", "no_proxy": "127.0.0.1,::1",
    })
    return env


def _server_environment(env):
    child = dict(env)
    child.pop("WOUNDAI_HTTP_TEST_PASSWORD", None)
    return child


def _client_environment(env):
    child = dict(env)
    for key in _SERVER_ONLY_SECRETS:
        child.pop(key, None)
    return child


def _client_command(url: str, run_id: str):
    # Authentication is delivered only in the private child environment. It
    # must never appear in argv, exception command fields or process listings.
    return [
        sys.executable,
        str(ROOT / "engineering/phase2/test_backend_http.py"),
        "--url", url, "--synthetic", "--test-run-id", run_id,
        "--user", "admin",
    ]


def serve(ready_file: Path) -> int:
    # This entry point is private to the launcher. Refuse a manual invocation
    # that could accidentally re-use an operator's cloud-backed environment.
    if (os.environ.get("WOUNDAI_STORE") != "local"
            or os.environ.get("WOUNDAI_REQUIRE_FUNCTIONAL_TESTS") != "1"):
        raise RuntimeError("HTTP test server requires explicit isolated LocalStore")
    run_id = os.environ.get("WOUNDAI_LOCAL_TEST_RUN", "")
    if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise RuntimeError("missing local HTTP test run identity")
    from app import app
    import store
    from werkzeug.serving import make_server

    if not isinstance(store.get_store(), store.LocalStore):
        raise RuntimeError("HTTP test server did not construct a LocalStore")

    @app.after_request
    def identify_test_instance(response):
        response.headers["X-WoundAI-Local-Test-Run"] = run_id
        return response

    server = make_server("127.0.0.1", 0, app, threaded=True)
    ready = {"pid": os.getpid(), "port": server.server_port, "run_id": run_id,
             "store": "local"}
    pending = ready_file.with_suffix(".pending")
    pending.write_text(json.dumps(ready), encoding="utf-8")
    pending.replace(ready_file)
    # A stop file permits graceful exit even when Windows' venv launcher PID
    # differs from the Python worker. No public shutdown HTTP route is added.
    server.timeout = 0.2
    try:
        while not ready_file.with_suffix(".stop").exists():
            server.handle_request()
    finally:
        server.server_close()
        stopped = {"pid": os.getpid(), "run_id": run_id}
        _atomic_json(ready_file.with_suffix(".stopped"), stopped, run_id)
    return 0


def run(out: Path, timeout: int) -> int:
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    run_id = secrets.token_hex(16)
    run_dir, result = _start_run(out, run_id)
    runtime = None
    env = None
    snapshot_before = None
    server_log_path = run_dir / "server.log"
    client_log_path = run_dir / "client.log"
    try:
        snapshot_before = source_snapshot(ROOT)
        result["source_snapshot_before"] = snapshot_before
        with tempfile.TemporaryDirectory(prefix="runtime-", dir=run_dir,
                                         ignore_cleanup_errors=True) as tmp:
            runtime = Path(tmp)
            env = build_environment(os.environ, runtime, run_id)
            ready_file = runtime / "ready.json"
            server = None
            server_worker_pid = None
            server_ready = False
            client_output = ""
            try:
                with server_log_path.open("w", encoding="utf-8") as server_log:
                    server = subprocess.Popen(
                        [sys.executable, str(Path(__file__).resolve()),
                         "--serve", str(ready_file)], cwd=runtime,
                        env=_server_environment(env), stdout=server_log,
                        stderr=subprocess.STDOUT,
                    )
                    deadline = time.monotonic() + min(timeout, 90)
                    while not ready_file.exists():
                        if server.poll() is not None:
                            raise RuntimeError(
                                "local server exited before readiness; see current server.log")
                        if time.monotonic() >= deadline:
                            raise TimeoutError("local server did not become ready")
                        time.sleep(0.1)
                    ready = json.loads(ready_file.read_text(encoding="utf-8"))
                    if (type(ready.get("pid")) is not int or ready.get("run_id") != run_id
                            or ready.get("store") != "local"
                            or type(ready.get("port")) is not int):
                        raise RuntimeError("local server readiness identity mismatch")
                    server_worker_pid = ready["pid"]
                    server_ready = True
                    url = "http://127.0.0.1:%d" % ready["port"]
                    command = _client_command(url, run_id)
                    client = subprocess.Popen(
                        command, cwd=runtime, env=_client_environment(env),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, encoding="utf-8", errors="replace",
                    )
                    stdout, stderr, timed_out = _communicate_client(client, timeout)
                    client_output = _redact(stdout + stderr, _secret_values(env))
                    client_log_path.write_text(client_output, encoding="utf-8")
                    print(client_output, end="")
                    if timed_out:
                        raise TimeoutError("local HTTP client timed out")
                    result.update({"passed": client.returncode == 0,
                                   "exit_code": client.returncode})
            except Exception as exc:
                result["error_type"] = type(exc).__name__
                print("HTTP validation failed (%s); see current run logs" %
                      type(exc).__name__)
                if isinstance(exc, (RuntimeError, TimeoutError)):
                    result["error_context"] = str(exc)
            finally:
                if not client_log_path.exists():
                    client_log_path.write_text(client_output, encoding="utf-8")
                if server is not None:
                    stop_requested = False
                    try:
                        ready_file.with_suffix(".stop").touch()
                        stop_requested = True
                    except OSError:
                        pass
                    if stop_requested:
                        try:
                            server.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            pass
                    stopped_ok = False
                    stopped_file = ready_file.with_suffix(".stopped")
                    if stopped_file.exists():
                        try:
                            stopped = json.loads(stopped_file.read_text(encoding="utf-8"))
                            stopped_ok = (stopped.get("run_id") == run_id
                                          and stopped.get("pid") == server_worker_pid)
                        except (OSError, ValueError):
                            pass
                    launcher_done = server.poll() is not None
                    if not stopped_ok or not launcher_done:
                        cleanup_done = stop_child_tree(
                            server, worker_pid=server_worker_pid if server_ready else None)
                    else:
                        cleanup_done = True
                    if (server_ready and not stopped_ok) or not cleanup_done:
                        result["passed"] = False
                        result.setdefault("error_type", "ServerCleanupError")
                        result["cleanup_error"] = "server did not prove complete shutdown"
    except Exception as exc:
        result["passed"] = False
        result["error_type"] = type(exc).__name__
        result["error_context"] = "private HTTP runtime could not be created or removed"
        print("HTTP validation failed (%s); see current run summary" % type(exc).__name__)
    _record_cleanup(result, runtime)
    try:
        snapshot_after = source_snapshot(ROOT)
        _record_source_comparison(result, snapshot_before, snapshot_after)
    except Exception:
        result["passed"] = False
        result["source_unchanged"] = False
        result.setdefault("error_type", "SourceEvidenceError")
        result["source_error"] = "source snapshot could not be established"
    secrets_to_redact = [] if env is None else _secret_values(env)
    result["artifacts"] = {
        "server_log": _log_evidence(server_log_path, secrets_to_redact),
        "client_log": _log_evidence(client_log_path, secrets_to_redact),
    }
    if not all(item.get("available") is True for item in result["artifacts"].values()):
        result["passed"] = False
        result.setdefault("error_type", "EvidenceWriteError")
        result["evidence_error"] = "one or more current-run logs are unavailable"
    result["seconds"] = round(time.monotonic() - started, 3)
    result["state"] = "passed" if result["passed"] else "failed"
    _atomic_json(run_dir / "http-summary.json", result, run_id)
    _atomic_json(out / "http-summary.json", result, run_id)
    return 0 if result["passed"] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--serve", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        return serve(args.serve)
    if args.out is None or args.timeout < 1:
        parser.error("--out and a positive --timeout are required")
    return run(args.out.resolve(), args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
