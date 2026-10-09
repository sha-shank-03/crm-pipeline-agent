package crm;

import com.google.gson.*;
import com.google.gson.stream.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.util.*;

final class Json {
    static final Gson GSON = new GsonBuilder().disableHtmlEscaping().serializeNulls().create();

    static JsonElement parse(String text) {
        try (JsonReader reader = new JsonReader(new StringReader(text))) {
            reader.setStrictness(Strictness.STRICT);
            JsonElement result = read(reader, 0);
            Failure.require(reader.peek() == JsonToken.END_DOCUMENT, "INVALID_JSON", "Trailing JSON input");
            return result;
        } catch (IOException | IllegalStateException | NumberFormatException e) {
            throw new Failure("INVALID_JSON", "Invalid JSON input");
        }
    }

    private static JsonElement read(JsonReader reader, int depth) throws IOException {
        Failure.require(depth <= 12, "INVALID_JSON", "JSON nesting exceeds limit");
        switch (reader.peek()) {
            case BEGIN_OBJECT:
                JsonObject object = new JsonObject();
                reader.beginObject();
                while (reader.hasNext()) {
                    String key = reader.nextName();
                    unicode(key);
                    Failure.require(!object.has(key), "INVALID_JSON", "Duplicate object key");
                    object.add(key, read(reader, depth + 1));
                }
                reader.endObject();
                return object;
            case BEGIN_ARRAY:
                JsonArray array = new JsonArray();
                reader.beginArray();
                while (reader.hasNext()) array.add(read(reader, depth + 1));
                reader.endArray();
                return array;
            case STRING: String text = reader.nextString(); unicode(text); return new JsonPrimitive(text);
            case NUMBER:
                String number = reader.nextString();
                Failure.require(number.matches("-?(0|[1-9][0-9]*)"), "INVALID_JSON", "Only integer JSON numbers are supported");
                return new JsonPrimitive(Long.parseLong(number));
            case BOOLEAN: return new JsonPrimitive(reader.nextBoolean());
            case NULL: reader.nextNull(); return JsonNull.INSTANCE;
            default: throw new Failure("INVALID_JSON", "Unexpected JSON token");
        }
    }

    private static void unicode(String text) {
        for (int i = 0; i < text.length(); i++) {
            char value = text.charAt(i);
            if (Character.isHighSurrogate(value)) {
                Failure.require(i + 1 < text.length() && Character.isLowSurrogate(text.charAt(i + 1)), "INVALID_JSON", "Unpaired Unicode surrogate");
                i++;
            } else Failure.require(!Character.isLowSurrogate(value), "INVALID_JSON", "Unpaired Unicode surrogate");
        }
    }

    static JsonObject object(JsonElement value) {
        Failure.require(value != null && value.isJsonObject(), "INVALID_REQUEST", "Expected object");
        return value.getAsJsonObject();
    }
    static JsonArray array(JsonObject value, String key) {
        Failure.require(value.has(key) && value.get(key).isJsonArray(), "INVALID_REQUEST", "Expected array: " + key);
        return value.getAsJsonArray(key);
    }
    static String string(JsonObject value, String key) {
        JsonElement item = value.get(key);
        Failure.require(item != null && item.isJsonPrimitive() && item.getAsJsonPrimitive().isString(),
                "INVALID_REQUEST", "Expected string: " + key);
        return item.getAsString();
    }
    static long integer(JsonObject value, String key) {
        JsonElement item = value.get(key);
        Failure.require(item != null && item.isJsonPrimitive() && item.getAsJsonPrimitive().isNumber(),
                "INVALID_REQUEST", "Expected integer: " + key);
        try { return Long.parseLong(item.getAsString()); }
        catch (NumberFormatException e) { throw new Failure("INVALID_REQUEST", "Expected integer: " + key); }
    }
    static boolean bool(JsonObject value, String key) {
        JsonElement item = value.get(key);
        Failure.require(item != null && item.isJsonPrimitive() && item.getAsJsonPrimitive().isBoolean(),
                "INVALID_REQUEST", "Expected boolean: " + key);
        return item.getAsBoolean();
    }
    static void fields(JsonObject value, String... names) {
        Set<String> expected = new HashSet<>(Arrays.asList(names));
        Failure.require(value.keySet().equals(expected), "INVALID_REQUEST", "Missing or unknown fields");
    }
    static JsonObject of(Object... pairs) {
        JsonObject result = new JsonObject();
        for (int i = 0; i < pairs.length; i += 2) result.add((String) pairs[i], GSON.toJsonTree(pairs[i + 1]));
        return result;
    }
    static String canonical(JsonElement value) {
        if (value.isJsonObject()) {
            List<String> keys = new ArrayList<>(value.getAsJsonObject().keySet());
            Collections.sort(keys);
            List<String> parts = new ArrayList<>();
            for (String key : keys) parts.add(GSON.toJson(key) + ":" + canonical(value.getAsJsonObject().get(key)));
            return "{" + String.join(",", parts) + "}";
        }
        if (value.isJsonArray()) {
            List<String> parts = new ArrayList<>();
            for (JsonElement item : value.getAsJsonArray()) parts.add(canonical(item));
            return "[" + String.join(",", parts) + "]";
        }
        return GSON.toJson(value);
    }
    static String hash(String text) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }
}
