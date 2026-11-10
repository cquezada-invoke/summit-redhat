package cl.invoke.demo;

import java.math.BigDecimal;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Escritura de JSON mínima (mapas, listas, textos, números y booleanos). La lectura usa Jakarta JSON-P del servidor. */
public final class Json {
    private Json() {
    }

    public static Map<String, Object> m(Object... kv) {
        Map<String, Object> r = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            r.put(String.valueOf(kv[i]), kv[i + 1]);
        }
        return r;
    }

    public static String escribir(Object o) {
        StringBuilder sb = new StringBuilder();
        valor(sb, o);
        return sb.toString();
    }

    private static void valor(StringBuilder sb, Object o) {
        if (o == null) {
            sb.append("null");
        } else if (o instanceof BigDecimal b) {
            sb.append(b.toPlainString());
        } else if (o instanceof Number || o instanceof Boolean) {
            sb.append(o);
        } else if (o instanceof Map<?, ?> mapa) {
            sb.append('{');
            Iterator<? extends Map.Entry<?, ?>> it = mapa.entrySet().iterator();
            while (it.hasNext()) {
                Map.Entry<?, ?> e = it.next();
                texto(sb, String.valueOf(e.getKey()));
                sb.append(':');
                valor(sb, e.getValue());
                if (it.hasNext()) {
                    sb.append(',');
                }
            }
            sb.append('}');
        } else if (o instanceof List<?> lista) {
            sb.append('[');
            for (int i = 0; i < lista.size(); i++) {
                if (i > 0) {
                    sb.append(',');
                }
                valor(sb, lista.get(i));
            }
            sb.append(']');
        } else {
            texto(sb, o.toString());
        }
    }

    private static void texto(StringBuilder sb, String s) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                case '<' -> sb.append("\\u003c");
                default -> {
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
                }
            }
        }
        sb.append('"');
    }
}
