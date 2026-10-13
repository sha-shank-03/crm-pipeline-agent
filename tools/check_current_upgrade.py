"""File-path source CLI correction and preserved original through v6; no Java/network."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))  # file-path CLI resolves packages from its own source copy
BASE = "3c1e900842a883988b2f06bbd819d0fec77f45ac"
ORIGINAL = "66263a4f795b72687bd27d703ec8b45fa6203e439a5742f074ad0ba6d0ad3024"
U1 = "f319b81638d34828bcf280672f313ea4f7e47af1f01f3afdc0e729e0f113e340"
U2A = "868ee93e098d43844ba4a06ae268edf903791e4319d26ef9b607b7c1b7cf2da8"
V3 = "46450da40aaba9042b4119be2de42683216265cf4793c1f21e5af9d95c650e18"
V4 = "aecb9564b4022973aa78e80d7a3dfd46b15c78cbbe16f4e915fe85172da1ee59"
V5 = "d6530c2cec9e521d318e077b5b2b2edede2d0d267b5d2d08df9f30240e6a8ab4"
V6 = "f70f9823ff565cff8df1b61bf36e4cfc605b46198f8af574610ccd4267cf7d92"
NEWLY_SEALED_EXISTING = {
    "tools/bootstrap.py": "d464d7df71faa99b8051bb2715c6dd75c2ba24408b4cb1dfa599d8b4dfcb56f7",
    "docs/quickstart.md": "a384601aa6e1a21117b492a73a9b9a56e3f7c9e8c868912d96692bfd73ba3af8",
}
VERSION = "upgrade-v7-file-cli"
MANIFEST = "evaluation/upgrade-v7/manifest.json"
U2C_CLI_MUTABLE = frozenset({"tools/check_current_upgrade.py", "tests/test_upgrade_provenance.py",
    ".github/workflows/ci.yml", "docs/quickstart.md", "README.md", "EVIDENCE.md"})
U2C_CACHE_MUTABLE = frozenset({"tools/bootstrap.py", "tools/clean_copy.py", "README.md", "EVIDENCE.md",
    "docs/quickstart.md", ".github/workflows/ci.yml", "tests/test_m3_release.py",
    "tools/check_current_upgrade.py", "tests/test_upgrade_provenance.py"})
U2C_MUTABLE = frozenset({"README.md", "EVIDENCE.md", ".github/workflows/ci.yml",
                         "tools/check_current_upgrade.py", "tests/test_upgrade_provenance.py"})
U2B_MUTABLE = frozenset({"tools/check_current_upgrade.py", "tests/test_upgrade_provenance.py"})
U1_MUTABLE = frozenset({".github/workflows/ci.yml","tools/release.py","tools/clean_copy.py"})
U2A_MUTABLE = frozenset({"crm_agent/connectors/salesforce.py", "tests/test_salesforce_contract.py",
    "tools/check_current_upgrade.py", "tests/test_upgrade_provenance.py", "docs/u2a-salesforce.md"})
REQUIRED_CURRENT = frozenset({"crm_agent/connectors/__init__.py","crm_agent/connectors/salesforce.py",
 "crm_agent/connectors/salesforce_http.py","crm_agent/connectors/salesforce_worker.py",
 "configs/salesforce.disabled.json","tests/test_salesforce_contract.py","tests/test_salesforce_http.py",
 "tests/test_upgrade_provenance.py","tests/test_m3_release.py","tools/check_current_upgrade.py","docs/u2a-salesforce.md",
 "crm_agent/providers/evidence_workflow.py", "evaluation/upgrade_cases.py", "evaluation/upgrade_scores.py",
 "tools/u2b_check.py", "tools/author_upgrade_tasks.py", "configs/upgrade-evaluation.json", "docs/u2b-evaluation.md",
 "tests/test_evidence_workflow.py", "tests/test_upgrade_evaluation.py", "evaluation/upgrade-tasks-v1/registry.json",
 "tools/summarize_u2b.py", "tests/test_u2b_summary.py", "docs/u2b-case-study.md", "docs/u2b-offline-summary.json",
 "tools/bootstrap.py", "tools/clean_copy.py", "tests/test_cached_packaging.py", "docs/quickstart.md",
 "tests/test_provenance_cli.py"})


def digest(root, relative):
    if not isinstance(relative,str) or "\\" in relative or ":" in relative:
        raise ValueError("Nonportable manifest path")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or str(path) != relative or any(p.startswith(".env") for p in path.parts):
        raise ValueError("Invalid source path")
    actual = root.joinpath(*path.parts)
    if actual.is_symlink() or not actual.resolve().is_relative_to(root) or not actual.is_file():
        raise ValueError("Source path outside root or missing")
    return hashlib.sha256(actual.read_bytes()).hexdigest()


def verify(root=None, value=None):
    root = Path(ROOT if root is None else root).resolve()
    value = json.loads((root/MANIFEST).read_text(encoding="utf-8")) if value is None else value
    if (value.get("version") != VERSION or value.get("base_commit") != BASE or
            value.get("original_frozen_manifest_sha256") != ORIGINAL or value.get("accepted_u1_manifest_sha256") != U1
            or value.get("original_u2a_manifest_sha256") != U2A or value.get("accepted_u2a_identity_manifest_sha256") != V3
            or value.get("accepted_u2b_manifest_sha256") != V4 or value.get("pre_correction_u2c_manifest_sha256") != V5
            or value.get("pre_cli_fix_v6_manifest_sha256") != V6):
        raise ValueError("Upgrade/base provenance differs")
    if value.get("newly_sealed_existing_pre_correction_sha256") != NEWLY_SEALED_EXISTING:
        raise ValueError("Previously unsealed bootstrap/guide provenance differs")
    if digest(root,"evaluation/frozen-manifest.json") != ORIGINAL or digest(root,"evaluation/upgrade-v1/manifest.json") != U1:
        raise ValueError("Original or accepted U1 manifest changed")
    if digest(root,"evaluation/upgrade-v2/manifest.json") != U2A:
        raise ValueError("Reviewed U2-A manifest changed")
    if digest(root,"evaluation/upgrade-v3/manifest.json") != V3:
        raise ValueError("Accepted U2-A identity manifest changed")
    if digest(root,"evaluation/upgrade-v4/manifest.json") != V4:
        raise ValueError("Accepted U2-B manifest changed")
    if digest(root,"evaluation/upgrade-v5/manifest.json") != V5:
        raise ValueError("Pre-correction U2-C manifest changed")
    if digest(root,"evaluation/upgrade-v6/manifest.json") != V6:
        raise ValueError("Failed file-CLI v6 manifest changed")
    original = json.loads((root/"evaluation/frozen-manifest.json").read_text(encoding="utf-8"))
    accepted = json.loads((root/"evaluation/upgrade-v1/manifest.json").read_text(encoding="utf-8"))
    predecessor = json.loads((root/"evaluation/upgrade-v2/manifest.json").read_text(encoding="utf-8"))
    v3 = json.loads((root/"evaluation/upgrade-v3/manifest.json").read_text(encoding="utf-8"))
    v4 = json.loads((root/"evaluation/upgrade-v4/manifest.json").read_text(encoding="utf-8"))
    v5 = json.loads((root/"evaluation/upgrade-v5/manifest.json").read_text(encoding="utf-8"))
    v6 = json.loads((root/"evaluation/upgrade-v6/manifest.json").read_text(encoding="utf-8"))
    entries = value.get("files")
    if not isinstance(entries,list) or not 1 <= len(entries) <= 129:
        raise ValueError("Invalid source inventory")
    expected = {}
    for entry in entries:
        if not isinstance(entry,dict) or set(entry) != {"path","base_sha256","u1_sha256","u2a_sha256","v3_sha256","v4_sha256","v5_sha256","v6_sha256","sha256"}:
            raise ValueError("Invalid source entry")
        if entry["path"] in expected:
            raise ValueError("Duplicate source entry")
        for key in ("base_sha256","u1_sha256","u2a_sha256","v3_sha256","v4_sha256","v5_sha256","v6_sha256","sha256"):
            h = entry[key]
            if (key == "sha256" or h is not None) and (not isinstance(h,str) or not re.fullmatch("[0-9a-f]{64}",h)):
                raise ValueError("Invalid source hash")
        expected[entry["path"]] = entry
    if not REQUIRED_CURRENT <= set(expected):
        raise ValueError("Current adapter/release source omitted")
    prior_paths = {old["path"] for old in v6["files"]}
    for old in v6["files"]:
        current = expected.get(old["path"])
        if (current is None or current["v6_sha256"] != old["sha256"] or
                any(current[k] != old[k] for k in ("base_sha256","u1_sha256","u2a_sha256","v3_sha256","v4_sha256","v5_sha256"))):
            raise ValueError("Failed file-CLI v6 source provenance omitted or changed")
        if old["path"] not in U2C_CLI_MUTABLE and current["sha256"] != old["sha256"]:
            raise ValueError("Failed file-CLI v6 source outside file CLI correction changed")
    for old in v5["files"]:
        current = expected.get(old["path"])
        if (current is None or current["v5_sha256"] != old["sha256"] or
                any(current[k] != old[k] for k in ("base_sha256","u1_sha256","u2a_sha256","v3_sha256","v4_sha256"))):
            raise ValueError("Pre-correction v5 source provenance omitted or changed")
        if old["path"] not in U2C_CACHE_MUTABLE and current["sha256"] != old["sha256"]:
            raise ValueError("Pre-correction v5 source outside cached packaging correction changed")
    for old in v4["files"]:
        current = expected.get(old["path"])
        if (current is None or current["v4_sha256"] != old["sha256"] or
                any(current[k] != old[k] for k in ("base_sha256","u1_sha256","u2a_sha256","v3_sha256"))):
            raise ValueError("Accepted v4 source provenance omitted or changed")
        if old["path"] not in U2C_MUTABLE | U2C_CACHE_MUTABLE and current["sha256"] != old["sha256"]:
            raise ValueError("Accepted v4 source outside U2-C packaging scope changed")
    for old in v3["files"]:
        current = expected.get(old["path"])
        if (current is None or current["v3_sha256"] != old["sha256"] or
                any(current[k] != old[k] for k in ("base_sha256","u1_sha256","u2a_sha256"))):
            raise ValueError("Accepted v3 source provenance omitted or changed")
        if old["path"] not in U2B_MUTABLE | U2C_MUTABLE | U2C_CACHE_MUTABLE and current["sha256"] != old["sha256"]:
            raise ValueError("Accepted v3 source outside U2-B provenance scope changed")
    for entry in entries:
        if entry["path"] not in prior_paths and any(entry[k] is not None for k in ("base_sha256","u1_sha256","u2a_sha256","v3_sha256","v4_sha256","v5_sha256","v6_sha256")):
            raise ValueError("Additive source has invented predecessor provenance")
    for old in predecessor["files"]:
        current = expected.get(old["path"])
        if (current is None or current["u2a_sha256"] != old["sha256"] or
                current["u1_sha256"] != old["u1_sha256"] or current["base_sha256"] != old["base_sha256"]):
            raise ValueError("Reviewed U2-A source provenance omitted or changed")
        if old["path"] not in U2A_MUTABLE | U2B_MUTABLE | U2C_MUTABLE | U2C_CACHE_MUTABLE and current["sha256"] != old["sha256"]:
            raise ValueError("Reviewed U2-A source outside identity correction changed")
    for old in accepted["files"]:
        current = expected.get(old["path"])
        if current is None or current["u1_sha256"] != old["sha256"] or current["base_sha256"] != old["base_sha256"]:
            raise ValueError("U1 source provenance omitted or changed")
        if old["path"] not in U1_MUTABLE | U2C_MUTABLE | U2C_CACHE_MUTABLE and current["sha256"] != old["sha256"]:
            raise ValueError("Accepted U1 source outside U2-A scope changed")
    for old in original["rules"]:
        if expected.get(old["path"],{}).get("base_sha256") != old["sha256"]:
            raise ValueError("Original source provenance changed")
    for entry in entries:
        if digest(root,entry["path"]) != entry["sha256"]:
            raise ValueError("Current source hash differs: "+entry["path"])
    from evaluation.upgrade_cases import check as check_tasks, REGISTRY
    if value.get("u2b_registry_sha256") != digest(root,REGISTRY):
        raise ValueError("U2-B registry provenance differs")
    tasks = check_tasks(root)
    from tools.summarize_u2b import SUMMARY, validate_redaction
    if value.get("redacted_u2b_summary_sha256") != digest(root,SUMMARY):
        raise ValueError("Redacted summary provenance differs")
    summary = json.loads((root/SUMMARY).read_text(encoding="utf-8"))
    validate_redaction(summary)
    if summary["source_evidence_sha256"]["sealed_u2b_manifest"] != V4:
        raise ValueError("Summary accepted source binding differs")
    if not {item[key] for split in tasks["splits"].values() for item in split["cases"] for key in ("input","label")} <= set(expected):
        raise ValueError("U2-B task source inventory omitted")
    count = 0
    for split in original["splits"].values():
        for case in split["cases"]:
            for key in ("input","label"):
                if digest(root,case[key]) != case[key+"_sha256"]:
                    raise ValueError("Original data changed")
                count += 1
    return {"version":value["version"],"base_commit":BASE,"original_freeze_preserved":True,
            "u1_manifest_preserved":True,"original_u2a_manifest_preserved":True,"v3_identity_manifest_preserved":True,
            "v4_u2b_manifest_preserved":True,"redacted_summary_checked":True,
            "pre_correction_v5_manifest_preserved":True,"cached_packaging_native_executed":False,
            "failed_file_cli_v6_manifest_preserved":True,
            "original_data_files_checked":count,"source_files_checked":len(entries),
            "frozen_dev":8,"frozen_heldout":32,"u2b_development":12,"u2b_capability":12,"u2b_blind":False,
            "new_evaluation_executed":False,"worker_self_accepted":False}


if __name__ == "__main__":
    print(json.dumps(verify(),indent=2))

