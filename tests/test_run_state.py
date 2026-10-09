"""Deterministic persistence/recovery faults; no Java, HTTP, providers or case scoring."""
import copy
import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crm_agent import run_state
from crm_agent.loop import DEFAULT_LIMITS, resume
from crm_agent.protocol import ProtocolError

ROOT = Path(__file__).resolve().parents[1]


def denial(code=5):
    error = PermissionError(errno.EACCES, "invented Windows replacement denial")
    error.winerror = code
    return error


def waiting():
    return {"schema_version": 1, "state": "WAITING_APPROVAL", "messages": [],
            "limits": dict(DEFAULT_LIMITS), "counters": {"note_reads": 0, "stage_attempts": 0, "errors": 0, "reconcile_calls": 0},
            "active_milliseconds": 0, "tool_calls": 0, "provider_turns": 0,
            "proposal_id": "invented-proposal", "proposal_digest": "d" * 64,
            "pending_stage": None, "pending_apply": None, "receipt": None}


class CommittedDouble:
    """Only observes the controller/key contract; does not simulate Java policy."""
    def __init__(self, path):
        self.path, self.calls, self.apply_keys, self.receipt = path, [], [], None

    def call(self, operation, arguments, key=None):
        self.calls.append((operation, copy.deepcopy(arguments), key))
        if operation == "review":
            return {"proposal": {"status": "APPROVED"}, "approval": {"id": "invented-approval"}}
        pending = run_state.load(self.path)["pending_apply"]
        if operation == "operation_result":
            if arguments != {"operation": "apply", "request_id": pending["request_id"], "args": pending["arguments"]}:
                raise AssertionError("Query must use the intact original checkpoint")
            return {"found": self.receipt is not None, "result": {"receipt": self.receipt} if self.receipt else None}
        if operation == "apply":
            if key != pending["request_id"] or arguments != pending["arguments"]:
                raise AssertionError("Write intent/key was not durably saved")
            if self.receipt is not None:
                raise AssertionError("Unexpected second apply rather than receipt query")
            self.apply_keys.append(key)
            self.receipt = {"action_key": key, "version": 8, "event_id": "invented-one-effect"}
            return {"receipt": self.receipt}
        raise AssertionError("No new approval/provider/other authority may be called")


class CheckpointPersistenceTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "work").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "work")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "run.json"
        self.old = {"schema_version": 1, "state": "APPLYING", "pending_apply": {"request_id": "original-key"}}
        run_state.save(self.path, self.old)
        self.old_bytes = self.path.read_bytes()
        self.replace = run_state.os.replace
        self.new = {"schema_version": 1, "state": "APPLIED", "receipt": {"action_key": "original-key"}}

    def test_transient_windows_denials_reuse_same_fsynced_temporary(self):
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                run_state.save(self.path, self.old)
                seen = []
                def replace(source, target):
                    seen.append(str(source))
                    self.assertEqual(Path(source).parent, self.path.parent)
                    self.assertEqual(json.loads(Path(source).read_text(encoding="utf-8")), self.new)
                    if len(seen) < 4:
                        self.assertEqual(self.path.read_bytes(), self.old_bytes)
                        raise denial(code)
                    return self.replace(source, target)
                with patch.object(run_state, "_WINDOWS", True), patch.object(run_state.os, "replace", side_effect=replace), patch.object(run_state.time, "sleep") as sleep:
                    run_state.save(self.path, self.new)
                self.assertEqual(len(set(seen)), 1)
                self.assertEqual(sleep.call_args_list, [unittest.mock.call(0.02), unittest.mock.call(0.05), unittest.mock.call(0.1)])
                self.assertEqual(run_state.load(self.path), self.new)
                self.assertEqual(list(self.path.parent.glob(".run-*.tmp")), [])

    def test_exhaustion_preserves_first_error_old_bytes_and_cleans_temporary(self):
        first, subsequent = denial(), denial(32)
        with patch.object(run_state, "_WINDOWS", True), patch.object(run_state.os, "replace", side_effect=[first, subsequent, subsequent, subsequent]) as replace, patch.object(run_state.time, "sleep") as sleep:
            with self.assertRaises(PermissionError) as caught:
                run_state.save(self.path, self.new)
        self.assertIs(caught.exception, first)
        self.assertEqual(replace.call_count, 4)
        self.assertEqual(sleep.call_count, 3)
        self.assertIn("four attempts", first.__notes__[0])
        self.assertEqual(self.path.read_bytes(), self.old_bytes)
        self.assertEqual(list(self.path.parent.glob(".run-*.tmp")), [])

    def test_nonretryable_errors_propagate_immediately(self):
        for error in (OSError(errno.ENOSPC, "full"), PermissionError(errno.EACCES, "no native code"), denial(87)):
            with self.subTest(error=error), patch.object(run_state, "_WINDOWS", True), patch.object(run_state.os, "replace", side_effect=error) as replace, patch.object(run_state.time, "sleep") as sleep:
                with self.assertRaises(OSError) as caught:
                    run_state.save(self.path, self.new)
                self.assertIs(caught.exception, error)
                self.assertEqual(replace.call_count, 1)
                sleep.assert_not_called()
                self.assertEqual(self.path.read_bytes(), self.old_bytes)
                self.assertEqual(list(self.path.parent.glob(".run-*.tmp")), [])

    def test_nonwindows_access_denial_is_not_retried(self):
        error = denial()
        with patch.object(run_state, "_WINDOWS", False), patch.object(run_state.os, "replace", side_effect=error) as replace, patch.object(run_state.time, "sleep") as sleep:
            with self.assertRaises(PermissionError) as caught:
                run_state.save(self.path, self.new)
        self.assertIs(caught.exception, error)
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.old_bytes)

    def test_a_permanent_error_after_transient_denial_is_not_hidden(self):
        error = OSError(errno.ENOSPC, "persistent disk failure")
        with patch.object(run_state, "_WINDOWS", True), patch.object(run_state.os, "replace", side_effect=[denial(), error]) as replace, patch.object(run_state.time, "sleep") as sleep:
            with self.assertRaises(OSError) as caught:
                run_state.save(self.path, self.new)
        self.assertIs(caught.exception, error)
        self.assertEqual(replace.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(self.path.read_bytes(), self.old_bytes)

    def test_cleanup_failure_does_not_mask_primary_replacement_failure(self):
        primary, cleanup = denial(), OSError(errno.EACCES, "temporary cleanup denied")
        with patch.object(run_state, "_WINDOWS", True), patch.object(run_state.os, "replace", side_effect=primary), patch.object(run_state.time, "sleep"), patch.object(run_state.os, "unlink", side_effect=cleanup):
            with self.assertRaises(PermissionError) as caught:
                run_state.save(self.path, self.new)
        self.assertIs(caught.exception, primary)
        self.assertTrue(any("temporary cleanup denied" in n for n in primary.__notes__))
        self.assertEqual(self.path.read_bytes(), self.old_bytes)
        self.assertEqual(len(list(self.path.parent.glob(".run-*.tmp"))), 1)

    def test_serialization_failure_keeps_old_checkpoint_and_primary_error(self):
        with patch.object(run_state.os, "unlink", side_effect=OSError("cleanup denied")), patch.object(run_state.os, "replace") as replace:
            with self.assertRaises(ProtocolError) as caught:
                run_state.save(self.path, {"schema_version": 1, "invalid": float("nan")})
        replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.old_bytes)
        self.assertTrue(any("cleanup denied" in n for n in caught.exception.__notes__))

    def test_fsync_failure_never_replaces_target(self):
        error = OSError(errno.ENOSPC, "fsync failure")
        with patch.object(run_state.os, "fsync", side_effect=error), patch.object(run_state.os, "replace") as replace:
            with self.assertRaises(OSError) as caught:
                run_state.save(self.path, self.new)
        self.assertIs(caught.exception, error)
        replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.old_bytes)
        self.assertEqual(list(self.path.parent.glob(".run-*.tmp")), [])

    def test_failed_first_creation_has_no_target_or_in_place_fallback(self):
        self.path.unlink()
        with patch.object(run_state, "_WINDOWS", True), patch.object(run_state.os, "replace", side_effect=denial()), patch.object(run_state.time, "sleep"):
            with self.assertRaises(PermissionError):
                run_state.save(self.path, self.new)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.glob(".run-*.tmp")), [])


class CheckpointRecoveryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "work").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "work")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "run.json"
        self.run = waiting()
        run_state.save(self.path, self.run)
        self.executor = CommittedDouble(self.path)
        self.replace = run_state.os.replace

    def test_committed_effect_with_exhausted_checkpoint_recovers_original_receipt(self):
        original_error = denial()
        def replacement(source, target):
            if json.loads(Path(source).read_text(encoding="utf-8"))["state"] == "APPLIED":
                raise original_error
            return self.replace(source, target)
        with patch.object(run_state, "_WINDOWS", True), patch.object(run_state.os, "replace", side_effect=replacement), patch.object(run_state.time, "sleep"):
            with self.assertRaises(PermissionError) as caught:
                resume(self.run, self.path, self.executor, self.executor)
        self.assertIs(caught.exception, original_error)
        durable = run_state.load(self.path)
        self.assertEqual(durable["state"], "APPLYING")
        self.assertIsNone(durable["receipt"])
        key = durable["pending_apply"]["request_id"]
        self.assertEqual(self.executor.apply_keys, [key])
        prior_calls = len(self.executor.calls)
        with patch("crm_agent.loop.uuid.uuid4", side_effect=AssertionError("No new action key")):
            final = resume(durable, self.path, self.executor, self.executor)
            repeated = resume(run_state.load(self.path), self.path, self.executor, self.executor)
        self.assertEqual([c[0] for c in self.executor.calls[prior_calls:]], ["operation_result", "operation_result"])
        self.assertEqual(self.executor.apply_keys, [key])
        self.assertEqual(final["receipt"], self.executor.receipt)
        self.assertEqual(repeated["receipt"], self.executor.receipt)
        self.assertEqual(run_state.load(self.path)["state"], "APPLIED")
        self.assertEqual(run_state.load(self.path)["pending_apply"], durable["pending_apply"])

    def test_transient_final_checkpoint_denial_does_not_repeat_apply(self):
        failures = []
        def replacement(source, target):
            if json.loads(Path(source).read_text(encoding="utf-8"))["state"] == "APPLIED" and len(failures) < 2:
                failures.append(denial(32))
                raise failures[-1]
            return self.replace(source, target)
        with patch.object(run_state, "_WINDOWS", True), patch.object(run_state.os, "replace", side_effect=replacement), patch.object(run_state.time, "sleep"):
            final = resume(self.run, self.path, self.executor, self.executor)
        self.assertEqual(final["state"], "APPLIED")
        self.assertEqual(len(self.executor.apply_keys), 1)
        self.assertEqual(run_state.load(self.path)["receipt"], self.executor.receipt)

    def test_missing_receipt_queries_then_retries_only_original_action(self):
        self.run.update(state="APPLYING", pending_apply={"request_id": "fixed-original-key", "arguments": {"proposal_id": "invented-proposal", "approval_id": "invented-approval", "digest": "d" * 64}})
        run_state.save(self.path, self.run)
        with patch("crm_agent.loop.uuid.uuid4", side_effect=AssertionError("No new key")):
            final = resume(run_state.load(self.path), self.path, self.executor, self.executor)
        self.assertEqual([c[0] for c in self.executor.calls], ["operation_result", "apply"])
        self.assertEqual(self.executor.apply_keys, ["fixed-original-key"])
        self.assertEqual(final["receipt"]["action_key"], "fixed-original-key")

    def test_failed_operation_query_preserves_original_key_without_apply(self):
        self.run.update(state="APPLYING", pending_apply={"request_id": "fixed-original-key", "arguments": {"proposal_id": "p", "approval_id": "a", "digest": "d" * 64}})
        run_state.save(self.path, self.run)
        calls = []
        class Unavailable:
            def call(inner, operation, arguments, key=None):
                calls.append(operation)
                raise OSError("invented unavailable query")
        final = resume(run_state.load(self.path), self.path, Unavailable(), Unavailable())
        self.assertEqual(calls, ["operation_result"])
        self.assertEqual(final["state"], "OUTCOME_UNKNOWN")
        self.assertEqual(run_state.load(self.path)["pending_apply"], self.run["pending_apply"])

    def test_save_failure_before_dispatch_starts_no_authority_call(self):
        error = denial()
        with patch.object(run_state, "_WINDOWS", True), patch.object(run_state.os, "replace", side_effect=error), patch.object(run_state.time, "sleep"):
            with self.assertRaises(PermissionError):
                resume(self.run, self.path, self.executor, self.executor)
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(run_state.load(self.path)["state"], "WAITING_APPROVAL")
        self.assertIsNone(run_state.load(self.path)["pending_apply"])
