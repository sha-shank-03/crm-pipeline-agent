"""U2-B dataset integrity and actor projection; no execution, Java or network."""
import copy
import json
from pathlib import Path

from tools.check_current_upgrade import digest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "evaluation/upgrade-tasks-v1/registry.json"

def check(root=ROOT):
    root = Path(root).resolve()
    value = json.loads((root/REGISTRY).read_text(encoding="utf-8"))
    if (value.get("version") != "u2b-invented-exposed-v1" or value.get("license") != "CC0-1.0"
            or value.get("exposure") != "worker-authored/exposed" or value.get("blind") is not False
            or set(value.get("splits",{})) != {"development","capability"}):
        raise ValueError("Dataset provenance or exposure differs")
    for key,path in (("baseline_source_sha256","crm_agent/providers/evidence_workflow.py"),
                     ("profile_sha256","configs/upgrade-evaluation.json")):
        if digest(root,path) != value[key]:
            raise ValueError("Frozen baseline or equal-budget profile changed")
    ids, inputs = set(), set()
    for split,cohort in value["splits"].items():
        if cohort.get("case_count") != 12 or len(cohort["cases"]) != 12 or cohort.get("exposure") != "worker-authored/exposed":
            raise ValueError("Expected 12 exposed cases per split")
        families = set()
        for item in cohort["cases"]:
            if item["case_id"] in ids or item["input_sha256"] in inputs:
                raise ValueError("Duplicate case ID or task bytes")
            ids.add(item["case_id"]); inputs.add(item["input_sha256"]); families.add(item["family"])
            for field in ("input","label"):
                if digest(root,item[field]) != item[field+"_sha256"]:
                    raise ValueError("Task or label hash differs")
                if not item[field].startswith(f"evaluation/upgrade-tasks-v1/{split}/{field}s/"):
                    raise ValueError("Task split/path differs")
            case = json.loads((root/item["input"]).read_text(encoding="utf-8"))
            label = json.loads((root/item["label"]).read_text(encoding="utf-8"))
            validate(case,label,item)
        if len(families) != 12:
            raise ValueError("Expected twelve distinct scenario families")
    old = value["retained_regression"]
    if old["case_count"] != 32 or old["split"] != "heldout" or digest(root,old["manifest"]) != old["manifest_sha256"]:
        raise ValueError("Original regression provenance differs")
    original = json.loads((root/old["manifest"]).read_text(encoding="utf-8"))
    if old["case_ids"] != [c["case_id"] for c in original["splits"]["heldout"]["cases"]]:
        raise ValueError("Original regression cohort differs")
    return value

def validate(case,label,item):
    if (set(case) != {"schema_version","case_id","as_of","task","fixture"} or type(case["schema_version"]) is not int
            or case["schema_version"] != 1 or case["fixture"].get("synthetic") is not True
            or case["case_id"] != label["case_id"] or case["case_id"] != item["case_id"]
            or label["family"] != item["family"] or label["expected_disposition"] not in {"PROPOSE","NO_CHANGE","HANDOFF"}):
        raise ValueError("Case/label contract differs")
    target = label["opportunity_id"]
    if target not in case["fixture"]["opportunities"] or target not in case["task"]:
        raise ValueError("Label target unavailable")
    changes = label["expected_changes"]
    if (not isinstance(changes,dict) or set(changes)-{"close_date","next_step"}
            or bool(changes) != (label["expected_disposition"]=="PROPOSE")
            or set(label["semantic_evidence"]) != set(changes)):
        raise ValueError("Invalid expected changes/evidence")
    if label["expected_final_version"] != case["fixture"]["opportunities"][target]["version"] + bool(changes):
        raise ValueError("Invalid expected final version")
    for field, items in label["semantic_evidence"].items():
        if not items:
            raise ValueError("Expected field lacks evidence")
        for evidence in items:
            if set(evidence) != {"note_id","line_id","quote"}:
                raise ValueError("Invalid evidence label")
            note = case["fixture"]["notes"][evidence["note_id"]]
            if not any(l["id"]==evidence["line_id"] and l["text"]==evidence["quote"] for l in note["lines"]):
                raise ValueError("Label quote absent from task")

def load_case(split,case_id,root=ROOT):
    registry = check(root)
    if split not in registry["splits"]:
        raise ValueError("Unsupported U2-B split")
    item = next((c for c in registry["splits"][split]["cases"] if c["case_id"]==case_id),None)
    if item is None:
        raise ValueError("Unknown case")
    root = Path(root)
    return (json.loads((root/item["input"]).read_text(encoding="utf-8")),
            json.loads((root/item["label"]).read_text(encoding="utf-8")),copy.deepcopy(item))

def actor_payload(case):
    """Only business context reaches the acting controller; labels stay with evaluator."""
    if set(case) != {"schema_version","case_id","as_of","task","fixture"}:
        raise ValueError("Unexpected actor input metadata")
    return copy.deepcopy({k:case[k] for k in ("as_of","task","fixture")})
