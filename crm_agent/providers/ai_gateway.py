"""Gateway-native function calls; local doubles are a separate, credential-free mode."""
from decimal import Decimal, InvalidOperation
import hashlib
import math
import re
import time

from ..events import EventLog, clean, version
from ..java_client import ROOT
from ..protocol import ProtocolError, encode, strict_json
from ..provider import Terminal, ToolCall
from ..run_state import save
from ..tools import validate_call
from .ollama_http import FINISH, SYSTEM_PROMPT
from .owned_http import GatewayError, OwnedHttpTransport

MODEL = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]{1,128}")
IDENTIFIER = re.compile(r"(?:call_|gen_|chatcmpl-)[A-Za-z0-9_-]{1,120}")
CONFIG_FIELDS = {"schema_version", "model", "provider", "max_requests", "max_output_tokens",
                 "input_token_ceiling", "allocation_usd", "reserve_per_request_usd",
                 "live_enabled", "dataset_version"}


def parse_response(payload, model, *, output_cap=2048, input_cap=40960):
    try:
        value = strict_json(payload.decode("utf-8", errors="strict"))
        if not isinstance(value, dict) or value.get("model") != model:
            raise GatewayError("MODEL_MISMATCH")
        generation = value.get("id")
        if not isinstance(generation, str) or not IDENTIFIER.fullmatch(generation) or not generation.startswith(("gen_", "chatcmpl-")):
            raise GatewayError("INVALID_GENERATION_ID")
        choices = value.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise GatewayError("INVALID_RESPONSE")
        choice = choices[0]
        message = choice.get("message")
        if choice.get("finish_reason") != "tool_calls" or not isinstance(message, dict) or message.get("role") != "assistant":
            raise GatewayError("INCOMPLETE_RESPONSE")
        calls = message.get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
            raise GatewayError("MULTIPLE_OR_MISSING_CALLS")
        call = calls[0]
        if call.get("type") != "function" or not isinstance(call.get("id"), str) or not IDENTIFIER.fullmatch(call["id"]) or not call["id"].startswith("call_"):
            raise GatewayError("INVALID_CALL_ID")
        function = call.get("function")
        if not isinstance(function, dict) or set(function) != {"name", "arguments"} or not isinstance(function["arguments"], str):
            raise GatewayError("MALFORMED_ARGUMENTS")
        args = strict_json(function["arguments"])
        name = function["name"]
        if not isinstance(args, dict):
            raise GatewayError("MALFORMED_ARGUMENTS")
        if name == "finish":
            if (set(args) != {"disposition", "reason", "citations"} or
                    args["disposition"] not in {"NO_CHANGE", "HANDOFF"} or
                    not isinstance(args["reason"], str) or len(args["reason"]) > 1024 or
                    not isinstance(args["citations"], list) or len(args["citations"]) > 8 or
                    any(not isinstance(item, dict) for item in args["citations"])):
                raise GatewayError("INVALID_FINISH")
            decision = Terminal(args["disposition"], clean(args["reason"]), tuple(args["citations"]))
        else:
            decision = validate_call(ToolCall(name, args))
        usage = value.get("usage")
        tokens = {"input_tokens": None, "output_tokens": None}
        if usage is not None:
            if not isinstance(usage, dict):
                raise GatewayError("INVALID_USAGE")
            for field, target, cap in [("prompt_tokens", "input_tokens", input_cap),
                                       ("completion_tokens", "output_tokens", output_cap)]:
                count = usage.get(field)
                if type(count) is not int or not 0 <= count <= cap:
                    raise GatewayError("INVALID_USAGE")
                tokens[target] = count
            total = usage.get("total_tokens")
            if type(total) is not int or total != tokens["input_tokens"] + tokens["output_tokens"]:
                raise GatewayError("INVALID_USAGE")
        return decision, call["id"], generation, tokens
    except GatewayError:
        raise
    except (ProtocolError, UnicodeError, TypeError, KeyError, ValueError, RecursionError):
        raise GatewayError("INVALID_RESPONSE") from None


