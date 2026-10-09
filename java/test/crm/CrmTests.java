package crm;

/** Parser/canonicalization assertions, called by the leased M1 check command. */
public final class CrmTests {
    private static int assertions;
    private static void check(boolean value, String message) {
        assertions++;
        if (!value) throw new AssertionError(message);
    }
    private static void rejects(String value) {
        try { Json.parse(value); throw new AssertionError("Accepted invalid JSON"); }
        catch (Failure e) { check(e.code.equals("INVALID_JSON"), "Unexpected parser error"); }
    }
    public static void main(String[] arguments) {
        rejects("{\"x\":1,\"x\":2}");
        rejects("{\"x\":null,\"x\":2}");
        rejects("{\"x\":NaN}");
        rejects("{\"x\":1.5}");
        rejects("{} {}");
        check(Json.canonical(Json.parse("{\"b\":2,\"a\":\"café\"}")).equals("{\"a\":\"café\",\"b\":2}"), "Canonical representation mismatch");
        check(Json.hash("abc").equals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"), "SHA-256 mismatch");
        check(Policy.date("2026-10-08").toString().equals("2026-10-08"), "ISO date round-trip failed");
        System.out.println(Json.canonical(Json.of("assertions", assertions, "suite", "java-m1", "passed", true)));
    }
}
