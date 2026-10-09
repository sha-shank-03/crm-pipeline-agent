# Third-party attribution

The two compile/runtime libraries are:

- Google Gson 2.14.0, `com.google.code.gson:gson:2.14.0`, Apache License 2.0. [Official tagged source](https://github.com/google/gson/tree/gson-parent-2.14.0); [verbatim upstream LICENSE](docs/dependency-notices/gson-2.14.0-upstream-LICENSE.txt).
- Google Error Prone annotations 2.48.0, `com.google.errorprone:error_prone_annotations:2.48.0`, Apache License 2.0. [Official tagged source](https://github.com/google/error-prone/tree/v2.48.0/annotations); [verbatim upstream COPYING](docs/dependency-notices/error_prone_annotations-2.48.0-upstream-LICENSE.txt).

This project downloads these JARs into an ignored dependency directory and does not vendor them in Git. [dependencies.lock.json](dependencies.lock.json) pins their verified SHA-256 hashes. [The artifact audit](docs/dependency-audit.json) records exact published artifact/parent POMs, inherited license declarations, JAR and license hashes, dependency scopes, and transfer receipts. Gson declares the annotations library as non-optional compile scope; the annotations artifact declares no further required non-test dependencies. Test dependencies and build plugins are not downloaded. No Error Prone compiler/checker is installed.

Neither inspected JAR embeds identifiable LICENSE or NOTICE files. This absence is recorded explicitly; the full license texts above were obtained from the exact upstream release tags and checked against the published POM license declarations. The bootstrap stops if an additional required non-test artifact appears. The two-artifact declared runtime closure has been verified.

Python and JDK are user-installed prerequisites and are not redistributed. No model SDK/runtime/weights, external accounts, or additional application libraries are bundled.

The optional CI workflow references the MIT-licensed official actions/checkout v7.0.1, actions/setup-java v6.0.1 and actions/setup-python v7.0.0. Their exact release commits, official tag/ref URLs and upstream license URLs/hashes are recorded in [the CI action lock](docs/ci-actions.lock.json), retrieved 2026-10-08. Action implementation code is not vendored. Hosted CI has not been executed.

CRM history and write APIs are established prior art, illustrated by [HubSpot deals documentation](https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/deals/guide). Human review and retry semantics are established workflow concepts, illustrated by [LangGraph interrupt documentation](https://docs.langchain.com/oss/python/langgraph/interrupts). No vendor content or source code was copied into the authored fixture.
