"""Pure telemetry controls with synthetic sentinel secrets only."""
import json
from pathlib import Path
import tempfile
import unittest

from crm_agent.events import EventLog, summary

ROOT = Path(__file__).resolve().parents[1]


class EventTests(unittest.TestCase):
    def test_known_secret_and_bearer_patterns_redacted(self):
        (ROOT / "work").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            path = Path(directory) / "events.jsonl"
            secret = "SYNTHETIC_SENTINEL_123"
            log = EventLog(path, {"run_id": "test", "mode": "local-double"}, secrets=(secret,))
            log.emit("failure", error_code=secret + " Bearer NOT_A_REAL_TOKEN sk-proj-FAKE")
            text = path.read_text()
            self.assertNotIn(secret, text)
            self.assertNotIn("NOT_A_REAL_TOKEN", text)
            self.assertNotIn("sk-proj-FAKE", text)
            self.assertIn("[REDACTED]", text)
            with self.assertRaises(ValueError): log.emit("bad", authorization=secret)

    def test_unknown_cost_not_reported_as_zero_or_success_cost(self):
        run = {"run_id": "test", "provider_mode": "local-double", "tool_calls": 0,
               "state": "HANDOFF", "active_milliseconds": 10,
               "provider_accounting": {"requests": 1, "attempts": [{"duration_seconds": 0.1}],
                                       "reserved_cost_usd": "0.01", "actual_cost_usd": None}}
        value = summary(run)
        self.assertIsNone(value["actual_cost_usd"])
        self.assertIsNone(value["cost_per_success_usd"])
        self.assertEqual(value["independent_verified_successes"], 0)
        self.assertEqual(value["latency_sample_count"], 1)

    def test_bounded_log_and_success_needs_external_verifier_input(self):
        (ROOT / "work").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            path = Path(directory) / "events.jsonl"
            path.write_bytes(b" " * 65536)
            log = EventLog(path, {"mode": "local-double"})
            with self.assertRaises(ValueError): log.emit("overflow")
        run = {"run_id": "r", "provider_mode": "local-double", "tool_calls": 5,
               "state": "APPLIED", "active_milliseconds": 1}
        self.assertEqual(summary(run)["independent_verified_successes"], 0)
        self.assertEqual(summary(run, verified_success=True)["independent_verified_successes"], 1)
