package cl.invoke.otp;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;

/** Publica el mensaje en el "teléfono" simulado de la demo (app mi-app, endpoint interno). */
final class Telefono {

    private static final HttpClient HTTP = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();

    private Telefono() {
    }

    static boolean publicar(Map<String, String> cfg, String canal, String destino, String asunto, String texto, String nota) {
        String url = cfg.getOrDefault("url-telefono", "http://192.168.122.1:8096/interno/mensaje");
        String json = "{\"canal\":" + q(canal) + ",\"destino\":" + q(destino) + ",\"asunto\":" + q(asunto)
                + ",\"texto\":" + q(texto) + ",\"nota\":" + q(nota) + "}";
        try {
            HttpResponse<Void> r = HTTP.send(HttpRequest.newBuilder(URI.create(url))
                    .timeout(Duration.ofSeconds(5))
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(json)).build(), HttpResponse.BodyHandlers.discarding());
            return r.statusCode() / 100 == 2;
        } catch (Exception e) {
            OtpBase.LOG.warnf("No se pudo publicar en el teléfono simulado (%s): %s", url, e.toString());
            return false;
        }
    }

    private static String q(String s) {
        if (s == null) {
            return "null";
        }
        StringBuilder b = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"' -> b.append("\\\"");
                case '\\' -> b.append("\\\\");
                case '\n' -> b.append("\\n");
                case '\r' -> b.append("\\r");
                case '\t' -> b.append("\\t");
                default -> {
                    if (c < 0x20) {
                        b.append(String.format("\\u%04x", (int) c));
                    } else {
                        b.append(c);
                    }
                }
            }
        }
        return b.append('"').toString();
    }
}
