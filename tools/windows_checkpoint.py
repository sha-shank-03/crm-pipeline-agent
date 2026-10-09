"""Leased Windows sharing-denial/real-Java recovery checks; source preparation is pure."""
import argparse
import copy
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crm_agent import run_state
from crm_agent.java_client import JavaClient
from crm_agent.loop import run_agent, resume
from crm_agent.scripted import ScriptedProvider


class OwnedHandle:
    def __init__(self, path):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                          wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.lock = threading.Lock()
        # Owned read handle allows reads/writes but denies delete/rename sharing.
        self.handle = self.kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 128, None)
        if self.handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        with self.lock:
            if self.handle is not None:
                if not self.kernel.CloseHandle(self.handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handle = None


def state(store):
    return json.loads(sorted(store.glob("generation-*.json"))[-1].read_text(encoding="utf-8"))


def sharing_case(directory, release_on_first_denial):
    directory.mkdir()
    path = directory / "run.json"
    old = {"schema_version": 1, "state": "APPLYING", "pending_apply": {"request_id": "owned-original-key"}}
    new = {**old, "state": "APPLIED"}
    run_state.save(path, old)
    old_bytes = path.read_bytes()
    handle = OwnedHandle(path)
    seen, release_errors = [], []
    denied = threading.Event()
    original = run_state.os.replace
    def observed(source, target):
        try:
            return original(source, target)
        except OSError as error:
            seen.append({"type": type(error).__name__, "winerror": getattr(error, "winerror", None),
                         "old_bytes_intact": path.read_bytes() == old_bytes})
            denied.set()
            raise
    thread = None
    if release_on_first_denial:
        def release():
            try:
                if not denied.wait(5):
                    raise AssertionError("No native sharing denial observed")
                handle.close()
            except Exception as error:
                release_errors.append(str(error))
        thread = threading.Thread(target=release, daemon=True)
        thread.start()
    failure = None
    try:
        with patch.object(run_state.os, "replace", side_effect=observed):
            try:
                run_state.save(path, new)
            except PermissionError as error:
                failure = error
    finally:
        handle.close()
        if thread:
            thread.join(6)
    assert not release_errors and (not thread or not thread.is_alive())
    assert seen and all(x["winerror"] in {5, 32, 33} and x["old_bytes_intact"] for x in seen)
    if release_on_first_denial:
        assert failure is None and run_state.load(path) == new
    else:
        assert failure is not None and len(seen) == 4 and path.read_bytes() == old_bytes
    assert not list(directory.glob(".run-*.tmp"))
    return {"native_denials": seen, "recovered": release_on_first_denial,
            "old_checkpoint_preserved_on_exhaustion": not release_on_first_denial,
            "temporary_files_remaining": 0}


def committed_case(directory):
    directory.mkdir()
    store = directory / "crm"
    fixture = json.loads((ROOT / "fixtures/dev/approved-update.json").read_text(encoding="utf-8"))
    operator = JavaClient(store, "fixture-operator")
    operator.call("init", {"fixture": fixture})
    before = state(store)
    proposer = JavaClient(store, "proposer-north")
    run = run_agent(proposer, ScriptedProvider(), directory / "run")
    assert run["state"] == "WAITING_APPROVAL"
    JavaClient(store, "manager-north").call("approve", {"proposal_id": run["proposal_id"], "digest": run["proposal_digest"]})
    path = directory / "run/run.json"
    calls, denials, handles, locked_bytes = [], [], [], []
    executor = JavaClient(store, "executor-north")
    class ObservedExecutor:
        def call(self, operation, arguments, request_id=None):
            calls.append({"operation": operation, "arguments": copy.deepcopy(arguments), "request_id": request_id})
            result = executor.call(operation, arguments, request_id)
            if operation == "apply":
                assert not handles, "No second apply may occur"
                handles.append(OwnedHandle(path))
                locked_bytes.append(path.read_bytes())
            return result
    observed_executor = ObservedExecutor()
    original = run_state.os.replace
    def replacement(source, target):
        try:
            return original(source, target)
        except OSError as error:
            denials.append({"type": type(error).__name__, "winerror": getattr(error, "winerror", None)})
            raise
    error_seen = None
    try:
        with patch.object(run_state.os, "replace", side_effect=replacement):
            try:
                resume(run, path, proposer, observed_executor)
            except PermissionError as error:
                error_seen = error
        assert error_seen is not None and len(denials) == 4
        assert all(x["winerror"] in {5, 32, 33} for x in denials)
        assert path.read_bytes() == locked_bytes[0]
        durable = run_state.load(path)
        assert durable["state"] == "APPLYING" and durable["receipt"] is None
        key = durable["pending_apply"]["request_id"]
        committed = state(store)
        assert key in committed["receipts"] and committed["opportunities"]["opp-101"]["version"] == 8
        run_state.save(directory / "preserved-applying.json", durable)
        run_state.save(directory / "preserved-committed.json", committed)
    finally:
        for handle in handles:
            handle.close()
    call_count = len(calls)
    final = resume(run_state.load(path), path, proposer, observed_executor)
    duplicate = resume(run_state.load(path), path, proposer, observed_executor)
    after = state(store)
    assert [c["operation"] for c in calls[call_count:]] == ["operation_result", "operation_result"]
    assert all(c["arguments"]["request_id"] == key and c["arguments"]["args"] == durable["pending_apply"]["arguments"] for c in calls[call_count:])
    assert sum(c["operation"] == "apply" for c in calls) == 1
    assert final["receipt"] == duplicate["receipt"] == committed["receipts"][key]
    assert final["state"] == duplicate["state"] == "APPLIED" and after == committed
    target = after["opportunities"]["opp-101"]
    assert target["close_date"] == "2026-11-12" and target["next_step"] == "Send revised agreement by 2026-10-09."
    assert len(after["receipts"]) == 1 and sum(e["type"] == "APPLIED" for e in after["audit"]) == 1
    assert after["accounts"] == before["accounts"] and after["notes"] == before["notes"]
    assert {k:v for k,v in after["opportunities"].items() if k != "opp-101"} == {k:v for k,v in before["opportunities"].items() if k != "opp-101"}
    protected = set(before["opportunities"]["opp-101"]) - {"close_date", "next_step", "version", "history"}
    assert all(target[k] == before["opportunities"]["opp-101"][k] for k in protected)
    assert not list(path.parent.glob(".run-*.tmp"))
    return {"native_denials": denials, "original_action_key": key, "calls": calls,
            "effect_count": 1, "receipt_count": 1, "version": 8, "query_before_retry": True,
            "new_approval": False, "duplicate_apply": False, "domain_unchanged_after_queries": True,
            "committed_sha256": hashlib.sha256(json.dumps(committed, sort_keys=True).encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt":
        raise ValueError("Owned Windows sharing tests require Windows; do not claim a skipped test passed")
    output = args.out.resolve()
    if output.exists():
        raise ValueError("Choose a fresh evidence path; prior evidence is preserved")
    from tools.build import verify_dependency
    verify_dependency()  # cached pins only; no fetch fallback
    if not (ROOT / "build/classes/crm/Main.class").is_file():
        raise ValueError("Previously built Java classes required")
    output.mkdir(parents=True)
    report = {"complete": False, "model_calls": 0, "conditions": [
        {"name": name, "status": "not_executed"} for name in ("native-transient", "native-exhaustion", "committed-effect-recovery")]}
    for index, function in enumerate((lambda: sharing_case(output / "transient", True),
                                      lambda: sharing_case(output / "exhaustion", False),
                                      lambda: committed_case(output / "committed"))):
        run_state.save(output / "report.json", report)
        try:
            report["conditions"][index].update(status="passed", evidence=function())
        except Exception as error:
            report["conditions"][index].update(status="error", error={"type": type(error).__name__, "message": str(error)})
            run_state.save(output / "report.json", report)
            raise
    report["complete"] = True
    run_state.save(output / "report.json", report)
    print(json.dumps({"passed_conditions": 3, "complete": True, "model_calls": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
