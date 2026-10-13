# Offline CRM operating evidence — 12 October 2026

Main accepted the U2-B offline evaluation contract after executing both cohorts and independently checking 879 assertions and five negative controls. This study covers 24 worker-authored/exposed invented tasks, each run once by two deterministic methods: 48 arm runs. Scenario families are paired between the development and capability cohorts; these are not 48 independent tasks or a blind generalization test. No LLM inference, Salesforce API call or actual human review occurred.

The existing agent controller uses the retained offline-rules provider. The fixed evidence workflow reads eligible scoped notes, understands a broader declared commitment grammar and resolves explicit named replacements. Both use the same five acting tools, North permissions, Java domain policy and exact-digest manager/executor boundary. The fixed workflow was frozen before task authoring. No controller, baseline, task, label or scorer was tuned after these outcomes.

| Cohort / method | Correct completion / 12 planned | Correct proposals / 5 expected | Valid handoffs / 5 expected | Correct no-change / 2 expected | Scripted approves / rejects |
| --- | ---: | ---: | ---: | ---: | ---: |
| Development / offline controller | 8/12 | 1/5 | 5/5 | 2/2 | 1 / 0 |
| Development / fixed workflow | 12/12 | 5/5 | 5/5 | 2/2 | 5 / 0 |
| Capability / offline controller | 5/12 | 0/5 | 4/5 | 1/2 | 0 / 2 |
| Capability / fixed workflow | 12/12 | 5/5 | 5/5 | 2/2 | 5 / 0 |

All 48 arm runs completed with zero evaluator errors, cancellations or pending slots. Each offline-controller arm made nine pre-review handoffs; each fixed-workflow arm made five. Only the handoffs shown as valid above match the expected task disposition. The two wrong capability proposals were rejected by the scripted reviewer and earn no completion credit. All final states were authorized, which alone does not mean the intended task was completed.

Paired correct-completion differences, offline controller minus fixed workflow, are -4/12 (-33.3 percentage points) in development and -7/12 (-58.3 points) in capability. There were zero controller wins, four/seven fixed-workflow wins and eight/five ties. Negative differences are retained; no uplift threshold applies. These results show the methods' coverage on the exposed authored scenarios and do not establish model quality or production impact.

| Cohort / method | Acting tool calls | Provider decision turns | Note reads | Resume dispatch counter | Pre-review active p50 / p95 (s) | Controller plus resume active p50 / p95 (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Development / offline controller | 48 | 59 | 12 | 2 | 0.922 / 1.359 | 0.922 / 1.610 |
| Development / fixed workflow | 51 | 58 | 11 | 10 | 1.031 / 1.469 | 1.031 / 2.046 |
| Capability / offline controller | 49 | 59 | 12 | 2 | 0.969 / 1.578 | 0.969 / 1.798 |
| Capability / fixed workflow | 51 | 58 | 11 | 10 | 1.016 / 1.407 | 1.016 / 1.937 |

Acting tool calls come from the saved controller transcript; provider decision turns include terminal decisions and are not model requests. Resume counts come from the saved reconciliation counter and are separate from acting tools and manager approve/reject actions. Total native Java request attempts are unavailable because a complete subprocess attempt trace was not persisted. The aggregate acting tool count is 199; neither this count nor the resume counter is a total native process count.

Latency uses saved active milliseconds: pre-review controller time, then controller plus resume time. It excludes fixture setup, manager approval/rejection action, reviewer waiting and complete evaluator/file I/O. Per-case end-to-end wall latency is unavailable. Nearest-rank p50/p95 use all 12 arm runs per row, including incorrect decisions; at n=12, p95 is the observed maximum. These single-host measurements are descriptive, with no confidence or throughput claim. Both-arm cohort report wall times were 39.687/39.625 seconds; outer command times were 40.609/40.437 seconds for development/capability. Per-arm wall times are not inferred.

Tokens are null/not applicable because no inference occurred. Provider cost amount and currency are null because no billing record exists; no paid-cost or human-review-time claim is made. Native process-tree RAM peaks remain unmeasured. The optional Gateway and read-only Salesforce source contracts do not turn these local outcomes into live evidence.

Correct completion requires a correct pre-review decision, the expected final domain state, protected-record invariants and exact approval/receipt/history authorization. Proposal correctness additionally requires exact supported fields and provenance. Handoff validity requires an expected handoff, a nonempty reason, no concealed stage and an unchanged authorized domain. All planned runs stay in the completion denominator; proposal/handoff/no-change columns use their stated applicable denominators. Scripted review occurs after label-aware proposal scoring and is reported separately from genuine human review.

[The aggregate summary](u2b-offline-summary.json) records all counts, min/p50/p95/max, definitions and SHA-256 references to main's private reports/receipts and the sealed U2-B source manifest. Raw paths, notes, traces, request IDs and operator identifiers are excluded. Main retains the raw evidence privately; these aggregates cannot reproduce its private state audit from public source alone.

For source review without Java or network:

    py -3.11 -m unittest tests.test_u2b_summary tests.test_upgrade_provenance -v
    py -3.11 -m tools.check_current_upgrade
    py -3.11 -m tools.release

CI adds the new pure summary/evaluator/provenance controls while preserving existing deadline, domain and provider tests, serial Windows/Linux jobs, pinned actions, read-only permissions and a ten-minute job ceiling. The 24-task Java comparison stays an explicit manual command in [the U2-B contract](u2b-evaluation.md); it is not added to automatic CI. Latest hosted CI/current native packaging are not executed in U2-C. Genuine Salesforce, secured live provider/main-held unseen evaluation, actual human review and final publication remain pending.
