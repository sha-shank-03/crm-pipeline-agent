"""Negative controls against an already verified report, in isolated disposable copies."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import tempfile
from .freeze import digest
from .verify import verify, load_json

def run(report_path, output):
    report_path = Path(report_path).resolve(); output = Path(output).resolve()
    positive = verify(report_path)
    if not positive.get("gate", {}).get("passed"):
        raise ValueError("Controls require an intact passing schema-2 report")
    if output.exists() or output.is_relative_to(report_path.parent):
        raise ValueError("Use a fresh output outside the report tree")
    if sum(path.stat().st_size for path in report_path.parent.rglob("*") if path.is_file()) > 64 * 1024 * 1024:
        raise ValueError("Control source exceeds bounded copy footprint")
    original_sha = digest(report_path)
    source = load_json(report_path)
    chosen = next(index for index, item in enumerate(source["methods"]["offline-rules"]["cases"]) if item["pre_review"]["disposition"] == "PROPOSE")
    specifications = [
        ("missing-case", None, lambda r, _: r["methods"]["offline-rules"]["cases"].pop()),
        ("duplicate-case", None, lambda r, _: r["methods"]["offline-rules"]["cases"].__setitem__(1, copy.deepcopy(r["methods"]["offline-rules"]["cases"][0]))),
        ("edited-metric", None, lambda r, _: r["methods"]["offline-rules"]["metrics"]["pre_review_exact"].update(numerator=0)),
        ("edited-expected-outcome", None, lambda r, row: row.update(expected_disposition="HANDOFF")),
        ("edited-gate", None, lambda r, _: r["gate"].update(passed=False)),
        ("edited-repeat-stability", None, lambda r, _: r["methods"]["offline-rules"]["reproducibility"].update(stable_cases=0)),
        ("escaped-artifact", None, lambda r, row: row["artifacts"]["after"].update(path="../../outside.json")),
        ("changed-after-unrefreshed-hash", "after", lambda value, _: value["opportunities"].pop(next(iter(value["opportunities"])))),
        ("lost-target-rehashed", "after", lambda value, row: value["opportunities"].pop(next(iter(value["receipts"].values()))["opportunity_id"])),
        ("lost-account-rehashed", "after", lambda value, _: value["accounts"].pop(next(iter(value["accounts"])))),
        ("changed-evidence-rehashed", "after", lambda value, _: value["notes"][next(iter(value["notes"]))].update(visibility=[])),
        ("receipt-digest-rehashed", "after", lambda value, _: next(iter(value["receipts"].values())).update(digest="0" * 64)),
        ("expired-approval-rehashed", "after", lambda value, _: next(iter(value["approvals"].values())).update(expires_at="2000-01-01T00:00:00Z")),
        ("protected-owner-rehashed", "after", lambda value, _: value["opportunities"][next(iter(value["receipts"].values()))["opportunity_id"]].update(owner_id="fabricated-owner")),
        ("changed-before-fixture-rehashed", "before", lambda value, _: value["accounts"][next(iter(value["accounts"]))].update(region="fabricated-region")),
        ("committed-generation-rehashed", "committed_state", lambda value, _: value.update(generation=499)),
        ("final-counter-rehashed", "final_run", lambda value, _: value.update(tool_calls=value["tool_calls"] + 1)),
        ("final-status-rehashed", "final_run", lambda value, _: value.update(state="WAITING_APPROVAL")),
    ]
    output.mkdir(parents=True); records = []
    for name, artifact_key, mutation in specifications:
        with tempfile.TemporaryDirectory(dir=output) as scratch:
            destination = Path(scratch) / "copy"
            shutil.copytree(report_path.parent, destination)
            report = copy.deepcopy(source); row = report["methods"]["offline-rules"]["cases"][chosen]
            saved = output / name; saved.mkdir()
            if artifact_key:
                artifact = row["artifacts"][artifact_key]; target = destination / artifact["path"]
                value = load_json(target); mutation(value, row)
                target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                if name != "changed-after-unrefreshed-hash": artifact["sha256"] = digest(target)
                shutil.copyfile(target, saved / (artifact_key + ".json"))
            else: mutation(report, row)
            candidate = destination / report_path.name
            candidate.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            try:
                verify(candidate); rejected = False; reason = "Incorrectly accepted"
            except ValueError as error:
                rejected = True; reason = str(error)
            records.append({"control": name, "rejected": rejected, "reason": reason, "case_id": row["case_id"], "repeat": row["repeat"]})
            (saved / "changed-record.json").write_text(json.dumps(row, indent=2) + "\n")
            metadata = {key: value for key, value in report.items() if key != "methods"}
            metadata["methods"] = {method: {**{key: value for key, value in info.items() if key != "cases"},
                                                   "case_identities": [[case["case_id"], case["repeat"]] for case in info["cases"]]}
                                   for method, info in report["methods"].items()}
            (saved / "changed-report-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    result = {"source_report_sha256": digest(report_path), "positive_verified_case_runs": positive["verified_case_runs"],
              "controls": records, "rejected": sum(item["rejected"] for item in records), "total": len(records),
              "source_report_unchanged": digest(report_path) == original_sha}
    if not result["source_report_unchanged"]:
        raise ValueError("Source report changed during isolated controls")
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--report", type=Path, required=True); parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(); result = run(args.report, args.out)
    print(json.dumps({"rejected": result["rejected"], "total": result["total"]}))
    raise SystemExit(0 if result["rejected"] == result["total"] else 1)
