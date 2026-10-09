# JSON subprocess contract v1

Launch: `java -Xmx128m -cp CLASSPATH crm.Main --store PATH --principal ID --as-of YYYY-MM-DD`.

Input is one UTF-8 JSON object, at most 64 KiB, depth at most 12. Unknown/duplicate fields and non-integer JSON numbers are rejected. Output is one JSON object, at most 256 KiB; diagnostics are on stderr. A normal domain rejection is represented in JSON, and the CLI converts it to exit code 2.

```json
{"schema_version":1,"request_id":"unique-operation-key","operation":"list_opportunities","args":{}}
```

Success:

```json
{"schema_version":1,"request_id":"unique-operation-key","ok":true,"result":{"opportunities":[]}}
```

Rejection:

```json
{"schema_version":1,"ok":false,"error":{"code":"PERMISSION_DENIED","message":"Operation unavailable to this principal"}}
```

Read operations: `list_opportunities`, `get_opportunity`, `list_notes`, `read_note`, `review`, `receipt`, `inspect`, `operation_result`. The provider dispatcher exposes only the first four and `stage_proposal`. Trusted operator initialization/review and executor apply are separate code paths. M2 `operation_result` takes `operation`, `request_id`, and exact original `args`; it returns `found` and `result`, checks the caller's operation role, fingerprint and current scope, and never commits. It accepts only stage/approve/reject/apply reconciliation.

Stage args: `run_id`, `opportunity_id`, `expected_version`, `expected_evidence_revision`, `expected_account_revision`, `changes`, `citations`. Every change has `field`, `before`, `after`; every citation has `field`, `note_id`, `note_revision`, `line_id`, `line_hash`, `quote`. One exact citation per changed field is required.

Approve/reject args: `proposal_id`, `digest`. Apply args: `proposal_id`, `approval_id`, `digest`. The apply request ID is its durable action key; the request carries no replacement patch. Identical successful requests return their recorded result. Reused key/different content returns `IDEMPOTENCY_CONFLICT`. A different apply key for a completed proposal returns `ALREADY_APPLIED`.

The run file persists original mutating arguments/keys before dispatch. Operator review uses a separate immutable intent file. On uncertain delivery the same operation is queried before retry; approval expiry or changed record state cannot be repaired by editing the patch under an old digest. M2 role revocation blocks privileged cached results/receipts. A completed identical apply can return its original receipt after approval consumption/expiry, with current caller scope still enforced.

M2 fixture-only `fixture_change` has a strict discriminated argument shape: `kind=note` with a complete incremented note, `kind=account` with account ID/new region, `kind=access` with principal/enabled, `kind=expire_approval` with approval ID, or `kind=policy` with version. Only the trusted fixture operator can invoke it. New/changed/relinked notes increment evidence revision for the union of affected opportunity IDs. Model tool schemas expose none of these arguments/operations. Launcher failpoints are also outside JSON tool arguments.
