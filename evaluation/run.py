"""Frozen offline comparison. Local integration/scoring requires an assigned milestone lease."""
import argparse
import json
from pathlib import Path
import time

from crm_agent.java_client import JavaClient
from crm_agent.loop import run_agent, resume
from crm_agent.providers.offline_rules import OfflineRuleProvider, LatestNoteProvider
from crm_agent.run_state import save
from .freeze import ROOT, MANIFEST, check, digest
from .oracle import proposal_score, final_score, fingerprint
from .metrics import metrics, reproducibility, outcome_gate

def state(store):
    return json.loads(sorted(store.glob("generation-*.json"))[-1].read_text(encoding="utf-8"))

def execute(output, split="dev", repeat=1, *, allow_heldout=False):
    if split not in {"dev", "heldout"} or type(repeat) is not int or repeat not in {1, 3}:
        raise ValueError("Unsupported comparison split/repetition")
    if split == "heldout" and (not allow_heldout or repeat != 3):
        raise ValueError("Held-out execution requires explicit authorization flag and exactly three repeats")
    frozen = check()
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Preserve existing evaluation outputs; choose a fresh directory")
    output.mkdir(parents=True)
    case_count = frozen["splits"][split]["case_count"]
    report = {"schema_version": 2, "split": split, "repeat": repeat, "model_quality_evaluated": False,
              "same_author_synthetic": True, "review_mode": "label_aware_scripted_after_pre_review_scoring",
              "frozen_manifest_sha256": digest(MANIFEST), "expected_cases_per_method": case_count,
              "methods": {}, "complete": False}
    start = time.monotonic()
    # Reserve the complete denominator before any init/provider call.
    for name, cls in [("offline-rules", OfflineRuleProvider), ("latest-note", LatestNoteProvider)]:
        records = [{"case_id": item["case_id"], "repeat": repetition, "status": "pending",
                    "expected_disposition": json.loads((ROOT / item["label"]).read_text(encoding="utf-8"))["expected_disposition"],
                    "input_sha256": item["input_sha256"], "label_sha256": item["label_sha256"]}
                   for repetition in range(1, repeat + 1) for item in frozen["splits"][split]["cases"]]
        report["methods"][name] = {"provider_mode": cls.mode, "model_id": None, "cases": records}
    save(output / "report.json", report)
    for name, cls in [("offline-rules", OfflineRuleProvider), ("latest-note", LatestNoteProvider)]:
        records = report["methods"][name]["cases"]
        case_map = {item["case_id"]: item for item in frozen["splits"][split]["cases"]}
        for record in records:
            item = case_map[record["case_id"]]
            if time.monotonic() - start > (240 if split == "dev" else 600):
                record.update(status="error", error={"type": "EvaluationBudgetExceeded", "message": "Not executed; complete planned denominator retained"})
                continue
            case = json.loads((ROOT / item["input"]).read_text(encoding="utf-8"))
            label = json.loads((ROOT / item["label"]).read_text(encoding="utf-8"))
            case_output = output / name / ("repeat-" + str(record["repeat"])) / item["case_id"]
            case_output.mkdir(parents=True)
            store = case_output / "crm"
            try:
                operator = JavaClient(store, "fixture-operator", case["as_of"])
                proposer = JavaClient(store, "proposer-north", case["as_of"])
                manager = JavaClient(store, "manager-north", case["as_of"])
                executor = JavaClient(store, "executor-north", case["as_of"])
                operator.call("init", {"fixture": case["fixture"]})
                before = state(store)
                run = run_agent(proposer, cls(), case_output / "run", case["task"])
                staged = state(store)
                score = proposal_score(run, staged, label)
                record["pre_review"] = score
                # Score and save before the benchmark reviewer sees or acts on its label.
                save(case_output / "before.json", before)
                save(case_output / "pre-review-state.json", staged)
                save(case_output / "pre-review-run.json", run)
                save(output / "report.json", report)
                if run.get("proposal_id"):
                    manager.call("approve" if score["correct"] else "reject", {"proposal_id": run["proposal_id"], "digest": run["proposal_digest"]})
                    run = resume(run, case_output / "run/run.json", proposer, executor)
                after = state(store)
                save(case_output / "after.json", after)
                save(case_output / "final-run.json", run)
                record.update(status="completed", final=final_score(before, after, label, score), fingerprint=fingerprint(after),
                              tool_calls=run["tool_calls"], provider_turns=run["provider_turns"],
                              repair_attempts=run["counters"]["errors"], active_milliseconds=run["active_milliseconds"])
                record["artifacts"] = {key: {"path": path.relative_to(output).as_posix(), "sha256": digest(path)} for key, path in {
                    "before": case_output / "before.json", "pre_review_state": case_output / "pre-review-state.json",
                    "pre_review_run": case_output / "pre-review-run.json", "after": case_output / "after.json",
                    "final_run": case_output / "final-run.json", "committed_state": sorted(store.glob("generation-*.json"))[-1]}.items()}
            except Exception as error:
                record.update(status="error", error={"type": type(error).__name__, "message": str(error)})
            report["methods"][name]["metrics"] = metrics(records)
            save(output / "report.json", report)
        report["methods"][name]["metrics"] = metrics(records)
        report["methods"][name]["reproducibility"] = reproducibility(records, repeat)
    report.update(complete=True, elapsed_seconds=round(time.monotonic() - start, 6))
    report["gate"] = outcome_gate(report)
    save(output / "report.json", report)
    return report

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["dev", "heldout"], required=True)
    parser.add_argument("--provider", choices=["offline-rules"], required=True)
    parser.add_argument("--baseline", choices=["latest-note"], required=True)
    parser.add_argument("--repeat", type=int, choices=[1, 3], required=True)
    parser.add_argument("--allow-heldout", action="store_true", help="Use only after rubric/freeze review and explicit execution authorization")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = execute(args.out, args.split, args.repeat, allow_heldout=args.allow_heldout)
    print(json.dumps({name: item["metrics"] for name, item in report["methods"].items()}, indent=2))
    print(json.dumps({"gate": report["gate"]}))
    return 0 if report["gate"]["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
