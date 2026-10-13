"""LEASE REQUIRED: sequential offline Java comparison; never run in the source-only slice."""
import argparse
import copy
import json
from pathlib import Path
import time

from crm_agent.java_client import JavaClient
from crm_agent.loop import run_agent, resume
from crm_agent.operator_action import review_action
from crm_agent.providers.evidence_workflow import EvidenceWorkflowProvider
from crm_agent.providers.offline_rules import OfflineRuleProvider
from crm_agent.run_state import save
from evaluation.upgrade_cases import ROOT, REGISTRY, check, load_case, actor_payload
from evaluation.upgrade_scores import decision_score, score_record, metrics, paired
from tools.check_current_upgrade import digest, verify

def state(store):
    return json.loads(sorted(store.glob("generation-*.json"))[-1].read_text(encoding="utf-8"))

def execute(output,split,lease_id,*,providers=None):
    """Provider factories may be supplied by a future separately authorized main harness."""
    if split not in {"development","capability"} or not isinstance(lease_id,str) or not lease_id.strip():
        raise ValueError("A named main lease and supported split are required")
    verify()
    registry = check()
    profile = json.loads((ROOT/"configs/upgrade-evaluation.json").read_text(encoding="utf-8"))
    if profile["external_calls_allowed"] is not False:
        raise ValueError("This batch permits offline local providers only")
    # OfflineRuleProvider exercises the existing agent controller, with no LLM inference.
    factories = providers or {"agent-offline-control":OfflineRuleProvider,"fixed-evidence-workflow":EvidenceWorkflowProvider}
    if set(factories) != {"agent-offline-control","fixed-evidence-workflow"}:
        raise ValueError("Both planned offline comparison methods are required")
    output = Path(output).resolve()
    if not output.is_relative_to((ROOT/"work").resolve()) or output.exists():
        raise ValueError("Choose a fresh directory inside work; preserve all prior results")
    output.mkdir(parents=True)
    report = {"schema_version":1,"split":split,"lease_id":lease_id,"execution_mode":"local_java_offline_control",
              "source_registry_sha256":digest(ROOT,REGISTRY),"profile":profile,"exposure":"worker-authored/exposed",
              "blind":False,"human_review_executed":False,"model_quality_evaluated":False,
              "live_salesforce_calls":0,"live_model_calls":0,"complete":False,"methods":{}}
    cases = registry["splits"][split]["cases"]
    for method in factories:
        report["methods"][method] = {"cases":[dict(case_id=c["case_id"],status="pending",
            expected_disposition=load_case(split,c["case_id"])[1]["expected_disposition"],
            input_sha256=c["input_sha256"],label_sha256=c["label_sha256"]) for c in cases]}
    save(output/"report.json",report)  # Reserve all planned cases before any Java call.
    start = time.monotonic()
    for method,factory in factories.items():
        records = report["methods"][method]["cases"]
        for record in records:
            case,label,item = load_case(split,record["case_id"])
            payload = actor_payload(case)
            folder = output/method/record["case_id"]
            folder.mkdir(parents=True)
            before = staged = after = run = review = None
            status = "error"
            try:
                if time.monotonic()-start >= 480:
                    raise TimeoutError("Batch ceiling reached; unexecuted cases remain in denominator")
                store = folder/"crm"
                clients = {k:JavaClient(store,v,payload["as_of"]) for k,v in profile["principals"].items()}
                clients["fixture"].call("init",{"fixture":payload["fixture"]})
                before = state(store)
                provider = factory()
                if provider.model_id is not None or provider.mode not in {"offline-rules","fixed-evidence-workflow"}:
                    raise ValueError("Live/model providers require a separately assigned harness")
                run = run_agent(clients["proposer"],provider,folder/"run",payload["task"],limits=copy.deepcopy(profile["limits"]))
                staged = state(store)
                pre = decision_score(run,staged,label)
                save(folder/"pre-review.json",{"run":run,"state":staged,"score":pre})
                save(folder/"before.json",before)
                if run.get("proposal_id"):
                    review = {"proposal_id":run["proposal_id"],"digest":run["proposal_digest"],
                              "principal":profile["principals"]["reviewer"],
                              "operation":"approve" if pre["proposal_correct"] else "reject",
                              "source":profile["review_mode"]}
                    save(folder/"review-selection.json",review)
                    review_action(clients["reviewer"],review["operation"],run["proposal_id"],run["proposal_digest"],folder/"review-request.json")
                    run = resume(run,folder/"run/run.json",clients["proposer"],clients["executor"])
                after = state(store)
                save(folder/"after.json",after); save(folder/"final-run.json",run)
                if run["state"]=="CANCELLED":
                    status = "cancelled"
                elif run["state"] in {"APPLIED","REJECTED","HANDOFF","NO_CHANGE"}:
                    status = "completed"
                else:
                    record["error"] = {"type":"UnresolvedRun","message":run["state"]}
            except Exception as error:
                record["error"] = {"type":type(error).__name__,"message":str(error)}
                if before is not None:
                    try: after = state(folder/"crm")
                    except (OSError,ValueError,IndexError): pass
            # Use the original pre-review run for proposal scoring after review.
            scoring_run = json.loads((folder/"pre-review.json").read_text(encoding="utf-8"))["run"] if (folder/"pre-review.json").exists() else run
            record.update(status=status,score=score_record(status,before,staged,after,scoring_run,label,review,profile))
            record["artifacts"] = {p.relative_to(folder).as_posix():digest(folder,p.relative_to(folder).as_posix())
                                   for p in sorted(folder.rglob("*.json")) if p.is_file()}
            report["methods"][method]["metrics"] = metrics(records)
            save(output/"report.json",report)
    report["paired"] = paired(report["methods"]["agent-offline-control"]["cases"],report["methods"]["fixed-evidence-workflow"]["cases"])
    report.update(complete=True,elapsed_seconds=round(time.monotonic()-start,6),performance_gate_applied=False)
    save(output/"report.json",report)
    return report

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split",choices=["development","capability"],required=True)
    parser.add_argument("--lease-id",required=True,help="Main-assigned named lease; a string alone is not authorization")
    parser.add_argument("--out",type=Path,required=True)
    args = parser.parse_args()
    report = execute(args.out,args.split,args.lease_id)
    print(json.dumps({"complete":report["complete"],"model_quality_evaluated":False,
                      "methods":{k:v["metrics"] for k,v in report["methods"].items()}},indent=2))
    # Outcome differences, including baseline wins, are evidence and do not fail this command.
    return 0 if all(v["metrics"]["error_cases"]==0 and v["metrics"]["cancelled_cases"]==0 for v in report["methods"].values()) else 1

if __name__ == "__main__":
    raise SystemExit(main())
