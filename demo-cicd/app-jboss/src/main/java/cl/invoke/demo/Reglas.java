package cl.invoke.demo;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.regex.Pattern;

/** Reglas de negocio y validaciones de entrada (sin dependencias del servidor: se prueban en el build). */
public final class Reglas {
    public static final BigDecimal MONTO_MAXIMO = new BigDecimal("10000.00");
    public static final List<String> RUBROS = List.of("Supermercado", "Farmacia", "Combustible", "Restaurantes", "Tecnología",
            "Vestuario", "Viajes", "Servicios básicos");
    public static final List<String> REGIONES = List.of("Metropolitana", "Valparaíso", "Biobío", "Antofagasta", "Los Lagos", "Coquimbo");
    public static final List<String> MEDIOS = List.of("tarjeta", "qr", "transferencia");
    private static final Pattern COMERCIO = Pattern.compile("^COM-\\d{4}$");
    private static final Pattern CUENTA = Pattern.compile("^CTA-\\d{6}$");
    private static final Pattern CODIGO = Pattern.compile("^[A-Z]{3}-\\d{8}$");

    private Reglas() {
    }

    /** Falla de negocio con su código HTTP y un mensaje para el usuario. */
    public static final class Falla extends RuntimeException {
        private final int estado;

        public Falla(int estado, String mensaje) {
            super(mensaje);
            this.estado = estado;
        }

        public int estado() {
            return estado;
        }
    }

    public static BigDecimal monto(String txt) {
        if (txt == null || txt.isBlank()) {
            throw new Falla(400, "Indica el monto");
        }
        BigDecimal m;
        try {
            m = new BigDecimal(txt.trim().replace(",", "."));
        } catch (NumberFormatException e) {
            throw new Falla(400, "El monto no es un número válido");
        }
        if (m.signum() <= 0) {
            throw new Falla(400, "El monto debe ser mayor que cero");
        }
        if (m.scale() > 2) {
            throw new Falla(400, "El monto admite hasta dos decimales");
        }
        if (m.compareTo(MONTO_MAXIMO) > 0) {
            throw new Falla(422, "El monto supera el máximo por operación (" + MONTO_MAXIMO.toPlainString() + " USD)");
        }
        return m.setScale(2);
    }

    /** Medio de pago: tarjeta, qr o transferencia. Acepta también los tipos de tarjeta (débito, crédito, prepago). */
    public static String medio(String m) {
        if (m == null || m.isBlank()) {
            return "tarjeta";
        }
        String x = m.trim().toLowerCase();
        return switch (x) {
            case "tarjeta", "débito", "debito", "crédito", "credito", "prepago" -> "tarjeta";
            case "qr" -> "qr";
            case "transferencia" -> "transferencia";
            default -> throw new Falla(400, "Medio de pago no válido: usa tarjeta, qr o transferencia");
        };
    }

    /** Canal de la operación: web (navegador de escritorio), app (móvil), pos o api. */
    public static String canal(String c) {
        if (c == null) {
            return "web";
        }
        String x = c.trim().toLowerCase();
        return switch (x) {
            case "app", "movil", "móvil" -> "app";
            case "pos", "api" -> x;
            default -> "web";
        };
    }

    public static String region(String r, String porDefecto) {
        return r != null && REGIONES.contains(r) ? r : porDefecto;
    }

    public static String rubro(String r) {
        return r != null && RUBROS.contains(r) ? r : null;
    }

    public static boolean comercioValido(String c) {
        return c != null && COMERCIO.matcher(c).matches();
    }

    public static boolean cuentaValida(String c) {
        return c != null && CUENTA.matcher(c).matches();
    }

    public static boolean codigoValido(String c) {
        return c != null && CODIGO.matcher(c).matches();
    }

    public static String codigo(String prefijo, long id) {
        return String.format("%s-%08d", prefijo, id % 100_000_000L);
    }

    /** Texto libre corto (glosa, motivo): sin saltos de línea y con largo máximo. */
    public static String texto(String t, int max) {
        if (t == null) {
            return null;
        }
        String x = t.replaceAll("[\\r\\n\\t]+", " ").trim();
        if (x.isEmpty()) {
            return null;
        }
        return x.length() > max ? x.substring(0, max) : x;
    }

    public static LocalDate fecha(String f) {
        if (f == null || f.isBlank()) {
            return null;
        }
        try {
            return LocalDate.parse(f.trim());
        } catch (java.time.format.DateTimeParseException e) {
            throw new Falla(400, "Fecha no válida (usa AAAA-MM-DD)");
        }
    }
}
