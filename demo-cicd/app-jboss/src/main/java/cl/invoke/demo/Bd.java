package cl.invoke.demo;

import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;
import java.util.Random;
import java.util.logging.Logger;
import javax.naming.InitialContext;
import javax.sql.DataSource;

/** Conexión a PostgreSQL (datasource que deja Ansible en JBoss) y estructura de la app, aplicada una vez al primer uso. */
public final class Bd {
    public static final String JNDI = "java:jboss/datasources/app-jboss";
    private static final Logger LOG = Logger.getLogger(Bd.class.getName());
    private static volatile DataSource ds;
    private static volatile boolean lista;

    private Bd() {
    }

    /** Operación dentro de una transacción. */
    public interface Tx<T> {
        T ejecutar(Connection c) throws Exception;
    }

    /** Conexión sin preparar la estructura (para el chequeo de la portada). */
    public static Connection cruda() throws Exception {
        if (ds == null) {
            ds = (DataSource) new InitialContext().lookup(JNDI);
        }
        return ds.getConnection();
    }

    public static Connection conexion() throws Exception {
        Connection c = cruda();
        if (!lista) {
            synchronized (Bd.class) {
                if (!lista) {
                    try {
                        preparar(c);
                        lista = true;
                    } catch (Exception e) {
                        c.close();
                        throw e;
                    }
                }
            }
        }
        return c;
    }

    public static <T> T tx(Tx<T> t) throws Exception {
        try (Connection c = conexion()) {
            c.setAutoCommit(false);
            try {
                T r = t.ejecutar(c);
                c.commit();
                return r;
            } catch (Exception e) {
                c.rollback();
                throw e;
            } finally {
                c.setAutoCommit(true);
            }
        }
    }

    // ───────── estructura y usuarios de demo ─────────
    private static void preparar(Connection c) throws Exception {
        c.setAutoCommit(false);
        try (Statement s = c.createStatement()) {
            s.execute("SELECT pg_advisory_xact_lock(7240012026)");   // un solo JBoss a la vez
            for (String sql : sentencias(recurso("/bd/esquema.sql"))) {
                s.execute(sql);
            }
            sembrarUsuarios(c);
            c.commit();
            LOG.info("App de pagos: estructura de base de datos lista");
        } catch (Exception e) {
            c.rollback();
            throw e;
        } finally {
            c.setAutoCommit(true);
        }
    }

    static List<String> sentencias(String sql) {
        StringBuilder sb = new StringBuilder();
        for (String linea : sql.split("\n")) {
            if (!linea.trim().startsWith("--")) {
                sb.append(linea).append('\n');
            }
        }
        List<String> r = new ArrayList<>();
        for (String p : sb.toString().split(";")) {
            if (!p.isBlank()) {
                r.add(p.trim());
            }
        }
        return r;
    }

