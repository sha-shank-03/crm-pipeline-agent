"""Static standalone-source audit. No Java, networking, scoring or git requirement."""
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", ".deps", "build", "work", ".venv", "__pycache__", ".pytest_cache", "node_modules", "target"}
REQUIRED = ["README.md", "LICENSE", "DATA_LICENSE", "THIRD_PARTY_NOTICES.md", "EVIDENCE.md",
            "docs/architecture.md", "docs/threat-model.md", "docs/evaluation.md", "docs/originality.md",
            "docs/quickstart.md", "docs/ci-actions.lock.json", ".github/workflows/ci.yml", "dependencies.lock.json"]

def source_files(root=None):
    root = Path(ROOT if root is None else root).resolve()
    files = []
    for parent, directories, filenames in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in EXCLUDED)
        for name in sorted(filenames):
            if name == "AGENTS.md" or name.startswith(".env") and name != ".env.example" or name.endswith((".pyc", ".pyo")):
                continue
            path = Path(parent) / name
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("Source export contains a link outside its root")
            files.append(path.relative_to(root))
    return sorted(files)

def check():
    from evaluation.freeze import check as freeze_check
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            raise ValueError("Missing release file: " + name)
    frozen = freeze_check()
    files = source_files()
    sensitive = re.compile(r"[A-Z]:[\\/]" + "|/" + "Users/|/" + "home/" + r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}")
    generated = {".jar", ".class", ".exe", ".dll", ".zip", ".pyc"}
    for relative in files:
        path = ROOT / relative
        if path.suffix.lower() in generated:
            raise ValueError("Generated binary in shipped source: " + relative.as_posix())
        text = path.read_text(encoding="utf-8")
        if sensitive.search(text):
            raise ValueError("Personal path or credential in shipped source: " + relative.as_posix())
        if path.suffix == ".py":
            compile(text, relative.as_posix(), "exec")
        if path.suffix == ".json":
            json.loads(text)
    lock = json.loads((ROOT / "dependencies.lock.json").read_text())
    expected = {"gson-2.14.0.jar": "2cbd119bf1961c28788310963dc80ba65f58cdeec1dd139c8bdb1240faa2c36f",
                "error_prone_annotations-2.48.0.jar": "b49c5c958316ed67a09c699dda9aa749caf434d51d863dea599ef36a49b9c855"}
    if {item["filename"]: item["sha256"] for item in lock["dependencies"]} != expected or len(lock["dependencies"]) != 2:
        raise ValueError("Dependency pin/closure changed")
    for filename, wanted in expected.items():
        cached = ROOT / ".deps" / filename
        if cached.exists() and hashlib.sha256(cached.read_bytes()).hexdigest() != wanted:
            raise ValueError("Cached artifact differs from pin")
    actions = json.loads((ROOT / "docs/ci-actions.lock.json").read_text())
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    for item in actions["actions"]:
        if not re.fullmatch("[0-9a-f]{40}", item["sha"]) or ("uses: " + item["repository"] + "@" + item["sha"]) not in workflow or item["license"] != "MIT":
            raise ValueError("CI action pin/attribution differs")
    uses = re.findall(r"uses:\s*(\S+)", workflow)
    if set(uses) != {item["repository"] + "@" + item["sha"] for item in actions["actions"]} or len(uses) != 3:
        raise ValueError("Unexpected CI action")
    for required in ["contents: read", "max-parallel: 1", "timeout-minutes: 10", "ubuntu-latest", "windows-latest", "persist-credentials: false"]:
        if required not in workflow:
            raise ValueError("CI execution constraint missing")
    if "secrets." in workflow or "upload-artifact" in workflow or "--allow-live-model" in workflow:
        raise ValueError("CI must remain offline and read-only")
    if "Apache License" not in (ROOT / "LICENSE").read_text() or "CC0-1.0" not in (ROOT / "DATA_LICENSE").read_text():
        raise ValueError("Project/data license missing")
    return {"source_files": len(files), "frozen_dev": frozen["splits"]["dev"]["case_count"],
            "frozen_heldout": frozen["splits"]["heldout"]["case_count"], "binary_dependencies": 2,
            "ci_actions_pinned": 3, "hosted_ci_executed": False, "static_source_audit": "passed"}
