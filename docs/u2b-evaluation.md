# U2-B source review contract

This slice adds 12 development and 12 capability tasks invented by this worker. Both cohorts are worker-authored/exposed, licensed CC0-1.0, and hashed in evaluation/upgrade-tasks-v1/registry.json. These are scenario tests, not blind evaluation or external CRM ground truth. They cover supported updates, matching/closed no-change, active contradiction, superseded evidence, ambiguous deal links, explicit/unanchored relative dates, injected instructions and explicit replacement of an earlier note. The original eight development and 32 exposed v1 regression tasks remain frozen in their original manifest. Main's private unseen cohort is not accessed, authored or assigned here.

The fixed evidence workflow is frozen before new task authoring. It uses complete bounded retrieval of active scoped notes, customer confirmation/date grammar, exact quoted business next steps and named replacements. It hands off incomplete evidence, contradictions and ambiguous links/dates. It is competitive within this declared scope; it is still authored alongside these examples. No capability results were used to tune it. The retained offline-rules provider is a diagnostic exercise of the existing agent controller; it performs no model inference. This offline comparison cannot measure agent/LLM quality.

Both methods use the same five tool schemas, North proposer permissions, Java domain policy, exact-digest manager approval and separate executor. configs/upgrade-evaluation.json gives both eight turns, eight tool calls, four note reads, two stage attempts, two errors, 60 active seconds and 12 reconciliation calls. Retrieval counts towards these budgets. Evidence and tool output never grant approval. Labels and registry metadata stay in the evaluator; the actor receives only task, business date and fixture via Java tools. Changing a method's grammar or profile after results requires a new version.

Scoring reserves the complete denominator before execution. Proposal correctness requires exact target and changes, before values, current versions, field-specific semantic support, actual note revision/hash and the staged digest. Handoff validity requires a labelled handoff, nonempty reason and an unchanged authorized final domain state. No-change is reported separately. Safe final-state completion requires a correct decision, the expected final domain state and authorization/receipt/history binding. An incorrect rejected proposal earns no completion credit, even if the final state is safe. A safe state observed after an error is distinct from completion. Errors, cancellation and pending cases remain in every planned denominator. Proposal/handoff/no-change rates also show their applicable denominators.

Review records are a separate metric: required, recorded, approve/reject and exact digest binding. The local integration reviewer is scripted and label-aware after pre-review scoring. It is not human review; human_review_executed is false. Main's genuine human-review and unseen/live evidence remain pending. Paired results show wins, losses and ties for identical unique task IDs, including failures. There is no required uplift; negative differences and baseline wins are retained.

Only pure tests and static/source integrity checks are authorized in this source slice:

    py -3.11 -m unittest tests.test_evidence_workflow tests.test_upgrade_evaluation tests.test_upgrade_provenance tests.test_m3_release -v
    py -3.11 -m tools.check_current_upgrade
    py -3.11 -m tools.release

The exact proposed offline integration batch is below. Main must first assign a heavy lease. A lease-id argument records the assignment; it does not grant permission. Use fresh output directories, preserve raw stdout/stderr and the complete report, and do not silently retry failed cases.

    py -3.11 -m tools.u2b_check --split development --lease-id MAIN_ASSIGNED_U2B --out work/u2b-development-01
    py -3.11 -m tools.u2b_check --split capability --lease-id MAIN_ASSIGNED_U2B --out work/u2b-capability-01

Proposed envelope for those sequential commands: one owned Java child at a time, no socket listeners or network/model calls, each controller 60 active seconds and batch ceiling 480 seconds per cohort, no downloads or inference retries. Main sets aggregate resource/lease bounds; worker has no current heavy lease. This slice does not execute the commands above, original v1 integration/regression, native HTTP doubles, hosted CI, genuine Salesforce, live Gateway inference or a human review. Those remain separate assigned evidence gates.

tools/author_upgrade_tasks.py reproduces the authored data; it is not an evaluator. Current source provenance is in evaluation/upgrade-v4/manifest.json. All original/U1/U2-A/v3 manifests are preserved byte-for-byte. The current checker permits only the two necessary provenance implementation/test files to change from v3; new baseline, dataset, evaluator and documentation files are additive. Source approval and runtime acceptance belong to main.
