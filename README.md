# CRM pipeline agent

A small local workflow for reviewing stale opportunity close dates and inconsistent next steps. Python retrieves CRM evidence and stages an exact patch; Java controls review, record versions, field permissions, atomic state changes, and retry receipts.

**Current checkpoint: M1/M2 accepted; authored-v6 final local MVP independently accepted, including documentation and release checks.** The fresh validation-informed comparison completed 192/192 runs: rules matched 96/96 pre-review outcomes and baseline 84/96; both reached 96/96 authorized correct states after label-aware synthetic review. Main independently passed 133 tests, three actual Windows checkpoint conditions, 16 development runs and two README demos, then checked all 192 raw states without importing the repository oracle. The earlier 191/192 failure and 98/192 partial cohort remain unchanged. JVM working sets were absent from integration samples, so combined JVM/Python peak and RAM-cap compliance remain unverified. See [fresh results](docs/m3-monitor2-results.json) and [EVIDENCE.md](EVIDENCE.md). Hosted CI, live model quality and production impact remain unestablished.

The two supported demo journeys are a reviewed correction and rejection of an approval made stale by a concurrent rep edit. Only `close_date` and `next_step` are editable. The normal workflow stops for an operator to enter the exact displayed action digest.

## Quickstart

Prerequisites: Python 3.11 and JDK 17 on PATH. No Maven, pip packages, Docker, model runtime, account, or API key is required. Windows commands explicitly select Python 3.11:

```powershell
py -3.11 tools/bootstrap.py --fetch-deps
py -3.11 tools/build.py
py -3.11 crm.py doctor
py -3.11 crm.py demo --scenario approved-update --provider scripted --scripted-review --out work/demo-approved
py -3.11 crm.py demo --scenario stale-approval --provider scripted --scripted-review --out work/demo-stale
py -3.11 tools/check.py --suite m1
```

The dependency lock pins Gson 2.14.0 and its declared Error Prone annotations 2.48.0 dependency. Bootstrap verifies the downloaded JAR hashes, published artifact/parent POM declarations, and upstream license texts. [The completed audit](docs/dependency-audit.json) records the exact closure and notice findings. Both JARs are on the compile/runtime classpath; no Error Prone compiler or checker is installed. Build/demo/test never download dependencies implicitly. Already provisioned verified artifacts can be used offline. Bootstrap records received response-body bytes durably and limits cumulative transfers to 2 MiB per bootstrap accounting scope; HTTP headers are excluded.

Choose a new output directory for each demo; existing outputs are preserved. Observed approved result: close date `2026-11-12`, next step `Send revised agreement by 2026-10-09.`, version `7 -> 8`, one apply event, and the original receipt on retry. Observed stale result: the rep's concurrent next step remains, version is 8, and there are zero agent apply events. The tests read committed state directly to check these outcomes.

## Manual review

```powershell
py -3.11 crm.py init --fixture fixtures/dev/approved-update.json --store work/manual-crm
py -3.11 crm.py run --store work/manual-crm --provider scripted --scope north --as-of 2026-10-08 --out work/manual-run
py -3.11 crm.py review --store work/manual-crm --proposal latest
py -3.11 crm.py approve --store work/manual-crm --proposal latest --reviewer manager-north --interactive
py -3.11 crm.py resume --store work/manual-crm --run work/manual-run
py -3.11 crm.py inspect --store work/manual-crm
```

`latest` resolves once to a concrete proposal. Approval requires entering its exact displayed digest. `reject` uses the same explicit review flow. Resume applies only the immutable stored patch; no replacement patch comes from the provider. Repeated resume reuses its checkpointed apply key. `inspect` is a trusted local fixture-operator view.

M2 also supports `--provider offline-rules`, and `py -3.11 crm.py cancel --run work/manual-run`. Review CLI actions persist their exact key/arguments in an immutable `operator-intent-*.json` file before approve/reject dispatch. Resume queries the original committed operation before retrying an uncertain mutation. Cancellation blocks new writes; a possibly committed write remains uncertain until reconciliation. The M1 regression and M2 policy/recovery tests exercised these boundaries.

## Architecture and contribution

```mermaid
flowchart LR
    User[Pipeline review task] --> Loop[Python bounded decision loop]
    Loop <--> Script[Scripted or offline rule provider]
    Loop <--> Java[Java scoped CRM tools]
    Java --> Proposal[Exact proposal and evidence]
    Proposal --> Review[Operator digest approval]
    Review --> Apply[Deterministic resume executor]
    Apply --> Store[Atomic snapshot and receipt]
    Store --> Verify[Independent final-state tests]
```

The provider chooses tools from observed records. It cannot approve, execute shell commands, read arbitrary files, or write CRM fields directly. Each Java request is a bounded JSON subprocess. Gson 2.14.0 and Error Prone annotations 2.48.0 are the two verified Java libraries. The store commits full immutable generations under a persistent file lock; state, receipt, approval consumption, and audit apply event share one commit. Store caps are 500 generations, 10 MiB per snapshot, and 100 MiB total generated storage.

The contribution is an inspectable CRM-specific governed update contract with evidence and concurrent-edit outcomes. Generic approval workflows and CRM CRUD are established prior art. [Architecture](docs/architecture.md), [local trust boundaries](docs/threat-model.md), and [protocol](contracts/protocol.md) explain the design.

## Verification and limits

```powershell
py -3.11 tools/check.py --suite light
py -3.11 tools/check.py --suite m1
py -3.11 tools/check.py --suite m2-unit
py -3.11 tools/check.py --suite m3-unit
py -3.11 tools/check.py --suite release
```

`light` runs Python protocol/loop tests without Java or networking. `m1` requires the built Java classes and both verified dependency artifacts; it runs parser assertions, real Java integration tests, and both demos. The integration verifier reads committed JSON state directly. It also exercises exact interactive approval input and two separately approved proposals against the same record version; only the first proposal applies.

`m2-unit` runs 50 pure-Python components against in-memory doubles. `m3-unit` adds evaluator/accounting/corruption and source-export controls on tiny invented data without a model, Java or case-set execution. `release` statically checks source/license/pins/path hygiene and frozen hashes without a JDK or Git. The `policy`, `recovery`, and `provider` suites require rebuilt Java and/or an actual local HTTP test server; all passed at M2. The authored-v5 `all` suite passed 119 methods, including both demos. The authored-v6 candidate adds 14 persistence/recovery methods; all 133 methods passed before the later held-out resource stop. [M2 details](docs/m2.md), [the frozen M3 contract](docs/evaluation.md), [original contribution](docs/originality.md) and [clean-copy procedure](docs/quickstart.md) distinguish measured evidence from pending independent acceptance and hosted CI. [M3 outcomes](docs/m3.md) report pre-review proposals separately from the synthetic reviewer's final-state outcomes.

The accepted M1 supports one authored fictional opportunity and its note. A quote/hash proves provenance, not the truth or meaning of a sales commitment. Local launcher roles assume a trusted machine owner and provide no enterprise authentication or OS isolation. Snapshot storage is intended for small demos. Portable power-loss durability and network filesystems are unverified. The optional Ollama transport requires explicit opt-in, a literal loopback address and an already installed local model; no service, model or paid call has been executed.

Code/documentation: Apache-2.0. Authored fictional fixtures: CC0-1.0. See [third-party notices](THIRD_PARTY_NOTICES.md) and [data license](DATA_LICENSE).
