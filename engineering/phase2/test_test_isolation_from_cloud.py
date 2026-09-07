# -*- coding: utf-8 -*-
"""測試行程永遠不得觸及正式雲端儲存。

2026-09-06 的事故:操作者的 shell 裡殘留著閘門核對用的 `WOUNDAI_STORE=gcs`、
`WOUNDAI_GCS_BUCKET`、`WOUNDAI_AUDIT_BUCKET`。`run_python_tests.py` 以
`os.environ.copy()` 原封繼承,而 12 支沒有自行清除該變數的測試就打到了真的
Cloud Storage——125 筆測試稽核紀錄寫進鎖定七年的稽核桶,**永遠無法刪除**。

當時三層防護一層都不存在:

  1. runner 不清理環境
  2. 後端不知道自己在測試行程裡
  3. 靠每支測試自己記得 `os.environ.pop`——12 支忘了

本檔鎖住第 1 與第 2 層。第 3 層不再是防線,只是冗餘。

    python engineering/phase2/test_test_isolation_from_cloud.py
"""
import os
import re
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools" / "windows" / "run_python_tests.py"
STORE = ROOT / "Backend" / "Flask" / "store.py"

CLOUD_VARS = ("WOUNDAI_STORE", "WOUNDAI_GCS_BUCKET", "WOUNDAI_GCS_PREFIX",
              "WOUNDAI_AUDIT_BUCKET")


def text(path):
    return path.read_text(encoding="utf-8-sig")


class TestIsolationFromCloudStorage(unittest.TestCase):

    # ── 第 1 層:runner 在 spawn 前剝除 ──────────────────────────────
    def test_runner_strips_cloud_store_variables(self):
        src = text(RUNNER)
        self.assertIn("env = os.environ.copy()", src)
        strip = re.search(
            r"for leaked in \[k for k in env(.{0,300}?)\]:\s*\n\s*env\.pop\(leaked\)",
            src, re.S)
        self.assertIsNotNone(strip, "runner must remove the cloud store variables from env")
        clause = strip.group(1)
        for var in ("WOUNDAI_STORE", "WOUNDAI_GCS_", "WOUNDAI_AUDIT_BUCKET"):
            self.assertIn(var, clause, "%s is not stripped" % var)

    def test_runner_strips_before_it_spawns_anything(self):
        src = text(RUNNER)
        # 剝除必須發生在把 env 交給 subprocess 之前,否則等於沒做。
        self.assertLess(src.index("env.pop(leaked)"), src.index("env=env"))

    def test_runner_still_sets_the_test_marker(self):
        # 第 2 層完全依賴這個標記。拿掉它,後端就看不見自己在測試行程裡。
        self.assertIn('env["WOUNDAI_REQUIRE_FUNCTIONAL_TESTS"] = "1"', text(RUNNER))

    # ── 第 2 層:後端拒絕在測試行程裡使用 GCS ────────────────────────
    def test_store_refuses_gcs_inside_a_test_process(self):
        src = text(STORE)
        self.assertIn(
            'if kind == "gcs" and os.environ.get("WOUNDAI_REQUIRE_FUNCTIONAL_TESTS") == "1":',
            src)
        # 必須是拋出,不能是靜默退回 local——靜默退回會給出假的綠燈。
        guard = src[src.index('WOUNDAI_REQUIRE_FUNCTIONAL_TESTS") == "1":'):]
        guard = guard[:guard.index("if kind ==", 10)]
        self.assertIn("raise RuntimeError", guard)
        self.assertNotIn("LocalStore(", guard)

    def test_refusal_actually_happens_at_runtime(self):
        """真的跑一個子行程,不是只看原始碼。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "Backend" / "Flask")
        env["WOUNDAI_STORE"] = "gcs"
        env["WOUNDAI_GCS_BUCKET"] = "woundai-flywheel-jackh001"
        env["WOUNDAI_AUDIT_BUCKET"] = "woundai-flywheel-jackh001-audit-epoch-20260905"
        env["WOUNDAI_REQUIRE_FUNCTIONAL_TESTS"] = "1"
        proc = subprocess.run(
            [sys.executable, "-c",
             "import store; store.reset_store(None); store.get_store()"],
            env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(proc.returncode, 0,
                            "a test process must not be able to build a GCS store")
        self.assertIn("refused inside a test process", proc.stdout + proc.stderr)

    def test_real_use_is_not_broken(self):
        """沒有測試標記時,GCS 設定仍然正常生效——這道防線不能誤傷正式執行。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "Backend" / "Flask")
        env["WOUNDAI_STORE"] = "gcs"
        env["WOUNDAI_GCS_BUCKET"] = "woundai-flywheel-jackh001"
        env.pop("WOUNDAI_REQUIRE_FUNCTIONAL_TESTS", None)
        proc = subprocess.run(
            [sys.executable, "-c",
             "import store; store.reset_store(None);\n"
             "try:\n"
             "    s = store.get_store(); print('KIND', type(s).__name__)\n"
             "except Exception as e:\n"
             "    print('KIND', type(e).__name__, e)"],
            env=env, capture_output=True, text=True, timeout=60)
        out = proc.stdout + proc.stderr
        self.assertNotIn("refused inside a test process", out,
                         "the guard must not fire outside a test process")

    def test_local_store_is_unaffected_by_the_guard(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "Backend" / "Flask")
        for v in CLOUD_VARS:
            env.pop(v, None)
        env["WOUNDAI_REQUIRE_FUNCTIONAL_TESTS"] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            env["WOUNDAI_FLYWHEEL_DIR"] = tmp
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import store; store.reset_store(None); "
                 "print('KIND', type(store.get_store()).__name__)"],
                env=env, capture_output=True, text=True, timeout=60)
        self.assertIn("KIND LocalStore", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
