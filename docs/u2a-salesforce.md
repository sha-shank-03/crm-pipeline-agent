# U2-A: read-only Salesforce snapshot source

U1 was independently accepted on 11 October 2026. U2-A is a separate source-only slice: its in-memory/file checks may run without a heavy lease. Native local HTTP/process checks, Java/CI-equivalent execution, genuine Salesforce access and hosted CI remain pending. The four native transport methods are authored, not executed by this slice. No fresh evaluation corpus, comparison or live model run is included.

## Read boundary

SalesforceSnapshot accepts trusted configuration and an injected transport, then returns Opportunity and linked-Task snapshots. It registers no acting tool and changes neither Salesforce nor the local Java CRM. There is no apply/export/PATCH endpoint. Task descriptions remain untrusted evidence; their contents cannot select a URL, SOQL, identity, credential or local operation.

The profile pins an exact HTTPS My Domain instance, identity host, org/user, API version and eighteen-character Opportunity/Task IDs. Configured IDs allow at most ten opportunities and eight tasks. The supplied profile is fictional and externally disabled; v66.0 is an authored protocol pin, not a discovered supported version in a real org. Main must confirm all pins before an external assignment.

The identity request uses a fixed org/user URL and verifies returned org, user, active status and the identity URL. It additionally requires asserted_user to be exactly boolean true, binding the supplied token to the queried identity. False, missing and wrong-type values stop with IDENTITY_MISMATCH after the identity GET. Returned endpoint/photo links are ignored. Salesforce documents the [identity URL format and token binding](https://developer.salesforce.com/docs/platform/mobile-sdk/guide/oauth-using-identity-urls.html). This path requires the identity scope as well as API access; no token creation or refresh flow is implemented.

Two fixed SELECT templates enumerate only the pinned IDs and selected fields. Task reads additionally constrain WhatId to the opportunity allowlist. Each returned row is checked again against the pins. A missing requested row stops with SCOPED_RECORD_MISSING instead of claiming a complete snapshot.

Opportunity fields are Id, Name, CloseDate, NextStep and LastModifiedDate. Task fields are Id, WhatId, Subject, Description, ActivityDate and LastModifiedDate. The adapter checks describe object name/queryability, unique required field metadata, expected types/lengths and boolean permission metadata. Read-only field permissions are valid. These are intentionally strict standard-field schema pins; main must review real describe responses, including their size, before a genuine run. The [describe resource](https://developer.salesforce.com/docs/platform/api-rest/guide/resources-sobject-describe.html) provides that metadata.

Mapping preserves source record IDs and source LastModifiedDate. It does not manufacture a local version or claim a consistent multi-request transaction. Import/staging into Java remains a later reviewed step; exact local approval does not become Salesforce write authorization.

## Bounds and failure behavior

Ceilings are 32 GET attempts, four pages per query, 256 KiB per response, 2 MiB received bytes, 60 cumulative active seconds and 20 seconds per request. Smaller positive integer bounds are permitted. Request/byte/active counters can be persisted and rebound; failed dispatch attempts remain counted. There are zero automatic retries.

Pagination accepts only the pinned API version's relative query-locator path. It rejects absolute URLs, other resources/versions, encodings, cycles, duplicate records, inconsistent totals, empty nonfinal pages and incomplete final coverage. Salesforce [query pagination](https://developer.salesforce.com/docs/platform/api-rest/guide/dome-query.html) uses nextRecordsUrl; the adapter treats it as untrusted input.

API responses require a valid Sforce-Limit-Info usage header and sufficient conservative headroom. The limits body must contain valid DailyApiRequests allocation/remaining values; headroom reserves the remaining local request ceiling plus the configured floor. The [limits resource](https://developer.salesforce.com/docs/platform/api-rest/guide/resources-limits.html) can lag consumption and requires View Setup and Configuration. This is a preflight/control boundary, not a guarantee against other clients consuming quota.

Identity, auth, quota, malformed/oversized JSON, schema drift, scope and pagination failures stop with fixed error codes. Raw error bodies/tokens are not put in error text. Snapshot acquisition makes no partial-success claim. Cancellation and the same absolute deadline are checked before/after every exchange.

The additive HTTP transport starts one owned Python worker for one GET. Its parent reserves cleanup within the caller deadline; the child disables proxies/redirects and caps headers/body/EOF processing. Tokens arrive through private stdin, never argv, environment reads or logs. External mode requires both external_enabled and explicit allow_external plus a supplied runtime credential callback. The loopback double forbids credentials. Native cleanup/DNS/TLS behavior is unverified until the later lease; no hard-real-time claim is made.

## Pure commands and later leased commands

Pure source checks, using Python 3.11:

~~~powershell
py -3.11 -m unittest tests.test_salesforce_contract tests.test_upgrade_provenance tests.test_m3_release -v
py -3.11 tools/check_current_upgrade.py
py -3.11 tools/check.py --suite release
~~~

The following commands require a new named lease and fresh output paths. They are authored for independent execution, not results of U2-A:

~~~powershell
py -3.11 tools/build.py
py -3.11 -m unittest tests.test_salesforce_http -v
py -3.11 tools/check.py --suite all
py -3.11 -m unittest tests.test_ai_gateway tests.test_upgrade_events tests.test_upgrade_transport tests.test_upgrade_journey tests.test_gateway_cli -v
py -3.11 crm.py demo --scenario approved-update --provider scripted --scripted-review --out work/u2a-review-approved
py -3.11 crm.py demo --scenario stale-approval --provider scripted --scripted-review --out work/u2a-review-stale
py -3.11 tools/upgrade_check.py --offline --out work/u2a-review-gateway
py -3.11 tools/clean_copy.py --out work/u2a-review-source --cached-deps --verify
~~~

The full legacy suite can create two concurrent Java processes. A grant must cover that concurrency as well as one loopback server and owned Python workers. These local commands use already verified cached JARs and do not fetch dependencies. Hosted CI separately provisions the same pinned artifacts on clean Windows/Linux runners; that source change is not a hosted execution result.

## Source preservation and release

evaluation/frozen-manifest.json and accepted evaluation/upgrade-v1/manifest.json remain byte-for-byte intact. Their old checkers continue to describe their respective source trees. The original evaluation/upgrade-v2/manifest.json is also preserved as the reviewed U2-A predecessor, including its failed token-binding contract. CI and release now use tools/check_current_upgrade.py with evaluation/upgrade-v3/manifest.json for the bounded identity correction. It verifies prior manifest bytes, original data hashes and current source hashes. Of U1's hashed files, only CI, release and clean-copy packaging differ; actor, approval and domain source retain U1 hashes. The correction permits changes only to the Salesforce guard, its pure fixtures/tests, provenance checker/tests and this guide.

Source export includes both prior manifests and current adapter/tests/docs. Generated binaries, work directories, local instructions and credentials remain excluded. Release checks reject enabled external profiles. Free CI retains pinned actions, Python 3.11/Java 17, read-only permissions, serial Windows/Linux jobs and no inference/secrets. It exercises the current offline upgrade without calling an external org or scoring a new evaluation. Original hosted failures and U1 evidence are preserved.

Code is Apache-2.0. Authored fixtures remain CC0-1.0; protocol-double payloads are authored test data, not independent customer evidence.

A genuine sandbox needs a dedicated Developer Edition test org, a restricted read-only user, explicit fixture scope, confirmed API/schema/describe sizes, identity/API scopes and limits permission, short-lived main-injected OAuth token, current quota headroom, and a separate main assignment/lease. No account setup, token inspection, real traffic, refresh storage, writes/export, hosted run, deployment or production-readiness result is claimed.

