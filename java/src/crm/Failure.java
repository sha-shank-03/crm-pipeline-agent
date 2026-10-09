package crm;

/** A stable domain/protocol error. Never contains raw retrieved text. */
final class Failure extends RuntimeException {
    final String code;
    Failure(String code, String message) { super(message); this.code = code; }
    static void require(boolean condition, String code, String message) {
        if (!condition) throw new Failure(code, message);
    }
}
