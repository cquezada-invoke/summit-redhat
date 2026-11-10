package cl.invoke.demo;

import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;

/** Hash de claves con PBKDF2-HMAC-SHA256 (sal aleatoria). Formato: pbkdf2_sha256$iteraciones$sal$hash. */
public final class Claves {
    private static final int ITERACIONES = 120_000;
    private static final SecureRandom AZAR = new SecureRandom();

    private Claves() {
    }

    public static String hash(String clave) {
        byte[] sal = new byte[16];
        AZAR.nextBytes(sal);
        return formato(ITERACIONES, sal, derivar(clave, sal, ITERACIONES));
    }

    public static boolean verificar(String clave, String almacenado) {
        if (clave == null || almacenado == null) {
            return false;
        }
        String[] p = almacenado.split("\\$");
        if (p.length != 4 || !"pbkdf2_sha256".equals(p[0])) {
            return false;
        }
        try {
            int it = Integer.parseInt(p[1]);
            byte[] sal = Base64.getDecoder().decode(p[2]);
            byte[] esperado = Base64.getDecoder().decode(p[3]);
            return MessageDigest.isEqual(esperado, derivar(clave, sal, it));
        } catch (IllegalArgumentException e) {
            return false;
        }
    }

    /** Token aleatorio para la cookie de sesión (en la base se guarda solo su SHA-256). */
    public static String token() {
        byte[] b = new byte[32];
        AZAR.nextBytes(b);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(b);
    }

    public static String sha256(String s) {
        try {
            byte[] d = MessageDigest.getInstance("SHA-256").digest(s.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            return Base64.getUrlEncoder().withoutPadding().encodeToString(d);
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private static String formato(int it, byte[] sal, byte[] h) {
        Base64.Encoder e = Base64.getEncoder();
        return "pbkdf2_sha256$" + it + "$" + e.encodeToString(sal) + "$" + e.encodeToString(h);
    }

    private static byte[] derivar(String clave, byte[] sal, int it) {
        try {
            PBEKeySpec spec = new PBEKeySpec(clave.toCharArray(), sal, it, 256);
            return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded();
        } catch (java.security.GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }
}
