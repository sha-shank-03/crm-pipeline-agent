"""Bounded structured operational events. Do not record prompts, arguments or headers."""
import hashlib
import re
import time
from pathlib import Path

from .protocol import encode
from .run_state import save

FIELDS = frozenset({"event", "run_id", "code_version", "prompt_version", "config_version",
    "dataset_version", "mode", "model", "provider", "generation_id", "request_count", "tool_count",
    "input_tokens", "output_tokens", "estimated_cost_usd", "reserved_cost_usd", "actual_cost_usd",
    "cost_status", "retries", "error_code", "state", "duration_seconds", "human_interventions",
    "unsafe_attempts", "unsafe_commits", "owned_child_exited", "peak_working_set_bytes"})
MAX_BYTES = 65536


def clean(value, secrets=()):
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)bearer\s+\S+|(?:sk-proj-|sk-|vck_)[A-Za-z0-9_-]+",
                       "[REDACTED]", value)
        return value[:256]
    if isinstance(value, dict):
        return {key: clean(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [clean(item, secrets) for item in value]
    return value


def version(paths):
    result = hashlib.sha256()
    for path in sorted(map(Path, paths)):
        result.update(path.name.encode())
        result.update(path.read_bytes())
    return result.hexdigest()


class EventLog:
    def __init__(self, path, context, *, secrets=()):
        if set(context) - FIELDS:
            raise ValueError("Unregistered telemetry context")
        self.path = Path(path)
        self.context = clean(context, secrets)
        self.secrets = tuple(secrets)

    def emit(self, event, **values):
        if set(values) - FIELDS:
            raise ValueError("Unregistered telemetry field")
        packet = clean(dict(self.context, event=event, **values), self.secrets)
        raw = encode(packet) + b"\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        prior = self.path.stat().st_size if self.path.exists() else 0
        if prior + len(raw) > MAX_BYTES:
            raise ValueError("Event log size exhausted")
        with self.path.open("ab") as stream:
            stream.write(raw)


def summary(run, *, verified_success=False, unsafe_attempts=0, unsafe_commits=0,
            human_interventions=0):
    ledger = run.get("provider_accounting", {})
    durations = sorted(item["duration_seconds"] for item in ledger.get("attempts", []))
    def quantile(q):
        return durations[min(len(durations) - 1, int((len(durations) - 1) * q + 0.5))] if durations else None
    return {"schema_version": 1, "run_id": run["run_id"], "mode": run["provider_mode"],
            "task_count": 1, "independent_verified_successes": int(verified_success),
            "state": run["state"], "requests": ledger.get("requests", 0), "tools": run["tool_calls"],
            "human_interventions": human_interventions, "unsafe_attempts": unsafe_attempts,
            "unsafe_commits": unsafe_commits, "latency_sample_count": len(durations),
            "active_latency_p50_seconds": quantile(0.5), "active_latency_p95_seconds": quantile(0.95),
            "active_latency_max_seconds": max(durations) if durations else None,
            "estimated_cost_usd": ledger.get("estimated_cost_usd"),
            "reserved_cost_usd": ledger.get("reserved_cost_usd"),
            "actual_cost_usd": None, "actual_cost_coverage": 0,
            "cost_per_success_usd": None, "cost_status": "unknown_or_not_billed_local_double",
            "active_milliseconds": run["active_milliseconds"],
            "approval_wait_included_in_active_time": False,
            "model_quality_evaluated": False}
