package crm;

import java.nio.channels.*;
import java.nio.file.*;

/** Owned test helper only; caller creates its store and readiness path. Always bounded. */
public final class LockHolder {
    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new IllegalArgumentException("store and readiness paths required");
        try (FileChannel channel = FileChannel.open(Path.of(args[0]).resolve("store.lock"), StandardOpenOption.CREATE, StandardOpenOption.WRITE);
             FileLock lock = channel.lock()) {
            Files.writeString(Path.of(args[1]), "ready");
            Thread.sleep(8000);
        }
    }
}
