"""U1 local-double CRM journey and direct state checks; no live model or external sandbox."""
import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crm_agent.events import summary
from crm_agent.java_client import DomainError, JavaClient, OutcomeUnknown
from crm_agent.loop import run_agent, resume
from crm_agent.operator_action import review_action
from crm_agent.protocol import strict_json
from crm_agent.providers.ai_gateway import AiGatewayProvider
from crm_agent.providers.owned_http import OwnedHttpTransport
from crm_agent.review import render
from crm_agent.run_state import load, save
from tools.gateway_double import GatewayDouble, config


def committed(store):
    paths = sorted(store.glob("generation-*.json"))
    return strict_json(paths[-1].read_text(encoding="utf-8"))


def journey(output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a fresh U1 output directory")
    output.mkdir(parents=True)
    store = output / "crm"
    clients = {role: JavaClient(store, role) for role in
               ["fixture-operator", "proposer-north", "manager-north", "executor-north"]}
    fixture = strict_json((ROOT / "fixtures/dev/approved-update.json").read_text(encoding="utf-8"))
    clients["fixture-operator"].call("init", {"fixture": fixture})
    before = committed(store)
    save(output / "before.json", before)
    proposer, manager, executor = [clients[role] for role in ["proposer-north", "manager-north", "executor-north"]]
    with GatewayDouble() as server:
        transport = OwnedHttpTransport(url=server.url, local_double=True,
                                       cancel_file=output / "run/cancel.requested")
        provider = AiGatewayProvider(config(), transport, local_double=True)
        run = run_agent(proposer, provider, output / "run")
        record = output / "run/run.json"
        assert run["state"] == "WAITING_APPROVAL"
        assert committed(store)["opportunities"] == before["opportunities"]
        assert committed(store)["approvals"] == {}
        packet = manager.call("review", {"proposal_id": run["proposal_id"]})
        (output / "review.txt").write_text(render(packet) + "\n", encoding="utf-8")
        digest = packet["proposal"]["digest"]
        assert digest == run["proposal_digest"]
        waiting = resume(load(record), record, proposer, executor)
        assert waiting["state"] == "WAITING_APPROVAL"
        guarded = committed(store)
        try:
            manager.call("approve", {"proposal_id": run["proposal_id"], "digest": "0" * 64})
        except DomainError as error:
            wrong_digest_code = error.code
        else:
            raise AssertionError("Bad digest was approved")
        assert committed(store) == guarded
        review_action(manager, "approve", run["proposal_id"], digest, output / "operator-intent.json")
        provider.event("operator_approved", human_interventions=1, state="WAITING_APPROVAL")
        class LoseApplyResponse:
            @property
            def active_deadline(self):
                return executor.active_deadline
            @active_deadline.setter
            def active_deadline(self, value):
                executor.active_deadline = value
            def call(self, operation, arguments, request_id=None):
                result = executor.call(operation, arguments, request_id)
                if operation == "apply":
                    raise OutcomeUnknown(request_id)  # Actual Java commit, authored response-loss injection.
                return result
        uncertain = resume(load(record), record, proposer, LoseApplyResponse())
        assert uncertain["state"] == "OUTCOME_UNKNOWN"
        after_commit = committed(store)
        assert len(after_commit["receipts"]) == 1
        recovered = resume(load(record), record, proposer, executor)
        assert recovered["state"] == "APPLIED"
        final = committed(store)
        assert final == after_commit
        duplicate = resume(load(record), record, proposer, executor)
        assert duplicate["receipt"] == recovered["receipt"]
        assert committed(store) == final
        old, new = before["opportunities"]["opp-101"], final["opportunities"]["opp-101"]
        assert new["version"] == old["version"] + 1
        assert new["close_date"] == "2026-11-12"
        assert new["next_step"] == "Send revised agreement by 2026-10-09."
        assert all(old[key] == new[key] for key in old if key not in {"version", "close_date", "next_step", "history"})
        assert len(new["history"]) == len(old["history"]) + 1
        assert sum(event["type"] == "APPLIED" for event in final["audit"]) == 1
        assert len(final["receipts"]) == 1
        assert len(server.requests) == 5
        assert server.authorizations_seen == 0
        assert all(item["owned_child_exited"] for item in transport.records)
        assert recovered["provider_accounting"] == run["provider_accounting"]
        assert recovered["provider_turns"] == run["provider_turns"]
        assert recovered["active_milliseconds"] >= run["active_milliseconds"]
        # A restarted provider sees persisted accounting, never a fresh request allowance.
        rebound = AiGatewayProvider(config(), transport, local_double=True)
        rebound.bind(recovered, record)
        assert rebound.run["provider_accounting"]["requests"] == 5
        save(output / "wire.json", {"mode": "local-double", "policy": "authored-observation-script",
                                    "requests": server.requests})
        provider.event("independent_state_checked", state="APPLIED", unsafe_commits=0,
                       unsafe_attempts=1, human_interventions=1)
        report = summary(recovered, verified_success=True, unsafe_attempts=1,
                         unsafe_commits=0, human_interventions=1)
        report.update(double_policy="authored-observation-script", wrong_digest_error=wrong_digest_code,
                      apply_response_loss_injection=True, receipt_count=1, agent_apply_events=1,
                      request_count_preserved_across_resume=True, final_version=new["version"],
                      approval_mode="explicit_exact_digest_programmatic_test_operator",
                      live_model_calls=0, genuine_sandbox_integration=False,
                      transport_processes=transport.records,
                      java_processes=[item for client in clients.values() for item in client.process_stats],
                      main_acceptance="pending_independent_review")
    report["owned_server_and_handlers_exited"] = True
    save(output / "after.json", final)
    save(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11):
        raise ValueError("Select Python 3.11 explicitly")
    report = journey(args.out)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, AssertionError, DomainError, OSError) as error:
        print(json.dumps({"error": getattr(error, "code", type(error).__name__),
                          "message": str(error)}), file=sys.stderr)
        raise SystemExit(2)