def wire_messages(messages):
    output = [{"role": "system", "content": SYSTEM_PROMPT}]
    pending = None
    seen = set()
    for item in messages:
        if item.get("role") == "assistant" and "tool" in item:
            call_id = item.get("call_id")
            if pending is not None or not isinstance(call_id, str) or not IDENTIFIER.fullmatch(call_id) or call_id in seen:
                raise GatewayError("INVALID_HISTORY")
            seen.add(call_id)
            pending = (call_id, item["tool"])
            output.append({"role": "assistant", "content": None, "tool_calls": [{
                "id": call_id, "type": "function", "function": {
                    "name": item["tool"], "arguments": encode(item["arguments"]).decode("utf-8")}}]})
        elif item.get("role") == "tool":
            if pending is None or item.get("call_id") != pending[0] or item.get("tool") != pending[1]:
                raise GatewayError("INVALID_HISTORY")
            output.append({"role": "tool", "tool_call_id": pending[0],
                           "content": encode(item.get("result", {"error": item.get("error")})).decode("utf-8")})
            pending = None
        elif item.get("role") == "user" and pending is None:
            output.append({"role": "user", "content": item.get("content", "")})
        else:
            raise GatewayError("INVALID_HISTORY")
    if pending is not None:
        raise GatewayError("INVALID_HISTORY")
    return output


