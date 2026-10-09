"""M1 CLI. Scripted decisions and scripted demo review are visibly labeled."""
import argparse
import copy
import json
from pathlib import Path
import platform
import subprocess
import sys

from crm_agent.java_client import DomainError, JavaClient, ROOT
from crm_agent.loop import run_agent, resume, request_cancel
from crm_agent.protocol import ProtocolError, strict_json
from crm_agent.review import render
from crm_agent.operator_action import review_action
from crm_agent.run_state import load, save
from crm_agent.scripted import ScriptedProvider
from crm_agent.providers.offline_rules import OfflineRuleProvider


def client(store, principal, as_of="2026-10-08"):
    return JavaClient(Path(store), principal, as_of)


def dump(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def resolve_proposal(store, identifier, as_of):
    if identifier != "latest":
        return identifier
    # Trusted local operator view; resolution is frozen before review/approval.
    state = client(store, "fixture-operator", as_of).call("inspect", {})
    proposals = list(state["proposals"])
    if not proposals:
        raise ValueError("Store has no proposal")
    return max(proposals, key=lambda item: next(event["commit_generation"] for event in state["audit"]
                                              if event["type"] == "PROPOSAL_STAGED" and event["proposal_id"] == item))


def demo(scenario, output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Demo output already exists; choose a fresh output directory")
    output.mkdir(parents=True)
    store = output / "crm"
    fixture = strict_json((ROOT / "fixtures/dev/approved-update.json").read_text(encoding="utf-8"))
    operator = client(store, "fixture-operator")
    operator.call("init", {"fixture": fixture})
    before = operator.call("inspect", {})
    run = run_agent(client(store, "proposer-north"), ScriptedProvider(), output / "run")
    if run["state"] != "WAITING_APPROVAL":
        raise AssertionError("Demo did not pause for review")
    manager = client(store, "manager-north")
    packet = manager.call("review", {"proposal_id": run["proposal_id"]})
    (output / "review.txt").write_text(render(packet) + "\n", encoding="utf-8")
    manager.call("approve", {"proposal_id": run["proposal_id"], "digest": run["proposal_digest"]})
    if scenario == "stale-approval":
        operator.call("edit_opportunity", {"opportunity_id": "opp-101", "expected_version": 7,
                                          "next_step": "Rep concurrently scheduled legal review."})
    record = output / "run/run.json"
    run = resume(run, record, client(store, "proposer-north"), client(store, "executor-north"))
    first_receipt = copy.deepcopy(run["receipt"])
    if scenario == "approved-update":
        run = resume(load(record), record, client(store, "proposer-north"), client(store, "executor-north"))
        if run["receipt"] != first_receipt:
            raise AssertionError("Duplicate apply did not return original receipt")
    after = operator.call("inspect", {})
    # Independent postconditions inspect full domain state, not the loop's status claim.
    old, new = before["opportunities"]["opp-101"], after["opportunities"]["opp-101"]
    protected = [key for key in old if key not in {"close_date", "next_step", "version", "history"}]
    if any(old[key] != new[key] for key in protected) or new["version"] != 8:
        raise AssertionError("Protected field or exact version invariant failed")
    applies = [event for event in after["audit"] if event["type"] == "APPLIED"]
    if scenario == "approved-update":
        if new["close_date"] != "2026-11-12" or new["next_step"] != "Send revised agreement by 2026-10-09." or len(applies) != 1 or len(after["receipts"]) != 1:
            raise AssertionError("Approved final-state invariant failed")
    elif new["close_date"] != old["close_date"] or new["next_step"] != "Rep concurrently scheduled legal review." or applies or after["receipts"] or run["state"] != "STALE":
        raise AssertionError("Stale approval changed concurrent state")
    report = {"schema_version": 1, "scenario": scenario, "provider_mode": "scripted", "review_mode": "scripted_synthetic",
              "model_id": None, "model_quality_evaluated": False, "state": run["state"],
              "old_version": old["version"], "new_version": new["version"], "agent_apply_events": len(applies),
              "receipt_count": len(after["receipts"]), "duplicate_receipt_equal": run["receipt"] == first_receipt if scenario == "approved-update" else None,
              "protected_fields_unchanged": True, "final_opportunity": new, "assertions_passed": True}
    save(output / "report.json", report)
    return report


def doctor():
    if sys.version_info[:2] != (3, 11):
        raise ValueError("Select Python 3.11 explicitly")
    versions = {}
    for command in (["java", "-version"], ["javac", "-version"]):
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        text = (result.stdout + result.stderr).strip()
        if result.returncode or not (text.startswith("javac 17.") if command[0] == "javac" else 'version "17.' in text):
            raise ValueError("JDK 17 must resolve on PATH")
        versions[command[0]] = text
    from tools.build import verify_dependency
    verify_dependency()
    if not (ROOT / "build/classes/crm/Main.class").is_file():
        raise ValueError("Build required")
    return {"python": platform.python_version(), "python_executable": sys.executable, "java": versions,
            "provider_mode": "scripted", "live_model_configured": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    initialize = commands.add_parser("init")
    initialize.add_argument("--fixture", type=Path, required=True)
    initialize.add_argument("--store", type=Path, required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--store", type=Path, required=True)
    run_parser.add_argument("--provider", choices=["scripted", "offline-rules", "ollama-http"], default="offline-rules")
    run_parser.add_argument("--task", default="Review the fictional North pipeline")
    run_parser.add_argument("--allow-live-model", action="store_true")
    run_parser.add_argument("--model")
    run_parser.add_argument("--base-url")
    run_parser.add_argument("--scope", choices=["north"], default="north")
    run_parser.add_argument("--as-of", default="2026-10-08")
    run_parser.add_argument("--out", type=Path, required=True)
    for name in ("review", "approve", "reject"):
        review_parser = commands.add_parser(name)
        review_parser.add_argument("--store", type=Path, required=True)
        review_parser.add_argument("--proposal", default="latest")
        review_parser.add_argument("--as-of", default="2026-10-08")
        if name != "review":
            review_parser.add_argument("--reviewer", choices=["manager-north"], required=True)
            review_parser.add_argument("--interactive", action="store_true", required=True)
    resume_parser = commands.add_parser("resume")
    resume_parser.add_argument("--store", type=Path, required=True)
    resume_parser.add_argument("--run", type=Path, required=True)
    cancel_parser = commands.add_parser("cancel")
    cancel_parser.add_argument("--run", type=Path, required=True)
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("--store", type=Path, required=True)
    demo_parser = commands.add_parser("demo")
    demo_parser.add_argument("--scenario", choices=["approved-update", "stale-approval"], required=True)
    demo_parser.add_argument("--provider", choices=["scripted"], required=True)
    demo_parser.add_argument("--scripted-review", action="store_true", required=True)
    demo_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor": dump(doctor())
        elif args.command == "init": dump(client(args.store, "fixture-operator").call("init", {"fixture": strict_json(args.fixture.read_text(encoding="utf-8"))}))
        elif args.command == "run":
            if args.provider == "ollama-http":
                from crm_agent.providers.ollama_http import OllamaHttpProvider
                provider = OllamaHttpProvider(args.base_url or "", args.model, allow_live_model=args.allow_live_model)
            else:
                provider = ScriptedProvider() if args.provider == "scripted" else OfflineRuleProvider()
            run_client = JavaClient(args.store, "proposer-north", args.as_of, cancel_file=args.out / "cancel.requested")
            dump(run_agent(run_client, provider, args.out, args.task))
        elif args.command in {"review", "approve", "reject"}:
            identifier = resolve_proposal(args.store, args.proposal, args.as_of)
            manager = client(args.store, "manager-north", args.as_of)
            packet = manager.call("review", {"proposal_id": identifier})
            print(render(packet))
            if args.command != "review":
                entered = input(f"Enter the exact displayed digest to {args.command}: ").strip()
                if entered != packet["proposal"]["digest"]:
                    raise ValueError("Digest did not match; no review action sent")
                intent = args.store / ("operator-intent-" + identifier + "-" + args.command + ".json")
                dump(review_action(manager, args.command, identifier, entered, intent))
        elif args.command == "resume":
            record = args.run / "run.json" if args.run.is_dir() else args.run
            run = load(record)
            if Path(run["store"]).resolve() != args.store.resolve():
                raise ValueError("Run belongs to a different store")
            # Reconciliation queries must run after cancellation; new mutations inspect the marker in resume.
            dump(resume(run, record, client(args.store, "proposer-north", run["as_of"]), client(args.store, "executor-north", run["as_of"])))
        elif args.command == "cancel":
            record = args.run / "run.json" if args.run.is_dir() else args.run
            dump(request_cancel(record))
        elif args.command == "inspect": dump(client(args.store, "fixture-operator").call("inspect", {}))
        elif args.command == "demo": dump(demo(args.scenario, args.out))
        return 0
    except (DomainError, ProtocolError, ValueError, OSError, AssertionError) as error:
        print(json.dumps({"error": getattr(error, "code", type(error).__name__), "message": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
