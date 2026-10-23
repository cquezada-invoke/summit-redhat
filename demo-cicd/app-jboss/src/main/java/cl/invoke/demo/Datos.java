package cl.invoke.demo;

import java.io.IOException;
import java.io.InputStream;
import java.util.Properties;

/** Datos de la versión desplegada, generados por Maven en cada build del pipeline. */
public final class Datos {
    private static final Properties P = new Properties();

    static {
        try (InputStream in = Datos.class.getResourceAsStream("/app.properties")) {
            if (in != null) {
                P.load(new java.io.InputStreamReader(in, java.nio.charset.StandardCharsets.UTF_8));
            }
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    private Datos() {
    }

    public static String version() {
        return P.getProperty("version", "desconocida");
    }

    public static String mensaje() {
        return P.getProperty("mensaje", "");
    }
}
