"""light/m2-unit/m3-unit/checkpoint-unit/release are pure/static; other suites require a local integration lease."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["light", "m2-unit", "m3-unit", "checkpoint-unit", "m1", "policy", "recovery", "provider", "all", "release"], required=True)
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 11):
        raise ValueError("Select Python 3.11 explicitly")
    if args.suite == "release":
        from tools.release import check
        print(json.dumps(check(), indent=2))
        return 0
    (ROOT / "work").mkdir(exist_ok=True)
    names = ["tests.test_python"]
    if args.suite == "checkpoint-unit":
        names = ["tests.test_run_state"]
    if args.suite in {"m2-unit", "m3-unit", "provider", "all"}:
        names.append("tests.test_m2_components")
        names.append("tests.test_m2_oracle")
    if args.suite in {"policy", "recovery"}:
        names = ["tests.test_m2_" + args.suite]
    if args.suite in {"m3-unit", "all"}:
        names.append("tests.test_run_state")
        names.append("tests.test_m3_evaluation")
        names.append("tests.test_m3_release")
    if args.suite in {"provider", "all"}:
        names.append("tests.test_m2_http")
    if args.suite == "all":
        names.extend(["tests.test_m2_policy", "tests.test_m2_recovery"])
    if args.suite in {"m1", "all"}:
        from tools.build import verify_dependency
        jars = verify_dependency()
        if not (ROOT / "build/classes/crm/CrmTests.class").is_file():
            raise ValueError("Build required before M1 checks")
        process = subprocess.run(["java", "-Xmx128m", "-cp", os.pathsep.join([str(ROOT / "build/classes")] + list(map(str, jars))), "crm.CrmTests"],
                                 cwd=ROOT, capture_output=True, text=True, timeout=20, check=False)
        print(process.stdout, end="")
        if process.returncode:
            print(process.stderr, file=sys.stderr)
            return process.returncode
        names.append("tests.test_m1_integration")
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1
    summary = {"suite": args.suite, "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
               "model_quality_evaluated": False}
    if args.suite in {"m1", "all"}:
        from crm import demo
        base = ROOT / "work" / ("m1-check-" + str(uuid.uuid4()))
        summary["demos"] = [demo(name, base / name) for name in ["approved-update", "stale-approval"]]
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, AssertionError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
