import java.io.*;
import java.security.*;
import java.util.*;
import java.util.jar.*;

/** Verification only: never reads the keystore or any password. */
class VerifyMmhBundle {
    public static void main(String[] args) throws Exception {
        if (args.length != 2 || !args[1].matches("[0-9a-f]{64}")) throw new SecurityException("arguments");
        int count = 0;
        Set<String> names = new HashSet<>();
        try (JarFile jar = new JarFile(args[0], true)) {
            var entries = jar.entries();
            byte[] buffer = new byte[65536];
            while (entries.hasMoreElements()) {
                JarEntry e = entries.nextElement();
                if (!names.add(e.getName())) throw new SecurityException("duplicate entry");
                try (InputStream in = jar.getInputStream(e)) {
                    while (in.read(buffer) != -1) { /* triggers signature verification */ }
                }
                String name = e.getName().toUpperCase(Locale.ROOT);
                if (e.isDirectory() || name.equals("META-INF/MANIFEST.MF") ||
                    name.matches("META-INF/[^/]+\\.(SF|RSA|DSA|EC)")) continue;
                CodeSigner[] signers = e.getCodeSigners();
                if (signers == null || signers.length != 1) throw new SecurityException("unsigned/multiple signers");
                byte[] der = signers[0].getSignerCertPath().getCertificates().get(0).getEncoded();
                String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(der));
                if (!hash.equals(args[1])) throw new SecurityException("wrong signer");
                count++;
            }
        }
        if (count < 2) throw new SecurityException("empty payload");
        System.out.println("{\"verified\":true,\"payload_entries\":" + count + "}");
    }
}
