# U1: local Gateway protocol and governed CRM writes

This slice is ready for independent review. It does not establish live model quality, external Salesforce integration or deployment. All model responses in the new journey come from an explicitly authored loopback HTTP double. The double uses actual Java observations and sends the documented Gateway function-call envelope; it is not a model.

The current hosted workflow still uses the original freeze contract. CI/release migration and the full legacy suite await later main assignment; current hosted CI is not claimed green.

The original published base is commit `3c1e900842a883988b2f06bbd819d0fec77f45ac`. Its evaluation manifest and data remain unchanged. The separate `evaluation/upgrade-v1/manifest.json` identifies changed source and new modules. Original freeze checks continue to describe the original tree; the upgrade does not silently replace those hashes.

## Reproduce the bounded U1 checks

Use Python 3.11 and Java 17, with the already verified two-JAR dependency cache. These commands do not fetch dependencies or load keys. Choose a fresh journey output directory.

```powershell
py -3.11 tools/build.py
py -3.11 -m unittest tests.test_ai_gateway tests.test_upgrade_events -v
py -3.11 -m unittest tests.test_m2_http tests.test_upgrade_transport -v
py -3.11 -m unittest tests.test_upgrade_journey -v
py -3.11 -m unittest tests.test_gateway_cli -v
py -3.11 tools/upgrade_check.py --offline --out work/u1-review
py -3.11 tools/check_upgrade_manifest.py
```

The recorded batch passed 34 unique methods (35 executions including the separately run original deadline method), the new Gateway-double CLI and the existing approved scripted CLI. Two CLI gate methods verified zero credential reads/HTTP dispatch for the disabled profile and rejected an arbitrary endpoint override. These are worker checks pending main review.

The retained journey reads the original fictional `opp-101` fixture at business date 2026-10-08. Five Gateway-shaped responses select list opportunities, get opportunity, list notes, read note and stage proposal. The saved `wire.json` pairs each assistant call ID with its actual Java observation.

The workflow pauses at `WAITING_APPROVAL`; resume without approval changes no opportunity. A deliberately wrong digest is denied. The test operator then authorizes the exact displayed digest through the existing immutable operator-intent flow. This is programmatic test review, not a human approval result.

The journey injects response loss **after Java actually commits apply**. The next resume queries the original operation key, recovers the receipt and does not write again. Direct generation-file checks establish version 7→8, only `close_date`/`next_step` changed, one history addition, one apply event and one receipt. The retained before/after snapshots, review packet, checkpoint, wire requests, events and report permit separate inspection.

The negative Java controls cover an out-of-scope principal, an unknown approval tool, stale approval after a rep edit and cancellation before apply. Pure parser controls reject malformed or multiple tool calls, unknown tools, unsupported completion claims, mismatched models, invalid usage and oversized requests.

## Deadlines and cancellation

Hosted run [37888888840](https://github.com/sha-shank-03/crm-pipeline-agent/actions/runs/37888888840) failed the Windows slow-provider assertion. A retry consuming the test server's instant fallback response is a supported code-based hypothesis; the original log does not contain enough timing data to prove every cause.

The original 0.05-second deadline and `<0.5s` assertion remain. Every delayed-server attempt now blocks until teardown. Inference retries are zero. The local Ollama transport checks the same deadline after headers, body reads including EOF and parsing. Early-timeout, late-EOF and slow-body controls are retained.

Gateway transport runs one owned Python HTTP process. A parent monotonic budget covers startup, I/O and a cleanup reservation within the supplied deadline. The child has no subprocesses, follows no redirects and accepts only the fixed Gateway URL or an explicitly selected literal-loopback double. Cancellation kills only its owned process handle. Native local tests cover headers, trickled body, dropped response, size limits and 401/402/403/429/500/503; DNS/TLS against a real provider remain unexecuted.

Java tools inherit the active deadline. Active time and reconciliation counters persist across resume. Waiting for review is excluded from active accounting and identified in the summary. The fixture roles are a local demonstration boundary, not enterprise authentication.

## Explicit configuration and evidence

`crm.py run --provider ai-gateway` requires an explicit nonsecret `--gateway-config` and `--allow-live-model`. The supplied `configs/gateway.disabled.json` has zero allocation and live execution disabled. It cannot send inference or load the runtime credential. Enabling it is outside U1. A later main-approved profile must identify the exact current catalog model, provider allowlist, request/output/input ceilings and conservative per-request reservation.

The application does not switch to another model or scripted provider on error. A typed failure stops inference. Its checkpoint counts and reserves dispatch attempts before possible transmission, retains reservations after ambiguous outcomes, and leaves actual cost unknown. Main owns the aggregate spend ledger and any later credential/pricing/live assignment.

Events contain code-component, prompt, config and dataset versions, mode, safe generation IDs, synthetic token counts, request/tool counts, timings and typed errors. They contain no request headers or prompts. The report's latency quantiles are **provider-request samples for one authored task**, not a population-level task-performance estimate. The wrong-digest unsafe attempt is an authored operator negative control, not a model-produced unsafe action. Actual cost and cost per success remain null.

The Windows retained journey also captures owned-handle peak working sets and exit codes for its five HTTP workers and fourteen Java calls. Root memory is sampled, so combined continuous peak and complete batch RAM-cap compliance are not established. Earlier v1 missing-JVM samples remain unchanged; new component measurements do not repair historical evidence.

Code uses Python standard libraries and the existing Java 17 libraries. Code is Apache-2.0; existing authored data is CC0-1.0. U2 Salesforce work begins read-only and is not implemented here; optional remote PATCH export is deferred because Opportunity time preconditions are weaker than the exact local version contract. Main independent acceptance, Linux/hosted reruns, new evaluations, live inference, sandbox access and publication remain pending.
