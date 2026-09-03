# -*- coding: utf-8 -*-
"""Regression tests for the deployment-image canonical-byte health contract."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))
sys.path.insert(0, BACKEND)

import runtime_golden as golden


FAILED = []


def check(name, ok):
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        FAILED.append(name)


def main():
    measured = golden.compute()
    check("1  reviewed fixture passes the expected-byte gate", golden.is_expected(measured))
    check("2  same version with a changed SHA is rejected", not golden.is_expected({
        "version": golden.EXPECTED_VERSION, "sha256": "0" * 64,
    }))
    check("3  same SHA with a changed version is rejected", not golden.is_expected({
        "version": "canon-v0", "sha256": golden.EXPECTED_SHA256,
    }))
    check("4  app health calls the reviewed-byte predicate, not a version tautology",
          "is_expected as _golden_expected" in open(os.path.join(BACKEND, "app.py"), encoding="utf-8").read())
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
