"""Export standalone source; optional cached quickstart invokes owned Java and needs a local lease."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.release import source_files

def export(destination, cached=False):
    destination = Path(destination).resolve()
    if destination.exists() or destination == ROOT or destination in ROOT.parents:
        raise ValueError("Choose a fresh output directory; existing outputs are preserved")
    paths = source_files(ROOT)  # inventory before output creation prevents recursion
    if cached:
        from tools.bootstrap import verify_cached
        jars = verify_cached(ROOT)  # both original pins before any output; never a fetch fallback
    destination.mkdir(parents=True)
    for relative in paths:
        target = destination / relative; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    if cached:
        (destination / ".deps").mkdir()
        for jar in jars:
            shutil.copyfile(jar, destination / ".deps" / jar.name)
    return destination

def verify_quickstart(destination):
    commands = [[sys.executable, "tools/bootstrap.py", "--cached-only"], [sys.executable, "tools/build.py"],
                [sys.executable, "crm.py", "doctor"],
                [sys.executable, "crm.py", "demo", "--scenario", "approved-update", "--provider", "scripted", "--scripted-review", "--out", "work/quickstart-approved"],
                [sys.executable, "crm.py", "demo", "--scenario", "stale-approval", "--provider", "scripted", "--scripted-review", "--out", "work/quickstart-stale"],
                [sys.executable, "tools/check.py", "--suite", "m1"],
                [sys.executable, "-m", "unittest", "tests.test_salesforce_contract", "tests.test_salesforce_http", "-v"],
                [sys.executable, "tools/upgrade_check.py", "--offline", "--out", "work/quickstart-gateway-double"],
                [sys.executable, "tools/check_current_upgrade.py"], [sys.executable, "tools/check.py", "--suite", "release"]]
    receipts = []
    for argv in commands:
        started = time.monotonic()
        process = subprocess.run(argv, cwd=destination, capture_output=True, text=True, encoding="utf-8", timeout=90)
        receipts.append({"argv": ["python3.11"] + argv[1:], "exit_code": process.returncode,
                         "seconds": round(time.monotonic() - started, 6), "stdout": process.stdout, "stderr": process.stderr})
        (destination / "work").mkdir(exist_ok=True)
        (destination / "work/clean-copy-check.json").write_text(json.dumps({"commands": receipts}, indent=2) + "\n")
        if process.returncode:
            return process.returncode
    return 0

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True); parser.add_argument("--cached-deps", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify and not args.cached_deps:
        raise ValueError("Automated clean-copy verification requires both already-verified cached JARs; no downloads")
    destination = export(args.out, args.cached_deps)
    result = verify_quickstart(destination) if args.verify else 0
    print(json.dumps({"copied": True, "cached_dependencies": args.cached_deps, "quickstart_executed": args.verify, "exit_code": result}))
    return result

if __name__ == "__main__":
    try: raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr); raise SystemExit(2)
