package crm;

import com.google.gson.*;
import java.nio.*;
import java.nio.charset.*;
import java.nio.file.*;
import java.time.*;
import java.util.*;

/** Exactly one bounded JSON request per owned JVM. */
public final class Main {
    public static void main(String[] arguments) {
        JsonObject response;
        try {
            Map<String, String> options = new HashMap<>();
            Failure.require(arguments.length == 6 || arguments.length == 8, "INVALID_LAUNCH", "Required: --store PATH --principal ID --as-of DATE");
            for (int i = 0; i < arguments.length; i += 2) {
                Failure.require(Set.of("--store", "--principal", "--as-of", "--test-failpoint").contains(arguments[i]) && !options.containsKey(arguments[i]), "INVALID_LAUNCH", "Unknown or duplicate launch option");
                options.put(arguments[i], arguments[i + 1]);
            }
            Failure.require(options.keySet().containsAll(Set.of("--store", "--principal", "--as-of")), "INVALID_LAUNCH", "Missing launcher option");
            Failure.require(!options.containsKey("--test-failpoint") || Set.of("before-rename", "after-rename", "atomic-unsupported").contains(options.get("--test-failpoint")), "INVALID_LAUNCH", "Unknown commit failpoint");
            byte[] input = System.in.readNBytes(65537);
            Failure.require(input.length <= 65536, "REQUEST_TOO_LARGE", "Request exceeds 64 KiB");
            String text;
            try { text = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(input)).toString(); }
            catch (CharacterCodingException e) { throw new Failure("INVALID_JSON", "Malformed UTF-8 input"); }
            JsonObject request = Json.object(Json.parse(text));
            Json.fields(request, "schema_version", "request_id", "operation", "args");
            Failure.require(Json.integer(request, "schema_version") == 1, "SCHEMA_VERSION", "Unsupported request schema");
            String requestId = Json.string(request, "request_id");
            Failure.require(requestId.matches("[A-Za-z0-9-]{1,96}"), "INVALID_REQUEST", "Invalid request ID");
            String operation = Json.string(request, "operation");
            Policy policy = new Policy(options.get("--principal"), Policy.date(options.get("--as-of")));
            Store store = new Store(Path.of(options.get("--store")), options.getOrDefault("--test-failpoint", ""));
            JsonObject result = store.execute(operation.equals("init"), state -> policy.execute(state, operation, requestId, Json.object(request.get("args"))));
            response = Json.of("schema_version", 1, "request_id", requestId, "ok", true, "result", result);
        } catch (Failure e) {
            response = Json.of("schema_version", 1, "ok", false, "error", Json.of("code", e.code, "message", e.getMessage()));
        } catch (Exception e) {
            System.err.println("CRM internal failure: " + e.getClass().getSimpleName());
            response = Json.of("schema_version", 1, "ok", false, "error", Json.of("code", "IO_FAILURE", "message", "CRM operation failed; reconcile before retry"));
        }
        String output = Json.canonical(response);
        if (output.getBytes(StandardCharsets.UTF_8).length > 262144) output = Json.canonical(Json.of("schema_version", 1, "ok", false, "error", Json.of("code", "RESPONSE_TOO_LARGE", "message", "Response exceeds limit")));
        // PrintStream's platform character encoding is not the wire encoding.
        byte[] wire = (output + "\n").getBytes(StandardCharsets.UTF_8);
        System.out.write(wire, 0, wire.length);
        System.out.flush();
    }
}
