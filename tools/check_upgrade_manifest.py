"""Verify upgrade-v1 source and preserved v1 data without Git, Java, credentials or network."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_FREEZE = "66263a4f795b72687bd27d703ec8b45fa6203e439a5742f074ad0ba6d0ad3024"
BASE = "3c1e900842a883988b2f06bbd819d0fec77f45ac"
MANIFEST = ROOT / "evaluation/upgrade-v1/manifest.json"


def digest(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT) or path.name.startswith(".env"):
        raise ValueError("Manifest path outside permitted source")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(value=None):
    value = json.loads(MANIFEST.read_text(encoding="utf-8")) if value is None else value
    if value["version"] != "upgrade-v1-u1" or value["base_commit"] != BASE:
        raise ValueError("Unsupported upgrade/base")
    if value["original_frozen_manifest_sha256"] != ORIGINAL_FREEZE or digest("evaluation/frozen-manifest.json") != ORIGINAL_FREEZE:
        raise ValueError("Original freeze changed")
    paths = [item["path"] for item in value["files"]]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate source entry")
    expected = {item["path"]: item for item in value["files"]}
    original = json.loads((ROOT / "evaluation/frozen-manifest.json").read_text(encoding="utf-8"))
    for item in original["rules"]:
        current = expected.get(item["path"])
        if current is None or current["base_sha256"] != item["sha256"]:
            raise ValueError("Missing original source provenance")
    for item in value["files"]:
        if digest(item["path"]) != item["sha256"]:
            raise ValueError("Upgrade source differs: " + item["path"])
    case_files = 0
    for split in original["splits"].values():
        for case in split["cases"]:
            for key in ("input", "label"):
                if digest(case[key]) != case[key + "_sha256"]:
                    raise ValueError("Original data changed")
                case_files += 1
    return {"version": value["version"], "base_commit": BASE,
            "original_freeze_preserved": True, "original_data_files_checked": case_files,
            "source_files_checked": len(paths), "worker_acceptance": False}


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
