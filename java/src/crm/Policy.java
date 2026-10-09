package crm;

import com.google.gson.*;
import java.time.*;
import java.time.format.DateTimeParseException;
import java.util.*;

final class Policy {
    static final Set<String> EDITABLE = Set.of("close_date", "next_step");
    final String principal;
    final String region;
    final LocalDate asOf;

    Policy(String principal, LocalDate asOf) {
        Failure.require(Set.of("proposer-north", "manager-north", "executor-north", "proposer-south", "manager-south", "executor-south", "fixture-operator").contains(principal),
                "PERMISSION_DENIED", "Unknown local principal");
        this.principal = principal;
        this.region = principal.endsWith("-south") ? "south" : "north";
        this.asOf = asOf;
    }

    Store.Result execute(JsonObject state, String operation, String requestId, JsonObject args) {
        if (operation.equals("init")) return initialize(state, args);
        requireEnabled(state, principal);
        Failure.require(Json.integer(state, "policy_version") == 1, "POLICY_VERSION", "Unsupported policy version");
        return switch (operation) {
            case "list_opportunities" -> read(listOpportunities(state, args));
            case "get_opportunity" -> read(getOpportunity(state, args));
            case "list_notes" -> read(listNotes(state, args));
            case "read_note" -> read(readNote(state, args));
            case "stage_proposal", "approve", "reject", "apply", "edit_opportunity", "fixture_change" -> mutate(state, operation, requestId, args);
            case "operation_result" -> read(operationResult(state, args));
            case "review" -> read(review(state, args));
            case "receipt" -> read(receipt(state, args));
            case "inspect" -> { requirePrincipal("fixture-operator"); Json.fields(args); yield read(state.deepCopy()); }
            default -> throw new Failure("UNKNOWN_OPERATION", "Unknown operation");
        };
    }
    private Store.Result read(JsonObject result) { return new Store.Result(result, false); }
    private void requirePrincipal(String expected) {
        String allowed = expected.equals("fixture-operator") ? expected : expected.replace("north", region);
        Failure.require(principal.equals(allowed), "PERMISSION_DENIED", "Operation unavailable to this principal");
    }
    private void requireEnabled(JsonObject state, String identity) {
        // M1 stores remain readable: absent access entries mean the original fixed role map.
        if (state.has("access") && map(state, "access").has(identity))
            Failure.require(Json.bool(Json.object(map(state, "access").get(identity)), "enabled"), "PERMISSION_DENIED", "Local role was revoked");
    }
    private JsonObject map(JsonObject state, String name) { return Json.object(state.get(name)); }
    private JsonObject lookup(JsonObject collection, String id) {
        Failure.require(collection.has(id), "NOT_FOUND", "Record not found");
        return Json.object(collection.get(id));
    }
    private JsonObject opportunity(JsonObject state, String id) {
        JsonObject item = lookup(map(state, "opportunities"), id);
        JsonObject account = lookup(map(state, "accounts"), Json.string(item, "account_id"));
        Failure.require(principal.equals("fixture-operator") || Json.string(account, "region").equals(region), "PERMISSION_DENIED", "Opportunity outside scope");
        return item;
    }
    private boolean visible(JsonObject state, JsonObject note) {
        JsonObject account = lookup(map(state, "accounts"), Json.string(note, "account_id"));
        return Json.string(account, "region").equals(region) && Json.array(note, "visibility").contains(new JsonPrimitive(region));
    }
    private Store.Result initialize(JsonObject state, JsonObject args) {
        requirePrincipal("fixture-operator");
        Json.fields(args, "fixture");
        JsonObject fixture = Json.object(args.get("fixture"));
        Json.fields(fixture, "schema_version", "synthetic", "name", "accounts", "opportunities", "notes");
        Failure.require(Json.integer(fixture, "schema_version") == 1 && Json.bool(fixture, "synthetic"), "INVALID_FIXTURE", "Only authored synthetic v1 fixtures are accepted");
        for (String name : List.of("accounts", "opportunities", "notes")) state.add(name, Json.object(fixture.get(name)).deepCopy());
        state.addProperty("schema_version", 1);
        state.addProperty("policy_version", 1);
        state.addProperty("synthetic", true);
        state.addProperty("name", Json.string(fixture, "name"));
        for (String name : List.of("proposals", "approvals", "receipts", "operations")) state.add(name, new JsonObject());
        state.add("audit", new JsonArray());
        // Fixture loading is an explicit trusted synthetic operation, including out-of-scope records.
        Failure.require(map(state, "opportunities").size() <= 10, "INVALID_FIXTURE", "Fixture exceeds opportunity limit");
        Failure.require(map(state, "notes").size() <= 16, "INVALID_FIXTURE", "Fixture exceeds note limit");
        for (String id : map(state, "accounts").keySet()) {
            JsonObject account = lookup(map(state, "accounts"), id);
            Json.fields(account, "id", "name", "region", "revision");
            Failure.require(Json.string(account, "id").equals(id) && Set.of("north", "south").contains(Json.string(account, "region")) && Json.integer(account, "revision") > 0, "INVALID_FIXTURE", "Invalid account");
            Json.string(account, "name");
        }
        for (String id : map(state, "opportunities").keySet()) {
            JsonObject opp = opportunity(state, id);
            Json.fields(opp, "id", "account_id", "owner_id", "stage", "amount_minor", "currency", "close_date", "next_step", "version", "evidence_revision", "history");
            Failure.require(Json.string(opp, "id").equals(id), "INVALID_FIXTURE", "Opportunity ID mismatch");
            Failure.require(Json.integer(opp, "version") > 0 && Json.integer(opp, "evidence_revision") > 0 && Json.integer(opp, "amount_minor") >= 0, "INVALID_FIXTURE", "Invalid integer field");
            Failure.require(Set.of("open", "won", "lost").contains(Json.string(opp, "stage")), "INVALID_FIXTURE", "Invalid stage");
            Json.string(opp, "owner_id"); Json.string(opp, "currency");
            date(Json.string(opp, "close_date")); Json.string(opp, "next_step");
            Json.array(opp, "history");
        }
        for (String id : map(state, "notes").keySet()) validateNote(state, id, lookup(map(state, "notes"), id));
        return new Store.Result(Json.of("initialized", true, "synthetic", true), true);
    }
    private JsonObject listOpportunities(JsonObject state, JsonObject args) {
        Json.fields(args);
        JsonArray items = new JsonArray();
        for (String id : map(state, "opportunities").keySet()) {
            JsonObject raw = lookup(map(state, "opportunities"), id);
            JsonObject account = lookup(map(state, "accounts"), Json.string(raw, "account_id"));
            if (!Json.string(account, "region").equals(region)) continue;
            JsonObject item = opportunity(state, id).deepCopy();
            item.remove("history");
            item.addProperty("account_name", Json.string(account, "name"));
            item.addProperty("stale_close_date", Json.string(item, "stage").equals("open") && date(Json.string(item, "close_date")).isBefore(asOf));
            items.add(item);
        }
        return Json.of("opportunities", items, "as_of", asOf.toString());
    }
    private JsonObject getOpportunity(JsonObject state, JsonObject args) {
        Json.fields(args, "id", "include_history");
        JsonObject item = opportunity(state, Json.string(args, "id")).deepCopy();
        if (!Json.bool(args, "include_history")) item.remove("history");
        JsonObject account = lookup(map(state, "accounts"), Json.string(item, "account_id"));
        item.addProperty("account_revision", Json.integer(account, "revision"));
        item.addProperty("account_name", Json.string(account, "name"));
        return item;
    }
    private JsonObject listNotes(JsonObject state, JsonObject args) {
        Json.fields(args, "opportunity_id");
        String id = Json.string(args, "opportunity_id");
        opportunity(state, id);
        JsonArray notes = new JsonArray();
        for (JsonElement element : map(state, "notes").asMap().values()) {
            JsonObject note = Json.object(element);
            if (visible(state, note) && Json.array(note, "opportunity_ids").contains(new JsonPrimitive(id))) {
                JsonObject metadata = note.deepCopy(); metadata.remove("lines"); notes.add(metadata);
            }
        }
        return Json.of("notes", notes);
    }
    private JsonObject readNote(JsonObject state, JsonObject args) {
        Json.fields(args, "note_id");
        JsonObject note = lookup(map(state, "notes"), Json.string(args, "note_id"));
        Failure.require(visible(state, note), "PERMISSION_DENIED", "Note outside scope");
        JsonObject result = note.deepCopy();
        for (JsonElement element : Json.array(result, "lines")) {
            JsonObject line = Json.object(element);
            line.addProperty("line_hash", Json.hash(Json.string(line, "text")));
        }
        return result;
    }
    private Store.Result mutate(JsonObject state, String operation, String requestId, JsonObject args) {
        switch (operation) {
            case "stage_proposal": requirePrincipal("proposer-north"); break;
            case "approve", "reject": requirePrincipal("manager-north"); break;
            case "apply": requirePrincipal("executor-north"); break;
            case "edit_opportunity", "fixture_change": requirePrincipal("fixture-operator"); break;
            default: throw new Failure("UNKNOWN_OPERATION", "Unknown mutation");
        }
        String key = operation + ":" + principal + ":" + requestId;
        String fingerprint = Json.hash(Json.canonical(Json.of("operation", operation, "principal", principal, "as_of", asOf.toString(), "args", args)));
        JsonObject operations = map(state, "operations");
        if (operations.has(key)) {
            JsonObject prior = Json.object(operations.get(key));
            Failure.require(Json.string(prior, "fingerprint").equals(fingerprint), "IDEMPOTENCY_CONFLICT", "Operation key reused with different content");
            // Scope is rechecked even for a successful repeat.
            opportunity(state, Json.string(prior, "opportunity_id"));
            if (operation.equals("stage_proposal")) recheckEvidenceAccess(state, Json.object(Json.object(prior.get("result")).get("proposal")));
            return read(Json.object(prior.get("result")).deepCopy());
        }
        JsonObject result = switch (operation) {
            case "stage_proposal" -> stage(state, requestId, args);
            case "approve" -> approve(state, requestId, args);
            case "reject" -> reject(state, args);
            case "apply" -> apply(state, requestId, args);
            case "edit_opportunity" -> edit(state, args);
            case "fixture_change" -> fixtureChange(state, args);
            default -> throw new Failure("UNKNOWN_OPERATION", "Unknown mutation");
        };
        operations.add(key, Json.of("fingerprint", fingerprint, "opportunity_id", Json.string(result, "opportunity_id"), "result", result.deepCopy()));
        return new Store.Result(result, true);
    }
    private JsonObject stage(JsonObject state, String requestId, JsonObject args) {
        Json.fields(args, "run_id", "opportunity_id", "expected_version", "expected_evidence_revision", "expected_account_revision", "changes", "citations");
        JsonArray changes = sorted(Json.array(args, "changes"), "field");
        JsonArray citations = sorted(Json.array(args, "citations"), "field");
        String id = "p-" + requestId;
        JsonObject action = Json.of("schema_version", 1, "proposal_id", id, "run_id", Json.string(args, "run_id"),
                "opportunity_id", Json.string(args, "opportunity_id"), "expected_version", Json.integer(args, "expected_version"),
                "expected_evidence_revision", Json.integer(args, "expected_evidence_revision"),
                "expected_account_revision", Json.integer(args, "expected_account_revision"),
                "policy_version", 1, "as_of", asOf.toString(), "changes", changes, "citations", citations);
        validateAction(state, action);
        JsonObject proposal = Json.of("id", id, "action", action, "digest", Json.hash(Json.canonical(action)), "status", "WAITING_APPROVAL");
        map(state, "proposals").add(id, proposal);
        event(state, "PROPOSAL_STAGED", Json.string(action, "opportunity_id"), id);
        return Json.of("proposal", proposal.deepCopy(), "opportunity_id", Json.string(action, "opportunity_id"));
    }
    private JsonArray sorted(JsonArray input, String key) {
        List<JsonObject> values = new ArrayList<>();
        for (JsonElement value : input) values.add(Json.object(value).deepCopy());
        values.sort(Comparator.comparing(value -> Json.string(value, key)));
        JsonArray result = new JsonArray(); values.forEach(result::add); return result;
    }
    private void validateAction(JsonObject state, JsonObject action) {
        String id = Json.string(action, "opportunity_id");
        JsonObject opp = opportunity(state, id);
        Failure.require(Json.string(opp, "stage").equals("open"), "CLOSED_OPPORTUNITY", "Only open opportunities are editable");
        Failure.require(Json.integer(opp, "version") == Json.integer(action, "expected_version"), "VERSION_CONFLICT", "Opportunity changed; propose and approve again");
        Failure.require(Json.integer(opp, "evidence_revision") == Json.integer(action, "expected_evidence_revision"), "EVIDENCE_CHANGED", "Evidence changed; propose and approve again");
        JsonObject account = lookup(map(state, "accounts"), Json.string(opp, "account_id"));
        Failure.require(Json.integer(account, "revision") == Json.integer(action, "expected_account_revision"), "VERSION_CONFLICT", "Account changed");
        Failure.require(Json.integer(action, "policy_version") == Json.integer(state, "policy_version"), "POLICY_VERSION", "Policy changed");
        JsonArray changes = Json.array(action, "changes");
        JsonArray citations = Json.array(action, "citations");
        Failure.require(changes.size() >= 1 && changes.size() <= 2 && citations.size() == changes.size(), "INVALID_PATCH", "One citation is required per changed field");
        Set<String> seen = new HashSet<>();
        LocalDate businessDate = date(Json.string(action, "as_of"));
        for (int index = 0; index < changes.size(); index++) {
            JsonObject change = Json.object(changes.get(index));
            Json.fields(change, "field", "before", "after");
            String field = Json.string(change, "field");
            Failure.require(EDITABLE.contains(field), "FORBIDDEN_FIELD", "Field is not editable");
            Failure.require(seen.add(field), "INVALID_PATCH", "Duplicate changed field");
            String before = Json.string(change, "before"), after = Json.string(change, "after");
            Failure.require(Json.string(opp, field).equals(before), "VERSION_CONFLICT", "Before value changed");
            Failure.require(!after.equals(before) && !after.isBlank(), "INVALID_PATCH", "Replacement must be nonempty and different");
            JsonObject citation = Json.object(citations.get(index));
            Json.fields(citation, "field", "note_id", "note_revision", "line_id", "line_hash", "quote");
            Failure.require(Json.string(citation, "field").equals(field), "INVALID_EVIDENCE", "Citation field mismatch");
            JsonObject note = lookup(map(state, "notes"), Json.string(citation, "note_id"));
            Failure.require(visible(state, note), "PERMISSION_DENIED", "Evidence outside scope");
            Failure.require(Json.string(note, "status").equals("active") && !date(Json.string(note, "occurred_at")).isAfter(businessDate), "INVALID_EVIDENCE", "Evidence is superseded or future-dated");
            Failure.require(Json.string(note, "account_id").equals(Json.string(opp, "account_id")) && Json.array(note, "opportunity_ids").contains(new JsonPrimitive(id)), "INVALID_EVIDENCE", "Evidence is not linked to this opportunity");
            Failure.require(Json.integer(note, "revision") == Json.integer(citation, "note_revision"), "EVIDENCE_CHANGED", "Evidence revision changed");
            String quote = Json.string(citation, "quote");
            boolean found = false;
            for (JsonElement lineElement : Json.array(note, "lines")) {
                JsonObject line = Json.object(lineElement);
                if (Json.string(line, "id").equals(Json.string(citation, "line_id"))) {
                    found = Json.string(line, "text").equals(quote) && Json.hash(quote).equals(Json.string(citation, "line_hash"));
                }
            }
            Failure.require(found, "INVALID_EVIDENCE", "Exact evidence line/hash mismatch");
            if (field.equals("close_date")) {
                LocalDate proposed = date(after);
                Failure.require(!proposed.isBefore(businessDate) && !proposed.isAfter(businessDate.plusDays(365)), "INVALID_PATCH", "Close date outside demo policy window");
                Failure.require(quote.contains(after), "INVALID_EVIDENCE", "Evidence does not contain proposed date");
            } else {
                Failure.require(after.codePointCount(0, after.length()) <= 240 && after.equals(quote), "INVALID_EVIDENCE", "Next step must match a bounded exact evidence line");
            }
        }
    }
    private JsonObject proposal(JsonObject state, String id) {
        JsonObject proposal = lookup(map(state, "proposals"), id);
        opportunity(state, Json.string(Json.object(proposal.get("action")), "opportunity_id"));
        Failure.require(Json.hash(Json.canonical(proposal.get("action"))).equals(Json.string(proposal, "digest")), "STORE_CORRUPT", "Proposal digest mismatch");
        return proposal;
    }
    private JsonObject review(JsonObject state, JsonObject args) {
        Json.fields(args, "proposal_id");
        JsonObject proposal = proposal(state, Json.string(args, "proposal_id"));
        String id = Json.string(Json.object(proposal.get("action")), "opportunity_id");
        JsonElement approval = JsonNull.INSTANCE;
        for (JsonElement value : map(state, "approvals").asMap().values()) {
            JsonObject item = Json.object(value);
            if (Json.string(item, "proposal_id").equals(Json.string(proposal, "id"))) approval = item.deepCopy();
        }
        return Json.of("proposal", proposal.deepCopy(), "approval", approval, "current_opportunity", opportunity(state, id).deepCopy(), "synthetic", Json.bool(state, "synthetic"));
    }
    private JsonObject approve(JsonObject state, String requestId, JsonObject args) {
        Json.fields(args, "proposal_id", "digest");
        JsonObject proposal = proposal(state, Json.string(args, "proposal_id"));
        Failure.require(Json.string(proposal, "status").equals("WAITING_APPROVAL"), "APPROVAL_STATE", "Proposal is not awaiting review");
        Failure.require(Json.string(proposal, "digest").equals(Json.string(args, "digest")), "APPROVAL_DIGEST", "Exact digest required");
        JsonObject action = Json.object(proposal.get("action")); validateAction(state, action);
        Instant now = Instant.now();
        String id = "a-" + requestId;
        JsonObject approval = Json.of("id", id, "proposal_id", Json.string(proposal, "id"), "digest", Json.string(proposal, "digest"),
                "reviewer", principal, "region", region, "issued_at", now.toString(), "expires_at", now.plusSeconds(900).toString(), "state", "APPROVED");
        map(state, "approvals").add(id, approval);
        proposal.addProperty("status", "APPROVED");
        event(state, "APPROVED", Json.string(action, "opportunity_id"), Json.string(proposal, "id"));
        return Json.of("approval", approval.deepCopy(), "opportunity_id", Json.string(action, "opportunity_id"));
    }
    private JsonObject reject(JsonObject state, JsonObject args) {
        Json.fields(args, "proposal_id", "digest");
        JsonObject proposal = proposal(state, Json.string(args, "proposal_id"));
        Failure.require(Json.string(proposal, "status").equals("WAITING_APPROVAL") && Json.string(proposal, "digest").equals(Json.string(args, "digest")), "APPROVAL_STATE", "Exact waiting proposal required");
        proposal.addProperty("status", "REJECTED");
        String id = Json.string(Json.object(proposal.get("action")), "opportunity_id");
        event(state, "REJECTED", id, Json.string(proposal, "id"));
        return Json.of("status", "REJECTED", "opportunity_id", id);
    }
    private JsonObject apply(JsonObject state, String requestId, JsonObject args) {
        Json.fields(args, "proposal_id", "approval_id", "digest");
        JsonObject proposal = proposal(state, Json.string(args, "proposal_id"));
        Failure.require(Json.string(proposal, "digest").equals(Json.string(args, "digest")), "APPROVAL_DIGEST", "Exact digest required");
        Failure.require(!Json.string(proposal, "status").equals("APPLIED"), "ALREADY_APPLIED", "Proposal already has a receipt; use the original action key");
        JsonObject approval = lookup(map(state, "approvals"), Json.string(args, "approval_id"));
        Failure.require(Json.string(approval, "proposal_id").equals(Json.string(proposal, "id")) && Json.string(approval, "digest").equals(Json.string(proposal, "digest")), "APPROVAL_DIGEST", "Approval is for another exact action");
        Failure.require(Json.string(approval, "reviewer").equals("manager-" + region) && Json.string(approval, "region").equals(region), "PERMISSION_DENIED", "Reviewer outside scope");
        requireEnabled(state, Json.string(approval, "reviewer"));
        Failure.require(Json.string(proposal, "status").equals("APPROVED") && Json.string(approval, "state").equals("APPROVED"), "APPROVAL_STATE", "Approved action required");
        Failure.require(Instant.now().isBefore(Instant.parse(Json.string(approval, "expires_at"))), "APPROVAL_EXPIRED", "Approval expired");
        JsonObject action = Json.object(proposal.get("action")); validateAction(state, action);
        String id = Json.string(action, "opportunity_id"); JsonObject opp = opportunity(state, id);
        long oldVersion = Json.integer(opp, "version");
        JsonObject before = new JsonObject(), after = new JsonObject();
        for (JsonElement element : Json.array(action, "changes")) {
            JsonObject change = Json.object(element); String field = Json.string(change, "field");
            before.add(field, opp.get(field).deepCopy()); after.addProperty(field, Json.string(change, "after"));
            opp.addProperty(field, Json.string(change, "after"));
        }
        opp.addProperty("version", oldVersion + 1);
        Json.array(opp, "history").add(Json.of("event_id", "apply-" + requestId, "actor", principal, "before", before, "after", after, "version", oldVersion + 1));
        approval.addProperty("state", "CONSUMED"); proposal.addProperty("status", "APPLIED");
        JsonObject receipt = Json.of("action_key", requestId, "proposal_id", Json.string(proposal, "id"), "approval_id", Json.string(approval, "id"),
                "digest", Json.string(proposal, "digest"), "opportunity_id", id, "old_version", oldVersion, "new_version", oldVersion + 1,
                "changed_fields", after, "commit_generation", Json.integer(state, "generation") + 1);
        map(state, "receipts").add(requestId, receipt.deepCopy());
        event(state, "APPLIED", id, Json.string(proposal, "id"));
        return Json.of("receipt", receipt, "opportunity_id", id);
    }
    private JsonObject receipt(JsonObject state, JsonObject args) {
        requirePrincipal("executor-north"); Json.fields(args, "action_key");
        JsonObject receipt = lookup(map(state, "receipts"), Json.string(args, "action_key"));
        opportunity(state, Json.string(receipt, "opportunity_id"));
        return Json.of("receipt", receipt.deepCopy(), "opportunity_id", Json.string(receipt, "opportunity_id"));
    }
    private JsonObject edit(JsonObject state, JsonObject args) {
        Json.fields(args, "opportunity_id", "expected_version", "next_step");
        Failure.require(Json.bool(state, "synthetic"), "PERMISSION_DENIED", "Fixture edit requires synthetic store");
        JsonObject opp = opportunity(state, Json.string(args, "opportunity_id"));
        Failure.require(Json.integer(opp, "version") == Json.integer(args, "expected_version"), "VERSION_CONFLICT", "Fixture edit version conflict");
        String value = Json.string(args, "next_step");
        Failure.require(!value.isBlank() && value.length() <= 240, "INVALID_PATCH", "Invalid fixture edit");
        long version = Json.integer(opp, "version") + 1;
        JsonObject before = Json.of("next_step", Json.string(opp, "next_step"));
        opp.addProperty("next_step", value); opp.addProperty("version", version);
        Json.array(opp, "history").add(Json.of("actor", principal, "before", before, "after", Json.of("next_step", value), "version", version));
        event(state, "FIXTURE_EDIT", Json.string(opp, "id"), "");
        return Json.of("opportunity_id", Json.string(opp, "id"), "new_version", version);
    }
    private void validateNote(JsonObject state, String id, JsonObject note) {
        Json.fields(note, "id", "revision", "account_id", "opportunity_ids", "occurred_at", "status", "visibility", "lines");
        Failure.require(Json.string(note, "id").equals(id) && Json.integer(note, "revision") > 0, "INVALID_FIXTURE", "Invalid note identity/revision");
        lookup(map(state, "accounts"), Json.string(note, "account_id")); date(Json.string(note, "occurred_at"));
        Failure.require(Set.of("active", "superseded").contains(Json.string(note, "status")), "INVALID_FIXTURE", "Invalid note status");
        for (JsonElement value : Json.array(note, "opportunity_ids")) {
            Failure.require(value.isJsonPrimitive() && value.getAsJsonPrimitive().isString(), "INVALID_FIXTURE", "Invalid note link");
            lookup(map(state, "opportunities"), value.getAsString());
        }
        for (JsonElement value : Json.array(note, "visibility")) Failure.require(value.isJsonPrimitive() && value.getAsJsonPrimitive().isString() && Set.of("north", "south").contains(value.getAsString()), "INVALID_FIXTURE", "Invalid visibility");
        Set<String> ids = new HashSet<>();
        Failure.require(Json.array(note, "lines").size() <= 16, "INVALID_FIXTURE", "Too many note lines");
        for (JsonElement value : Json.array(note, "lines")) {
            JsonObject line = Json.object(value); Json.fields(line, "id", "text");
            Failure.require(ids.add(Json.string(line, "id")) && Json.string(line, "text").length() <= 4096, "INVALID_FIXTURE", "Duplicate/oversized evidence line");
        }
    }
    private void recheckEvidenceAccess(JsonObject state, JsonObject proposal) {
        for (JsonElement value : Json.array(Json.object(proposal.get("action")), "citations")) {
            JsonObject note = lookup(map(state, "notes"), Json.string(Json.object(value), "note_id"));
            Failure.require(visible(state, note), "PERMISSION_DENIED", "Cached evidence no longer readable");
        }
    }
    private JsonObject operationResult(JsonObject state, JsonObject args) {
        Json.fields(args, "operation", "request_id", "args");
        String operation = Json.string(args, "operation");
        Failure.require(Set.of("stage_proposal", "approve", "reject", "apply").contains(operation), "INVALID_REQUEST", "Only checkpointed governed mutation reconciliation is supported");
        requirePrincipal(operation.equals("apply") ? "executor-north" : operation.equals("stage_proposal") ? "proposer-north" : "manager-north");
        String key = operation + ":" + principal + ":" + Json.string(args, "request_id");
        if (!map(state, "operations").has(key)) return Json.of("found", false, "result", JsonNull.INSTANCE);
        JsonObject prior = Json.object(map(state, "operations").get(key));
        String fingerprint = Json.hash(Json.canonical(Json.of("operation", operation, "principal", principal, "as_of", asOf.toString(), "args", Json.object(args.get("args")))));
        Failure.require(Json.string(prior, "fingerprint").equals(fingerprint), "IDEMPOTENCY_CONFLICT", "Checkpoint content differs from committed operation");
        opportunity(state, Json.string(prior, "opportunity_id"));
        JsonObject result = Json.object(prior.get("result"));
        if (operation.equals("stage_proposal")) recheckEvidenceAccess(state, Json.object(result.get("proposal")));
        return Json.of("found", true, "result", result.deepCopy());
    }
    private JsonObject fixtureChange(JsonObject state, JsonObject args) {
        Failure.require(Json.bool(state, "synthetic"), "PERMISSION_DENIED", "Fixture change requires synthetic store");
        String kind = Json.string(args, "kind");
        String opportunityId = map(state, "opportunities").keySet().stream().findFirst().orElseThrow(() -> new Failure("INVALID_FIXTURE", "Opportunity required"));
        switch (kind) {
            case "note": {
                Json.fields(args, "kind", "note"); JsonObject note = Json.object(args.get("note")).deepCopy(); String id = Json.string(note, "id");
                validateNote(state, id, note); Set<String> affected = new HashSet<>();
                if (map(state, "notes").has(id)) {
                    JsonObject old = lookup(map(state, "notes"), id);
                    Failure.require(Json.integer(note, "revision") == Json.integer(old, "revision") + 1, "EVIDENCE_CHANGED", "Replacement note revision must increment once");
                    for (JsonElement value : Json.array(old, "opportunity_ids")) affected.add(value.getAsString());
                } else Failure.require(Json.integer(note, "revision") == 1 && map(state, "notes").size() < 16, "INVALID_FIXTURE", "New note requires revision one and capacity");
                for (JsonElement value : Json.array(note, "opportunity_ids")) affected.add(value.getAsString());
                for (String affectedId : affected) { JsonObject opp = opportunity(state, affectedId); opp.addProperty("evidence_revision", Json.integer(opp, "evidence_revision") + 1); }
                map(state, "notes").add(id, note); break;
            }
            case "account": {
                Json.fields(args, "kind", "account_id", "region"); String region = Json.string(args, "region");
                Failure.require(Set.of("north", "south").contains(region), "INVALID_FIXTURE", "Invalid account scope");
                JsonObject account = lookup(map(state, "accounts"), Json.string(args, "account_id"));
                account.addProperty("region", region); account.addProperty("revision", Json.integer(account, "revision") + 1); break;
            }
            case "access": {
                Json.fields(args, "kind", "principal", "enabled"); String identity = Json.string(args, "principal");
                Failure.require(Set.of("proposer-north", "manager-north", "executor-north", "proposer-south", "manager-south", "executor-south").contains(identity), "INVALID_FIXTURE", "Invalid revocable role");
                if (!state.has("access")) state.add("access", new JsonObject());
                map(state, "access").add(identity, Json.of("enabled", Json.bool(args, "enabled"))); break;
            }
            case "expire_approval": {
                Json.fields(args, "kind", "approval_id"); JsonObject approval = lookup(map(state, "approvals"), Json.string(args, "approval_id"));
                approval.addProperty("expires_at", Instant.now().minusSeconds(1).toString());
                opportunityId = Json.string(Json.object(proposal(state, Json.string(approval, "proposal_id")).get("action")), "opportunity_id"); break;
            }
            case "policy": Json.fields(args, "kind", "version"); Failure.require(Json.integer(args, "version") > 0, "INVALID_FIXTURE", "Invalid policy version"); state.addProperty("policy_version", Json.integer(args, "version")); break;
            default: throw new Failure("INVALID_REQUEST", "Unknown synthetic fixture change");
        }
        event(state, "FIXTURE_CHANGE", opportunityId, ""); return Json.of("opportunity_id", opportunityId, "kind", kind);
    }
    private void event(JsonObject state, String type, String opportunityId, String proposalId) {
        Json.array(state, "audit").add(Json.of("type", type, "actor", principal, "opportunity_id", opportunityId,
                "proposal_id", proposalId, "at", Instant.now().toString(), "commit_generation", Json.integer(state, "generation") + 1));
    }
    static LocalDate date(String value) {
        Failure.require(value.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}"), "INVALID_DATE", "ISO date required");
        try { return LocalDate.parse(value); }
        catch (DateTimeParseException e) { throw new Failure("INVALID_DATE", "Invalid ISO date"); }
    }
}
