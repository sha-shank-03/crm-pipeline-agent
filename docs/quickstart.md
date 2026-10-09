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

Use fresh output paths for each attempt; existing outputs are preserved. Bootstrap fetches only the two pinned JARs, validates their digests and declared closure, and skips already verified cached artifacts. Build/tests do not fetch dependencies. For disconnected execution, provision both exact JARs in .deps before building. [Dependency audit](dependency-audit.json) and [third-party notices](../THIRD_PARTY_NOTICES.md) record their pins and licenses.

The approved demo ends at version 8 with the exact reviewed date/next step, one apply event/receipt and the original receipt on retry. The stale demo preserves the rep's concurrent next step with zero agent writes. The all suite contains 133 methods, real Java policy/recovery and local HTTP doubles, plus parser assertions and both demos. It starts no model.

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

The cached verification form requires both already verified JARs and permits no fetch fallback. It runs seven commands: bootstrap/build/doctor, two fresh demos, M1 and static release. The receipt is work/clean-copy-check.json inside the new copy. The completed authored-v6 check passed all seven, 19 M1 methods, eight Java assertions and four demo invocations. The tested copy predates final result-documentation edits.

## Authored comparison

The explicit --allow-heldout flag opts into the frozen authored comparison; it is not an approval or OS isolation mechanism. Select new output paths:

```powershell
py -3.11 -m evaluation.freeze --check
py -3.11 -m evaluation.run --split heldout --provider offline-rules --baseline latest-note --repeat 3 --allow-heldout --out work/reproduced-heldout
py -3.11 -m evaluation.verify --report work/reproduced-heldout/report.json
py -3.11 -m evaluation.corruptions --report work/reproduced-heldout/report.json --out work/reproduced-corruptions
```

The fresh validation-informed cohort completed 192/192, passed the verifier and 18 corruption controls, and was independently inspected from raw state without importing the repository oracle. Rules matched 96/96 pre-review outcomes versus baseline 84/96; both had 96/96 authorized correct final states after label-aware synthetic review. The original 191/192 failed and 98/192 partial cohorts remain historical and unchanged. The frozen manifest's scored:false/pending_main fields are historical pre-execution metadata, retained unchanged.

Main independently passed the application/evaluation review: 133 methods, three Windows conditions, 16 development runs and two README demos, followed by 192 raw-state checks and 1,152 artifact hashes. Final documentation review is pending. [Current results](m3-monitor2-results.json) distinguish these checks from older worker evidence.

All 899 worker integration resource samples contain only Python, despite 1,349 captured Java completions. The 125,263,872-byte sample maximum is not a combined JVM/Python peak or proof of RAM-cap compliance; Java coverage/concurrency and full lifetime RAM remain unknown. Main's lifetime RAM peak is also unmeasured. [M3 results](m3.md) report exact limits without inferring why samples missed JVMs.

CI source contains Ubuntu/Windows jobs with read-only contents permission, one job at a time, ten-minute limits and commit-pinned actions in ci-actions.lock.json. No hosted workflow has been dispatched. Future CI setup/bootstrap may provision runtimes and the small pinned JARs; the completed local follow-up downloaded neither binaries nor models. Live model quality, real CRM/accounts, production impact and publication remain unestablished.
