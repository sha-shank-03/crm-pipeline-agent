"""Leased real process concurrency/interruption, lock and storage tests."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import subprocess
import threading
import time
import uuid
from unittest.mock import patch
import os

from crm_agent.java_client import DomainError, JavaClient, OutcomeUnknown
from crm_agent.loop import resume
from crm_agent.run_state import load, save
from tests.m2_support import M2Fixture, committed_state, java_command

class M2RecoveryTests(M2Fixture):
    def test_simultaneous_duplicate_delivery_has_one_receipt(self):
        self.approve()
        key = str(uuid.uuid4())
        gate = threading.Barrier(2)
        def deliver():
            gate.wait(timeout=3)
            return JavaClient(self.store, "executor-north").call("apply", self.apply_args, key)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: deliver(), range(2)))
        self.assertEqual(results[0], results[1])
        final = committed_state(self.store)
        self.assertEqual(final["opportunities"]["opp-101"]["version"], 8)
        self.assertEqual(sum(event["type"] == "APPLIED" for event in final["audit"]), 1)
        self.assertEqual(len(final["receipts"]), 1)

    def test_simultaneous_competing_proposals_have_one_winner(self):
        self.approve()
        second = self.proposer.call("stage_proposal", self.stage_args)["proposal"]
        approval = self.manager.call("approve", {"proposal_id": second["id"], "digest": second["digest"]})["approval"]
        other = {"proposal_id": second["id"], "approval_id": approval["id"], "digest": second["digest"]}
        gate = threading.Barrier(2)
        def deliver(arguments):
            gate.wait(timeout=3)
            try: return JavaClient(self.store, "executor-north").call("apply", arguments)
            except DomainError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(deliver, [self.apply_args, other]))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertEqual(sum(result == "VERSION_CONFLICT" for result in results), 1)
        final = committed_state(self.store)
        self.assertEqual(final["opportunities"]["opp-101"]["version"], 8)
        self.assertEqual(len(final["receipts"]), 1)
        self.assertEqual(sum(item["type"] == "APPLIED" for item in final["audit"]), 1)

    def interrupted_apply(self, failpoint, expected_exit):
        self.approve()
        key = str(uuid.uuid4())
        self.run.update(state="APPLYING", pending_apply={"request_id": key, "arguments": self.apply_args})
        save(self.record, self.run)
        before = committed_state(self.store)
        crashed = self.raw(self.envelope("apply", self.apply_args, key), "executor-north", failpoint)
        self.assertEqual(crashed.returncode, expected_exit)
        self.assertEqual(crashed.stdout, b"")
        after_crash = committed_state(self.store)
        if failpoint == "before-rename":
            self.assertEqual(after_crash, before)
            self.assertTrue(list(self.store.glob(".snapshot-*.tmp")))
        else:
            self.assertEqual(after_crash["opportunities"]["opp-101"]["version"], 8)
            self.assertIn(key, after_crash["receipts"])
        run = resume(load(self.record), self.record, self.proposer, self.executor)
        self.assertEqual(run["state"], "APPLIED")
        self.assertEqual(run["pending_apply"]["request_id"], key)
        final = committed_state(self.store)
        self.assertEqual(len(final["receipts"]), 1)
        self.assertEqual(sum(event["type"] == "APPLIED" for event in final["audit"]), 1)
        if failpoint == "after-rename": self.assertEqual(final, after_crash)
        self.assertEqual(resume(load(self.record), self.record, self.proposer, self.executor)["receipt"], run["receipt"])
        self.assertEqual(committed_state(self.store), final)

    def test_owned_termination_before_rename_restarts_with_original_key(self):
        self.interrupted_apply("before-rename", 71)

    def test_lost_reply_after_rename_reconciles_without_new_generation(self):
        self.interrupted_apply("after-rename", 72)

    def holder(self):
        ready = self.output / "holder.ready"
        process = subprocess.Popen(java_command(self.store, main="crm.LockHolder") + [str(self.store), str(ready)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def stop():
            if process.poll() is None: process.kill()
            process.communicate(timeout=3)
        self.addCleanup(stop)
        deadline = time.monotonic() + 3
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(ready.exists(), "Owned lock holder did not become ready")
        return process

    def test_occupied_lock_rejects_mutation_without_new_generation(self):
        before = committed_state(self.store)
        process = self.holder()
        try:
            with self.assertRaises(DomainError) as busy:
                self.proposer.call("stage_proposal", self.stage_args)
            self.assertEqual(busy.exception.code, "STORE_BUSY")
            self.assertEqual(committed_state(self.store), before)
        finally:
            process.kill(); process.communicate(timeout=3)

    def test_cancel_owned_process_preserves_uncertainty_and_no_partial_write(self):
        process = self.holder()
        marker = self.output / "cancel.requested"
        client = JavaClient(self.store, "proposer-north", cancel_file=marker)
        timer = threading.Timer(0.3, lambda: marker.write_text("cancel"))
        timer.start()
        try:
            with self.assertRaises(OutcomeUnknown): client.call("stage_proposal", self.stage_args)
        finally:
            timer.join(timeout=2)
            process.kill(); process.communicate(timeout=3)
        self.assertEqual(committed_state(self.store)["opportunities"]["opp-101"]["version"], 7)
        self.assertEqual(len(committed_state(self.store)["proposals"]), 1)
        self.assertEqual(committed_state(self.store)["receipts"], {})

    def test_corrupt_latest_generation_fails_without_rollback(self):
        latest = sorted(self.store.glob("generation-*.json"))[-1]
        latest.write_text('{"broken":')
        paths = sorted(self.store.glob("generation-*.json"))
        with self.assertRaises(DomainError) as corrupt: self.proposer.call("list_opportunities", {})
        self.assertEqual(corrupt.exception.code, "STORE_CORRUPT")
        self.assertEqual(sorted(self.store.glob("generation-*.json")), paths)
        self.assertEqual(latest.read_text(), '{"broken":')

    def test_generation_capacity_refuses_write(self):
        snapshot = committed_state(self.store)
        for generation in range(snapshot["generation"] + 1, 500):
            copied = copy.deepcopy(snapshot); copied["generation"] = generation
            (self.store / f"generation-{generation:06d}.json").write_text(json.dumps(copied))
        self.unchanged_error(self.proposer, "stage_proposal", self.stage_args, "STORE_CAPACITY")

    def test_total_storage_capacity_refuses_before_commit(self):
        # A sparse file tests the logical generated footprint without a 100-MiB download.
        with (self.store / ".orphan.tmp").open("wb") as stream:
            stream.seek(100 * 1024 * 1024 - 1); stream.write(b"x")
        self.unchanged_error(self.proposer, "stage_proposal", self.stage_args, "STORE_CAPACITY")

    def test_snapshot_size_limit_fails_closed(self):
        latest = sorted(self.store.glob("generation-*.json"))[-1]
        with latest.open("wb") as stream:
            stream.seek(10 * 1024 * 1024); stream.write(b"x")
        with self.assertRaises(DomainError) as error: self.proposer.call("list_opportunities", {})
        self.assertEqual(error.exception.code, "STORE_CORRUPT")

    def test_unsupported_atomic_move_has_no_initial_generation(self):
        store = self.output / "unsupported"
        payload = self.envelope("init", {"fixture": self.fixture})
        process = subprocess.run(java_command(store, "fixture-operator", "atomic-unsupported"), input=payload, capture_output=True, timeout=8)
        response = json.loads(process.stdout)
        self.assertEqual(response["error"]["code"], "ATOMIC_MOVE_UNSUPPORTED")
        self.assertEqual(list(store.glob("generation-*.json")), [])

    def test_unavailable_java_startup_has_no_store_change(self):
        before = committed_state(self.store)
        with patch.dict(os.environ, {"PATH": ""}):
            with self.assertRaises(DomainError) as missing: self.proposer.call("stage_proposal", self.stage_args)
        self.assertEqual(missing.exception.code, "BUILD_REQUIRED")
        self.assertEqual(committed_state(self.store), before)

    def test_read_only_busy_operation_retries_once(self):
        process = self.holder()
        try:
            with patch.object(self.proposer, "_call", wraps=self.proposer._call) as calls:
                with self.assertRaises(DomainError) as busy: self.proposer.call("list_opportunities", {})
                self.assertEqual(busy.exception.code, "STORE_BUSY")
                self.assertEqual(calls.call_count, 2)
        finally:
            process.kill(); process.communicate(timeout=3)