class AiGatewayProvider:
    def __init__(self, config, transport, *, local_double=False):
        if not isinstance(config, dict) or set(config) != CONFIG_FIELDS or type(config.get("schema_version")) is not int or config["schema_version"] != 1:
            raise GatewayError("INVALID_CONFIG")
        if not isinstance(config["model"], str) or not MODEL.fullmatch(config["model"]):
            raise GatewayError("INVALID_MODEL")
        if not isinstance(config["provider"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", config["provider"]):
            raise GatewayError("INVALID_PROVIDER")
        for key, cap in [("max_requests", 8), ("max_output_tokens", 2048), ("input_token_ceiling", 1050000)]:
            if type(config[key]) is not int or not 1 <= config[key] <= cap:
                raise GatewayError("INVALID_CONFIG")
        if type(config["live_enabled"]) is not bool or not isinstance(config["dataset_version"], str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", config["dataset_version"]):
            raise GatewayError("INVALID_CONFIG")
        try:
            self.allocation = Decimal(str(config["allocation_usd"]))
            self.reservation = Decimal(str(config["reserve_per_request_usd"]))
            if not self.allocation.is_finite() or not self.reservation.is_finite() or self.allocation < 0 or self.reservation < 0:
                raise GatewayError("INVALID_COST_BOUND")
        except (InvalidOperation, ValueError, TypeError):
            raise GatewayError("INVALID_COST_BOUND") from None
        if local_double:
            if config["live_enabled"] or self.allocation != 0 or self.reservation != 0 or not transport.local_double:
                raise GatewayError("INVALID_DOUBLE_CONFIG")
        elif not config["live_enabled"] or transport.local_double or self.reservation <= 0 or self.allocation < self.reservation:
            raise GatewayError("LIVE_DISABLED")
        self.config = dict(config)
        self.transport = transport
        self.mode = "local-double" if local_double else "ai-gateway-live"
        self.model_id = config["model"]
        self.local_double = local_double
        self.usage = []
        self.call_id = None
        self.run = None
        self.record = None
        self.log = None
        self.standalone = {"requests": 0, "reserved_cost_usd": "0", "estimated_cost_usd": None,
                           "actual_cost_usd": None, "attempts": []}

    def bind(self, run, record):
        self.run, self.record = run, record
        ledger = run.setdefault("provider_accounting", dict(self.standalone, attempts=[]))
        if (type(ledger.get("requests")) is not int or not 0 <= ledger["requests"] <= self.config["max_requests"] or
                not isinstance(ledger.get("attempts"), list) or len(ledger["attempts"]) > ledger["requests"]):
            raise GatewayError("INVALID_ACCOUNTING")
        run["versions"] = {
            "code_version": version([ROOT / p for p in [
                "crm_agent/providers/ai_gateway.py", "crm_agent/providers/owned_http.py",
                "crm_agent/providers/http_worker.py", "crm_agent/events.py", "crm_agent/loop.py",
                "crm_agent/java_client.py", "java/src/crm/Policy.java", "java/src/crm/Store.java"]]),
            "prompt_version": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
            "config_version": hashlib.sha256(encode(self.config)).hexdigest(),
            "dataset_version": self.config["dataset_version"]}
        run["provider_config"] = self.config
        self.log = EventLog(record.parent / "events.jsonl", dict(run["versions"],
            run_id=run["run_id"], mode=self.mode, model=self.model_id,
            provider="local-double" if self.local_double else None))
        self.log.emit("provider_bound", state=run["state"], actual_cost_usd=None, retries=0)

    def event(self, event, **values):
        if self.log:
            self.log.emit(event, **values)

    def next(self, messages, tools, limits):
        seconds = limits.get("remaining_seconds")
        if type(seconds) not in {int, float} or not math.isfinite(seconds) or seconds <= 0:
            raise GatewayError("INVALID_DEADLINE")
        deadline = time.monotonic() + min(20.0, seconds)
        body = encode({"model": self.model_id, "messages": wire_messages(messages), "stream": False,
                       "max_tokens": self.config["max_output_tokens"], "tool_choice": "required",
                       "tools": [{"type": "function", "function": item} for item in tools + [FINISH]],
                       "providerOptions": {"gateway": {"only": [self.config["provider"]]}}})
        if len(body) > 32768:
            raise GatewayError("REQUEST_SIZE")
        ledger = self.run["provider_accounting"] if self.run is not None else self.standalone
        if ledger["requests"] >= self.config["max_requests"]:
            raise GatewayError("REQUEST_BUDGET")
        try:
            prior_reservation = Decimal(ledger["reserved_cost_usd"])
        except (InvalidOperation, TypeError, KeyError):
            raise GatewayError("INVALID_ACCOUNTING") from None
        if not prior_reservation.is_finite() or prior_reservation < 0:
            raise GatewayError("INVALID_ACCOUNTING")
        reserved = prior_reservation + self.reservation
        if not self.local_double and reserved > self.allocation:
            raise GatewayError("COST_BUDGET")
        ledger["requests"] += 1
        ledger["reserved_cost_usd"] = str(reserved)
        ledger["estimated_cost_usd"] = None if self.local_double else str(reserved)
        if self.record:
            save(self.record, self.run)  # Reserve/count before possible transmission.
        started = time.monotonic()
        attempt = {"generation_id": None, "input_tokens": None, "output_tokens": None,
                   "error_code": None, "actual_cost_usd": None}
        try:
            payload = self.transport.post(body, max(0, deadline - time.monotonic()))
            # Retain only a safe generation identifier even when later schema validation fails.
            try:
                envelope = strict_json(payload.decode("utf-8", errors="strict"))
                candidate = envelope.get("id") if isinstance(envelope, dict) else None
                if isinstance(candidate, str) and IDENTIFIER.fullmatch(candidate) and candidate.startswith(("gen_", "chatcmpl-")):
                    attempt["generation_id"] = candidate
            except (ProtocolError, UnicodeError):
                pass
            decision, self.call_id, generation, tokens = parse_response(
                payload, self.model_id, output_cap=self.config["max_output_tokens"],
                input_cap=self.config["input_token_ceiling"])
            if self.call_id in {message.get("call_id") for message in messages}:
                raise GatewayError("REUSED_CALL_ID")
            if time.monotonic() >= deadline:
                raise GatewayError("DEADLINE")
            attempt.update(tokens, generation_id=generation)
            self.usage.append(tokens)
            return decision
        except GatewayError as error:
            attempt["error_code"] = error.code
            raise
        finally:
            attempt["duration_seconds"] = round(time.monotonic() - started, 6)
            ledger["attempts"].append(attempt)
            if self.record:
                save(self.record, self.run)
            self.event("provider_request", request_count=ledger["requests"],
                       reserved_cost_usd=ledger["reserved_cost_usd"],
                       estimated_cost_usd=ledger["estimated_cost_usd"],
                       cost_status="not_billed_double" if self.local_double else "unknown_reserved",
                       retries=0, **attempt)
