# Standalone quickstart and reproduction

Run from the repository root with Python 3.11 and JDK 17 on PATH. No Python packages, Maven, Docker, model service or account credentials are required. Windows selects Python 3.11 explicitly; on Linux use the installed Python 3.11 executable.

```powershell
py -3.11 tools/bootstrap.py --fetch-deps
py -3.11 tools/build.py
py -3.11 crm.py doctor
py -3.11 crm.py demo --scenario approved-update --provider scripted --scripted-review --out work/quickstart-approved
py -3.11 crm.py demo --scenario stale-approval --provider scripted --scripted-review --out work/quickstart-stale
py -3.11 tools/check.py --suite all
py -3.11 tools/check.py --suite release
```

Use fresh output paths for each attempt; existing outputs are preserved. --fetch-deps is an explicit provisioning/audit mode and may access remote artifact/POM/license material. Build/tests do not fetch dependencies. For disconnected execution, provision both original pinned JARs in .deps, then use `py -3.11 tools/bootstrap.py --cached-only` before building. Cached-only mode verifies the exact original coordinate/filename/hash pairs, refuses missing/changed cache or altered pins, and never accesses the network, download ledger or remote POM/license audit. It does not rewrite the lock or historical audit. [Dependency audit](dependency-audit.json) and [third-party notices](../THIRD_PARTY_NOTICES.md) record the earlier pins/closure/licenses.

The approved demo ends at version 8 with the exact reviewed date/next step, one apply event/receipt and the original receipt on retry. The stale demo preserves the rep's concurrent next step with zero agent writes. The historical authored-v6 all suite passed 133 methods, real Java policy/recovery and local HTTP doubles, plus parser assertions and both demos. Current configured suites include later pure/native upgrade controls; latest full-suite/hosted acceptance remains pending. No model is started by these offline commands.

## Windows checkpoint recovery

On Windows, after building, reproduce the three owned sharing-denial and receipt-recovery conditions in a fresh directory:

```powershell
py -3.11 tools/windows_checkpoint.py --out work/windows-checkpoint-verification
```

These conditions have passed in worker and independent main runs: release after a real denial, hold through bounded exhaustion, and recover the original receipt after a committed Java effect. The tool closes its own handles and threads. See [checkpoint recovery](checkpoint-recovery.md) for the original failure and bounded replacement contract; the controlled conditions do not establish its cause.

## Source copy and disconnected check

```powershell
py -3.11 tools/clean_copy.py --out work/standalone-source
py -3.11 tools/clean_copy.py --out work/clean-copy-verification --cached-deps --verify
```

The source exporter excludes Git, caches, build/work output, local instructions, environment files and bytecode. It includes all fixtures, labels, frozen manifest, licenses/notices and Java/Python entrypoints. Follow the quickstart from the exported root; it imports no sibling source.

The current cached verification form verifies both original JAR pins before creating the copy and checks both copied JARs again with `tools/bootstrap.py --cached-only` before build. There is no fetch fallback. It runs ten sequential commands: cached bootstrap, build, doctor, approved demo, stale demo, M1, Salesforce pure/native local contracts, offline Gateway double, current upgrade provenance and static release. A failure stops the sequence and retains work/clean-copy-check.json inside the new copy. The Salesforce/Gateway steps are local doubles; the 24-task cohort comparison and live services are excluded. Individual command timeout is 90 seconds; a separate main-assigned runtime envelope is required for the complete native packaging batch.

On 12 October main reproduced a pre-build parser failure in the v5 proposal: bootstrap had been invoked with no flags while its parser required --fetch-deps. The v6 correction added cached-only mode. Main then ran the fresh v6 copy: steps 1–8 passed, step 9 (`python tools/check_current_upgrade.py`) failed with ModuleNotFoundError: evaluation, and release step 10 did not run. The file-path checker now adds its own source root before importing repository packages; an actual source-only subprocess regression uses no checkout/PYTHONPATH, Java or network. The bootstrap/build/application/provider/test inputs for the first eight steps remain byte-identical to main's passing copy. Their dated raw outcomes carry forward without repeating them; full packaging acceptance is still pending.

Main needs only a fresh current source-only copy for the two remaining gates, preserving the earlier copy and failed receipt:

    py -3.11 tools/clean_copy.py --out work/u2c-main-source-gates-01
    cd work/u2c-main-source-gates-01
    py -3.11 tools/check_current_upgrade.py
    py -3.11 tools/check.py --suite release

These remaining commands copy/check public source and run static provenance/release checks; they require no cached JARs, build or native services. The earlier full ten-step command stays available as a reproduction contract, but the bounded correction requests no repeat of its eight passing native steps. Main separately regenerated the saved offline summary byte-identically under 481 assertions/seven negative controls; the 24 exposed tasks/48 prior arm runs and negative controller differences are unchanged. The historical 8 October authored-v6 check passed seven commands, 19 M1 methods, eight Java assertions and four demo invocations; that dated copy remains separate from current gates.

## Authored comparison

The following commands describe the historical original-v1 authored comparison and require its matching source snapshot. Current upgrade provenance is checked with `py -3.11 -m tools.check_current_upgrade`; preserved original manifests are not regenerated to match upgraded source. The explicit --allow-heldout flag is not an approval or OS isolation mechanism. Do not rerun or tune the accepted cohorts as part of the cached packaging correction. Historical commands use fresh output paths:

```powershell
py -3.11 -m evaluation.freeze --check
py -3.11 -m evaluation.run --split heldout --provider offline-rules --baseline latest-note --repeat 3 --allow-heldout --out work/reproduced-heldout
py -3.11 -m evaluation.verify --report work/reproduced-heldout/report.json
py -3.11 -m evaluation.corruptions --report work/reproduced-heldout/report.json --out work/reproduced-corruptions
```

The fresh validation-informed cohort completed 192/192, passed the verifier and 18 corruption controls, and was independently inspected from raw state without importing the repository oracle. Rules matched 96/96 pre-review outcomes versus baseline 84/96; both had 96/96 authorized correct final states after label-aware synthetic review. The original 191/192 failed and 98/192 partial cohorts remain historical and unchanged. The frozen manifest's scored:false/pending_main fields are historical pre-execution metadata, retained unchanged.

Main independently accepted the historical local MVP application/evaluation/documentation: 133 methods, three Windows conditions, 16 development runs and two README demos, followed by 192 raw-state checks and 1,152 artifact hashes. [Dated MVP results](m3-monitor2-results.json) distinguish these checks from current upgrade gates. Main accepted the U2-B offline contract separately; current corrected packaging, genuine Salesforce/live/unseen/human-review and final upgrade/publication acceptance remain pending.

All 899 worker integration resource samples contain only Python, despite 1,349 captured Java completions. The 125,263,872-byte sample maximum is not a combined JVM/Python peak or proof of RAM-cap compliance; Java coverage/concurrency and full lifetime RAM remain unknown. Main's lifetime RAM peak is also unmeasured. [M3 results](m3.md) report exact limits without inferring why samples missed JVMs.

CI source contains Ubuntu/Windows jobs with read-only contents permission, one job at a time, ten-minute limits and commit-pinned actions in ci-actions.lock.json. This correction has not dispatched or executed hosted CI; earlier CI records remain historical. Future CI setup/bootstrap may provision runtimes and the small pinned JARs; the historical local follow-up downloaded neither binaries nor models. Current native/hosted packaging, live model quality, genuine CRM/accounts, production impact and upgrade publication remain unestablished.