    private static String recurso(String ruta) throws IOException {
        try (InputStream in = Bd.class.getResourceAsStream(ruta)) {
            if (in == null) {
                throw new IOException("no está el recurso " + ruta);
            }
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    private static void sembrarUsuarios(Connection c) throws Exception {
        Properties p = new Properties();
        try (InputStream in = Bd.class.getResourceAsStream("/bd/usuarios-demo.properties")) {
            if (in == null) {
                return;
            }
            p.load(new InputStreamReader(in, StandardCharsets.UTF_8));
        }
        for (String usuario : p.stringPropertyNames()) {
            String[] f = p.getProperty(usuario).split("\\|");
            if (f.length < 6) {
                continue;
            }
            try (PreparedStatement q = c.prepareStatement("SELECT 1 FROM app_usuarios WHERE usuario = ?")) {
                q.setString(1, usuario);
                try (ResultSet r = q.executeQuery()) {
                    if (r.next()) {
                        continue;
                    }
                }
            }
            int uid;
            try (PreparedStatement q = c.prepareStatement(
                    "INSERT INTO app_usuarios (usuario, nombre, rol, region, hash) VALUES (?, ?, ?, ?, ?) RETURNING id")) {
                q.setString(1, usuario);
                q.setString(2, f[1].trim());
                q.setString(3, f[2].trim());
                q.setString(4, f[3].trim());
                q.setString(5, Claves.hash(f[0].trim()));
                try (ResultSet r = q.executeQuery()) {
                    r.next();
                    uid = r.getInt(1);
                }
            }
            int cid;
            try (PreparedStatement q = c.prepareStatement(
                    "INSERT INTO app_cuentas (numero, usuario_id) VALUES (?, ?) ON CONFLICT (numero) DO NOTHING RETURNING id")) {
                q.setString(1, f[5].trim());
                q.setInt(2, uid);
                try (ResultSet r = q.executeQuery()) {
                    if (!r.next()) {
                        continue;
                    }
                    cid = r.getInt(1);
                }
            }
            historiaInicial(c, cid, new BigDecimal(f[4].trim()), f[3].trim(), usuario.hashCode());
        }
    }

    /** Abono inicial y algunos pagos de los últimos días, para que el panel no parta vacío. */
    private static void historiaInicial(Connection c, int cuenta, BigDecimal inicial, String region, long semilla) throws Exception {
        Random rnd = new Random(semilla);
        String[][] comercios = {{"COM-0001", "Supermercado Los Andes", "Supermercado"}, {"COM-0002", "Farmacia Central", "Farmacia"},
            {"COM-0003", "Estación Ruta 5", "Combustible"}, {"COM-0004", "Café del Puerto", "Restaurantes"},
            {"COM-0005", "TecnoHogar", "Tecnología"}, {"COM-0006", "Moda Austral", "Vestuario"},
            {"COM-0008", "Aguas y Luz del Valle", "Servicios básicos"}, {"COM-0010", "Sushi Pacífico", "Restaurantes"}};
        String[] medios = {"tarjeta", "tarjeta", "qr", "qr", "transferencia"};
        Instant ahora = Instant.now();
        BigDecimal saldo = inicial;
        movimiento(c, "ABO", cuenta, "abono", null, null, "Abono inicial", null, region, "web", inicial, ahora.minus(20, ChronoUnit.DAYS));
        for (int i = 0; i < 10; i++) {
            String[] com = comercios[rnd.nextInt(comercios.length)];
            BigDecimal m = BigDecimal.valueOf(5 + rnd.nextInt(14000) / 100.0).setScale(2, java.math.RoundingMode.HALF_UP);
            Instant t = i < 2 ? ahora.minus(rnd.nextInt(180) + 5, ChronoUnit.MINUTES)
                : ahora.minus(rnd.nextInt(18) + 1, ChronoUnit.DAYS).minus(rnd.nextInt(600), ChronoUnit.MINUTES);
            movimiento(c, "PAG", cuenta, "pago", medios[rnd.nextInt(medios.length)], com[0], com[1], com[2], region,
                rnd.nextBoolean() ? "web" : "app", m.negate(), t);
            saldo = saldo.subtract(m);
        }
        try (PreparedStatement q = c.prepareStatement("UPDATE app_cuentas SET saldo = ? WHERE id = ?")) {
            q.setBigDecimal(1, saldo);
            q.setInt(2, cuenta);
            q.executeUpdate();
        }
    }

    private static void movimiento(Connection c, String pref, int cuenta, String tipo, String medio, String comercio, String contraparte,
                                   String rubro, String region, String canal, BigDecimal monto, Instant creado) throws Exception {
        long id = siguienteId(c);
        try (PreparedStatement q = c.prepareStatement("INSERT INTO app_movimientos (id, codigo, cuenta_id, tipo, medio, comercio, contraparte,"
                + " rubro, region, canal, monto, estado, creado) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'aprobado', ?)")) {
            q.setLong(1, id);
            q.setString(2, Reglas.codigo(pref, id));
            q.setInt(3, cuenta);
            q.setString(4, tipo);
            q.setString(5, medio);
            q.setString(6, comercio);
            q.setString(7, contraparte);
            q.setString(8, rubro);
            q.setString(9, region);
            q.setString(10, canal);
            q.setBigDecimal(11, monto);
            q.setTimestamp(12, Timestamp.from(creado));
            q.executeUpdate();
        }
    }

    public static long siguienteId(Connection c) throws Exception {
        try (Statement s = c.createStatement();
             ResultSet r = s.executeQuery("SELECT nextval(pg_get_serial_sequence('app_movimientos', 'id'))")) {
            r.next();
            return r.getLong(1);
        }
    }
}
