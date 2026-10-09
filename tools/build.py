"""Compile sorted Java 17 sources with a hash-verified dependency. Never downloads."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def verify_dependency():
    lock = json.loads((ROOT / "dependencies.lock.json").read_text(encoding="utf-8"))
    expected_coordinates = ["com.google.code.gson:gson:2.14.0", "com.google.errorprone:error_prone_annotations:2.48.0"]
    if [item["coordinate"] for item in lock["dependencies"]] != expected_coordinates:
        raise ValueError("Unexpected dependency closure")
    jars = []
    for dependency in lock["dependencies"]:
        expected = dependency.get("sha256")
        jar = ROOT / ".deps" / dependency["filename"]
        if not expected or not jar.is_file() or hashlib.sha256(jar.read_bytes()).hexdigest() != expected:
            raise ValueError("Pinned artifact is missing or unverified; use explicit bootstrap first")
        jars.append(jar)
    return jars


def main():
    if sys.version_info[:2] != (3, 11):
        raise ValueError("Select Python 3.11 explicitly")
    jars = verify_dependency()
    output = ROOT / "build/classes"
    output.mkdir(parents=True, exist_ok=True)
    sources = sorted((ROOT / "java/src").rglob("*.java"))
    sources += sorted((ROOT / "java/test").rglob("*.java"))
    arguments = ROOT / "build/sources.args"
    arguments.write_text("\n".join('"' + path.as_posix() + '"' for path in sources) + "\n", encoding="utf-8")
    process = subprocess.run(["javac", "-J-Xmx128m", "--release", "17", "-encoding", "UTF-8", "-cp", os.pathsep.join(map(str, jars)),
                              "-d", str(output), "@" + str(arguments)], cwd=ROOT, timeout=60, check=False)
    if process.returncode:
        return process.returncode
    print(json.dumps({"java_release": 17, "source_files": len(sources), "dependency_verified": True}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
