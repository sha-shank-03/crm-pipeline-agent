package crm;

import com.google.gson.*;
import java.io.*;
import java.nio.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.function.Function;

/** Trusted local immutable snapshots; the lock is never renamed or removed. */
final class Store {
    static final long SNAPSHOT_LIMIT = 10L * 1024 * 1024;
    static final long TOTAL_LIMIT = 100L * 1024 * 1024;
    static final int GENERATION_LIMIT = 500;
    final Path directory;
    final String failpoint;
    record Result(JsonObject value, boolean changed) {}

    Store(Path directory) { this(directory, ""); }
    Store(Path directory, String failpoint) { this.directory = directory.toAbsolutePath().normalize(); this.failpoint = failpoint; }

    JsonObject execute(boolean initialize, Function<JsonObject, Result> action) throws IOException {
        Files.createDirectories(directory);
        try (FileChannel channel = FileChannel.open(directory.resolve("store.lock"), StandardOpenOption.CREATE, StandardOpenOption.WRITE)) {
            FileLock lock = acquire(channel);
            try (lock) {
                List<Path> committed = committed();
                Failure.require(initialize == committed.isEmpty(), initialize ? "STORE_EXISTS" : "STORE_MISSING",
                        initialize ? "Store already initialized" : "Initialize a synthetic store first");
                JsonObject state = initialize ? new JsonObject() : load(committed.get(committed.size() - 1));
                long generation = initialize ? -1 : Json.integer(state, "generation");
                if (!initialize) Failure.require(Json.integer(state, "schema_version") == 1, "SCHEMA_VERSION", "Unsupported store schema");
                Result result = action.apply(state);
                Failure.require(failpoint.isEmpty() || (Json.bool(state, "synthetic") && result.changed()), "INVALID_LAUNCH", "Commit failpoint requires a synthetic mutation");
                if (result.changed()) {
                    if (initialize) probeAtomicMove();
                    state.addProperty("generation", generation + 1);
                    commit(state, committed.size());
                }
                return result.value();
            }
        }
    }

    private FileLock acquire(FileChannel channel) throws IOException {
        long end = System.nanoTime() + 2_000_000_000L;
        do {
            FileLock lock = channel.tryLock();
            if (lock != null) return lock;
            try { Thread.sleep(25); }
            catch (InterruptedException e) { Thread.currentThread().interrupt(); throw new Failure("CANCELLED", "Lock acquisition cancelled"); }
        } while (System.nanoTime() < end);
        throw new Failure("STORE_BUSY", "Store lock timeout");
    }

    private List<Path> committed() throws IOException {
        try (var files = Files.list(directory)) {
            return files.filter(p -> p.getFileName().toString().matches("generation-[0-9]{6}\\.json")).sorted().toList();
        }
    }
    private JsonObject load(Path path) throws IOException {
        Failure.require(Files.size(path) <= SNAPSHOT_LIMIT, "STORE_CORRUPT", "Snapshot exceeds size limit");
        try {
            JsonObject state = Json.object(Json.parse(Files.readString(path, StandardCharsets.UTF_8)));
            String expected = String.format(Locale.ROOT, "generation-%06d.json", Json.integer(state, "generation"));
            Failure.require(path.getFileName().toString().equals(expected), "STORE_CORRUPT", "Generation mismatch");
            for (String key : List.of("accounts", "opportunities", "notes", "proposals", "approvals", "receipts", "operations")) Json.object(state.get(key));
            Json.array(state, "audit");
            return state;
        } catch (Failure e) { throw new Failure("STORE_CORRUPT", "Latest committed snapshot is invalid"); }
    }
    private void probeAtomicMove() throws IOException {
        Failure.require(!failpoint.equals("atomic-unsupported"), "ATOMIC_MOVE_UNSUPPORTED", "Injected synthetic atomic capability failure");
        Path from = Files.createTempFile(directory, ".atomic-", ".tmp");
        Path to = directory.resolve(from.getFileName().toString() + ".moved");
        try { Files.move(from, to, StandardCopyOption.ATOMIC_MOVE); }
        catch (AtomicMoveNotSupportedException e) { throw new Failure("ATOMIC_MOVE_UNSUPPORTED", "Store requires atomic rename"); }
        finally { Files.deleteIfExists(from); Files.deleteIfExists(to); }
    }
    private void commit(JsonObject state, int count) throws IOException {
        byte[] bytes = (Json.canonical(state) + "\n").getBytes(StandardCharsets.UTF_8);
        Failure.require(count < GENERATION_LIMIT, "STORE_CAPACITY", "Generation limit reached");
        Failure.require(bytes.length <= SNAPSHOT_LIMIT, "STORE_CAPACITY", "Snapshot size limit reached");
        long used = 0;
        try (var files = Files.list(directory)) {
            for (Path path : files.toList()) if (Files.isRegularFile(path) && !path.getFileName().toString().equals("store.lock")) used += Files.size(path);
        }
        Failure.require(used + bytes.length <= TOTAL_LIMIT, "STORE_CAPACITY", "Total generated store budget reached");
        Path target = directory.resolve(String.format(Locale.ROOT, "generation-%06d.json", Json.integer(state, "generation")));
        Failure.require(!Files.exists(target), "STORE_CORRUPT", "Generation already exists");
        Path temporary = Files.createTempFile(directory, ".snapshot-", ".tmp");
        try {
            try (FileChannel out = FileChannel.open(temporary, StandardOpenOption.WRITE)) {
                ByteBuffer buffer = ByteBuffer.wrap(bytes);
                while (buffer.hasRemaining()) out.write(buffer);
                out.force(true);
            }
            if (failpoint.equals("before-rename")) Runtime.getRuntime().halt(71);
            Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE);
            if (failpoint.equals("after-rename")) Runtime.getRuntime().halt(72);
        } catch (AtomicMoveNotSupportedException e) { throw new Failure("ATOMIC_MOVE_UNSUPPORTED", "Store requires atomic rename"); }
        finally { Files.deleteIfExists(temporary); }
    }
}
