package cl.invoke.demo;

import static cl.invoke.demo.Json.m;

import cl.invoke.demo.Reglas.Falla;
import jakarta.json.JsonObject;
import jakarta.json.JsonReader;
import jakarta.json.JsonValue;
import jakarta.servlet.annotation.WebServlet;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.io.StringReader;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * API de la App de pagos (JSON sobre HTTP). Toda operación de la interfaz pasa por aquí, así el espejo del balanceador
 * al data lake recibe requests de negocio con rubro, región, medio, monto y canal.
 * Las rutas son las mismas que usa la prueba de carga (demo-datalake/colector/carga-pagos.py).
 */
@WebServlet(urlPatterns = {"/login", "/logout", "/sesion", "/saldo", "/cuentas", "/comercios", "/comercios/*", "/movimientos",
    "/pagos", "/pagos/*", "/qr/pagar", "/transferencias", "/reembolsos", "/reembolsos/*", "/admin/*"})
public class Api extends HttpServlet {
    static final String COOKIE = "pagos_sesion";
    static final ZoneId ZONA = ZoneId.of("America/Santiago");
    private static final Logger LOG = Logger.getLogger(Api.class.getName());
    private static final String MOV = "SELECT m.codigo, m.tipo, m.creado, m.medio, m.comercio, m.contraparte, m.rubro, m.region, m.canal,"
        + " m.monto, m.estado, m.glosa, m.ref_codigo, r.estado AS reembolso FROM app_movimientos m"
        + " LEFT JOIN app_reembolsos r ON r.pago_codigo = m.codigo AND r.estado <> 'rechazado' ";

    /** Usuario con sesión vigente. */
    record Usuario(int id, String usuario, String nombre, String rol, String region, int cuentaId, String cuenta) {
        boolean supervisor() {
            return "supervisor".equals(rol);
        }

        Map<String, Object> json() {
            return m("usuario", usuario, "nombre", nombre, "rol", rol, "region", region, "cuenta", cuenta);
        }
    }

    @Override
    protected void service(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        req.setCharacterEncoding("UTF-8");
        String ruta = req.getServletPath() + (req.getPathInfo() == null ? "" : req.getPathInfo());
        String metodo = req.getMethod();
        try {
            if (!"GET".equals(metodo) && !"POST".equals(metodo)) {
                throw new Falla(405, "Método no permitido");
            }
            JsonObject b = "POST".equals(metodo) ? cuerpo(req) : JsonValue.EMPTY_JSON_OBJECT;
            if ("/login".equals(ruta) && "POST".equals(metodo)) {
                login(req, resp, b);
                return;
            }
            if ("/logout".equals(ruta)) {
                logout(req, resp);
                return;
            }
            Usuario u = sesion(req);
            if (u == null) {
                throw new Falla(401, "Inicia sesión para continuar");
            }
            responder(resp, 200, despachar(metodo, ruta, u, b, req));
        } catch (Falla f) {
            responder(resp, f.estado(), m("error", f.getMessage()));
        } catch (Exception e) {
            LOG.log(Level.WARNING, "App de pagos: error en " + metodo + " " + ruta, e);
            responder(resp, 500, m("error", "No se pudo completar la operación. Intenta nuevamente."));
        }
    }

    private Object despachar(String metodo, String ruta, Usuario u, JsonObject b, HttpServletRequest req) throws Exception {
        String[] p = ruta.split("/");     // "", "pagos", "PAG-...", "anular"
        boolean get = "GET".equals(metodo);
        switch (p[1]) {
            case "sesion":
                return u.json();
            case "saldo":
                return panel(u);
            case "cuentas":
                return cuentas(u);
            case "comercios":
                if (p.length == 2) {
                    return comercios();
                }
                if (p.length == 4 && "liquidacion".equals(p[3]) && get) {
                    return liquidacion(u, p[2], Reglas.fecha(req.getParameter("fecha")));
                }
                break;
            case "movimientos":
                return historial(u, req);
            case "pagos":
                if (p.length == 2 && !get) {
                    return pagar(u, b, false);
                }
                if (p.length == 2) {
                    return historial(u, req);
                }
                if (p.length == 3 && get) {
                    return detalle(u, p[2]);
                }
                if (p.length == 4 && !get && "confirmar".equals(p[3])) {
                    return detalle(u, p[2]);   // los pagos se aprueban al crearse; confirmar devuelve su estado
                }
                if (p.length == 4 && !get && "anular".equals(p[3])) {
                    return anular(u, p[2], b);
                }
                break;
            case "qr":
                if (!get) {
                    return pagar(u, b, true);
                }
                break;
            case "transferencias":
                if (!get) {
                    return transferir(u, b);
                }
                break;
            case "reembolsos":
                if (p.length == 2) {
                    return get ? reembolsos(u) : pedirReembolso(u, b);
                }
                if (p.length == 4 && !get && ("aprobar".equals(p[3]) || "rechazar".equals(p[3]))) {
                    return resolverReembolso(u, p[2], "aprobar".equals(p[3]));
                }
                break;
            case "admin":
                if (p.length == 3 && "resumen".equals(p[2]) && get) {
                    return resumen(u, Reglas.fecha(req.getParameter("fecha")));
                }
                break;
            default:
                break;
        }
        throw new Falla(404, "Ruta no encontrada");
    }

    // ───────── sesión ─────────
    private void login(HttpServletRequest req, HttpServletResponse resp, JsonObject b) throws Exception {
        String usuario = Reglas.texto(str(b, "usuario"), 60);
        String clave = str(b, "clave");
        if (usuario == null || clave == null || clave.isEmpty()) {
            throw new Falla(400, "Indica usuario y clave");
        }
        String canal = Reglas.canal(str(b, "canal"));
        Usuario u = null;
        try (Connection c = Bd.conexion()) {
            try (PreparedStatement q = c.prepareStatement("SELECT hash FROM app_usuarios WHERE usuario = ?")) {
                q.setString(1, usuario.toLowerCase());
                try (ResultSet r = q.executeQuery()) {
                    if (!r.next() || !Claves.verificar(clave, r.getString(1))) {
                        throw new Falla(401, "Usuario o clave incorrectos");
                    }
                }
            }
            String token = Claves.token();
            try (PreparedStatement q = c.prepareStatement("DELETE FROM app_sesiones WHERE expira < now()")) {
                q.executeUpdate();
            }
            try (PreparedStatement q = c.prepareStatement("INSERT INTO app_sesiones (token_sha, usuario_id, canal, expira)"
                    + " SELECT ?, id, ?, now() + interval '8 hours' FROM app_usuarios WHERE usuario = ?")) {
                q.setString(1, Claves.sha256(token));
                q.setString(2, canal);
                q.setString(3, usuario.toLowerCase());
                q.executeUpdate();
            }
            u = buscar(c, Claves.sha256(token));
            resp.addHeader("Set-Cookie", COOKIE + "=" + token + "; Path=" + ruta(req) + "; HttpOnly; SameSite=Strict; Max-Age=28800");
        }
        responder(resp, 200, u == null ? m() : u.json());
    }

    private void logout(HttpServletRequest req, HttpServletResponse resp) throws Exception {
        String t = token(req);
        if (t != null) {
            try (Connection c = Bd.conexion(); PreparedStatement q = c.prepareStatement("DELETE FROM app_sesiones WHERE token_sha = ?")) {
                q.setString(1, Claves.sha256(t));
                q.executeUpdate();
            }
        }
        resp.addHeader("Set-Cookie", COOKIE + "=; Path=" + ruta(req) + "; HttpOnly; SameSite=Strict; Max-Age=0");
        responder(resp, 200, m("ok", true));
    }

    private static String ruta(HttpServletRequest req) {
        return req.getContextPath().isEmpty() ? "/" : req.getContextPath() + "/";
    }

    private static String token(HttpServletRequest req) {
        if (req.getCookies() != null) {
            for (Cookie k : req.getCookies()) {
                if (COOKIE.equals(k.getName()) && k.getValue() != null && !k.getValue().isBlank()) {
                    return k.getValue();
                }
            }
        }
        return null;
    }

    private static Usuario sesion(HttpServletRequest req) throws Exception {
        String t = token(req);
        if (t == null) {
            return null;
        }
        try (Connection c = Bd.conexion()) {
            return buscar(c, Claves.sha256(t));
        }
    }

    private static Usuario buscar(Connection c, String tokenSha) throws Exception {
        try (PreparedStatement q = c.prepareStatement("SELECT u.id, u.usuario, u.nombre, u.rol, u.region, k.id, k.numero"
                + " FROM app_sesiones s JOIN app_usuarios u ON u.id = s.usuario_id JOIN app_cuentas k ON k.usuario_id = u.id"
                + " WHERE s.token_sha = ? AND s.expira > now() ORDER BY k.id LIMIT 1")) {
            q.setString(1, tokenSha);
            try (ResultSet r = q.executeQuery()) {
                return r.next() ? new Usuario(r.getInt(1), r.getString(2), r.getString(3), r.getString(4), r.getString(5), r.getInt(6),
                    r.getString(7)) : null;
            }
        }
    }

    // ───────── consultas ─────────
    private Object panel(Usuario u) throws Exception {
        try (Connection c = Bd.conexion()) {
            BigDecimal saldo;
            String moneda;
            try (PreparedStatement q = c.prepareStatement("SELECT saldo, moneda FROM app_cuentas WHERE id = ?")) {
                q.setInt(1, u.cuentaId());
                try (ResultSet r = q.executeQuery()) {
                    r.next();
                    saldo = r.getBigDecimal(1);
                    moneda = r.getString(2);
                }
            }
            BigDecimal gastoMes;
            int pagosMes;
            try (PreparedStatement q = c.prepareStatement("SELECT coalesce(sum(-monto), 0), count(*) FROM app_movimientos WHERE cuenta_id = ?"
                    + " AND tipo IN ('pago', 'transferencia') AND monto < 0 AND estado <> 'anulado'"
                    + " AND creado >= date_trunc('month', now() AT TIME ZONE 'America/Santiago') AT TIME ZONE 'America/Santiago'")) {
                q.setInt(1, u.cuentaId());
                try (ResultSet r = q.executeQuery()) {
                    r.next();
                    gastoMes = r.getBigDecimal(1);
                    pagosMes = r.getInt(2);
                }
            }
            List<Object> ult = new ArrayList<>();
            try (PreparedStatement q = c.prepareStatement(MOV + "WHERE m.cuenta_id = ? ORDER BY m.creado DESC, m.id DESC LIMIT 8")) {
                q.setInt(1, u.cuentaId());
                try (ResultSet r = q.executeQuery()) {
                    while (r.next()) {
                        ult.add(mov(r));
                    }
                }
            }
            return m("cuenta", u.cuenta(), "titular", u.nombre(), "saldo", saldo, "moneda", moneda, "gasto_mes", gastoMes,
                "operaciones_mes", pagosMes, "movimientos", ult);
        }
    }

    private Object cuentas(Usuario u) throws Exception {
        List<Object> l = new ArrayList<>();
        try (Connection c = Bd.conexion(); PreparedStatement q = c.prepareStatement(
                "SELECT k.numero, u.nombre FROM app_cuentas k JOIN app_usuarios u ON u.id = k.usuario_id WHERE k.id <> ? ORDER BY u.nombre")) {
            q.setInt(1, u.cuentaId());
            try (ResultSet r = q.executeQuery()) {
                while (r.next()) {
                    l.add(m("cuenta", r.getString(1), "titular", r.getString(2)));
                }
            }
        }
        return m("cuentas", l);
    }

    private Object comercios() throws Exception {
        List<Object> l = new ArrayList<>();
        try (Connection c = Bd.conexion(); PreparedStatement q = c.prepareStatement(
                "SELECT codigo, nombre, rubro FROM app_comercios ORDER BY nombre")) {
            try (ResultSet r = q.executeQuery()) {
                while (r.next()) {
                    l.add(m("codigo", r.getString(1), "nombre", r.getString(2), "rubro", r.getString(3)));
                }
            }
        }
        return m("comercios", l, "rubros", Reglas.RUBROS, "regiones", Reglas.REGIONES);
    }

    private Object historial(Usuario u, HttpServletRequest req) throws Exception {
        StringBuilder sql = new StringBuilder(MOV + "WHERE m.cuenta_id = ?");
        List<Object> args = new ArrayList<>();
        args.add(u.cuentaId());
        LocalDate desde = Reglas.fecha(req.getParameter("desde"));
        LocalDate hasta = Reglas.fecha(req.getParameter("hasta"));
        if (desde != null) {
            sql.append(" AND (m.creado AT TIME ZONE 'America/Santiago')::date >= ?");
            args.add(java.sql.Date.valueOf(desde));
        }
        if (hasta != null) {
            sql.append(" AND (m.creado AT TIME ZONE 'America/Santiago')::date <= ?");
            args.add(java.sql.Date.valueOf(hasta));
        }
        filtro(sql, args, "m.tipo", req.getParameter("tipo"));
        filtro(sql, args, "m.medio", req.getParameter("medio"));
        filtro(sql, args, "m.rubro", req.getParameter("rubro"));
        filtro(sql, args, "m.estado", req.getParameter("estado"));
        String texto = Reglas.texto(req.getParameter("texto"), 60);
        if (texto != null) {
            sql.append(" AND (m.contraparte ILIKE ? OR m.codigo ILIKE ? OR m.glosa ILIKE ?)");
            String t = "%" + texto.replace("%", "").replace("_", "") + "%";
            args.add(t);
            args.add(t);
            args.add(t);
        }
        sql.append(" ORDER BY m.creado DESC, m.id DESC LIMIT 200");
        List<Object> l = new ArrayList<>();
        BigDecimal entradas = BigDecimal.ZERO;
        BigDecimal salidas = BigDecimal.ZERO;
        try (Connection c = Bd.conexion(); PreparedStatement q = c.prepareStatement(sql.toString())) {
            for (int i = 0; i < args.size(); i++) {
                q.setObject(i + 1, args.get(i));
            }
            try (ResultSet r = q.executeQuery()) {
                while (r.next()) {
                    Map<String, Object> x = mov(r);
                    BigDecimal mo = (BigDecimal) x.get("monto");
                    if (mo.signum() > 0) {
                        entradas = entradas.add(mo);
                    } else {
                        salidas = salidas.add(mo.negate());
                    }
                    l.add(x);
                }
            }
        }
        return m("movimientos", l, "total", l.size(), "entradas", entradas, "salidas", salidas);
    }

    private static void filtro(StringBuilder sql, List<Object> args, String col, String valor) {
        String v = Reglas.texto(valor, 40);
        if (v != null) {
            sql.append(" AND ").append(col).append(" = ?");
            args.add(v);
        }
    }

    private Object detalle(Usuario u, String codigo) throws Exception {
        if (!Reglas.codigoValido(codigo)) {
            throw new Falla(400, "Código de operación no válido");
        }
        try (Connection c = Bd.conexion(); PreparedStatement q = c.prepareStatement(MOV + "WHERE m.codigo = ? AND m.cuenta_id = ?")) {
            q.setString(1, codigo);
            q.setInt(2, u.cuentaId());
            try (ResultSet r = q.executeQuery()) {
                if (!r.next()) {
                    throw new Falla(404, "No existe la operación " + codigo + " en tu cuenta");
                }
                return mov(r);
            }
        }
    }

    private Object reembolsos(Usuario u) throws Exception {
        List<Object> l = new ArrayList<>();
        try (Connection c = Bd.conexion(); PreparedStatement q = c.prepareStatement("SELECT r.codigo, r.pago_codigo, r.monto, r.motivo, r.estado,"
                + " r.creado, r.resuelto, m.contraparte, m.rubro FROM app_reembolsos r LEFT JOIN app_movimientos m ON m.codigo = r.pago_codigo"
                + " WHERE r.cuenta_id = ? ORDER BY r.creado DESC LIMIT 100")) {
            q.setInt(1, u.cuentaId());
            try (ResultSet r = q.executeQuery()) {
                while (r.next()) {
                    l.add(reembolso(r));
                }
            }
        }
        return m("reembolsos", l);
    }

    private Object resumen(Usuario u, LocalDate fecha) throws Exception {
        soloSupervisor(u);
        LocalDate dia = fecha == null ? LocalDate.now(ZONA) : fecha;
        java.sql.Date d = java.sql.Date.valueOf(dia);
        String delDia = " (creado AT TIME ZONE 'America/Santiago')::date = ? ";
        try (Connection c = Bd.conexion()) {
            List<Object> rubros = filas(c, "SELECT coalesce(rubro, 'Sin rubro'), count(*) FILTER (WHERE estado <> 'anulado'),"
                + " coalesce(sum(-monto) FILTER (WHERE estado <> 'anulado'), 0),"
                + " count(*) FILTER (WHERE estado = 'anulado'), count(*) FILTER (WHERE estado = 'reembolsado')"
                + " FROM app_movimientos WHERE tipo = 'pago' AND" + delDia + "GROUP BY 1 ORDER BY 3 DESC", d,
                "rubro", "pagos", "monto", "anulados", "reembolsados");
            List<Object> medios = filas(c, "SELECT coalesce(medio, '-'), count(*), coalesce(sum(-monto), 0) FROM app_movimientos"
                + " WHERE tipo = 'pago' AND estado <> 'anulado' AND" + delDia + "GROUP BY 1 ORDER BY 3 DESC", d, "medio", "pagos", "monto");
            List<Object> canales = filas(c, "SELECT coalesce(canal, '-'), count(*), coalesce(sum(-monto), 0) FROM app_movimientos"
                + " WHERE tipo = 'pago' AND estado <> 'anulado' AND" + delDia + "GROUP BY 1 ORDER BY 2 DESC", d, "canal", "pagos", "monto");
            List<Object> pendientes = new ArrayList<>();
            try (PreparedStatement q = c.prepareStatement("SELECT r.codigo, r.pago_codigo, r.monto, r.motivo, r.estado, r.creado, r.resuelto,"
                    + " m.contraparte, m.rubro, u.nombre FROM app_reembolsos r LEFT JOIN app_movimientos m ON m.codigo = r.pago_codigo"
                    + " JOIN app_cuentas k ON k.id = r.cuenta_id JOIN app_usuarios u ON u.id = k.usuario_id"
                    + " WHERE r.estado = 'solicitado' ORDER BY r.creado LIMIT 100")) {
                try (ResultSet r = q.executeQuery()) {
                    while (r.next()) {
                        Map<String, Object> x = reembolso(r);
                        x.put("cliente", r.getString(10));
                        pendientes.add(x);
                    }
                }
            }
            BigDecimal total = BigDecimal.ZERO;
            long n = 0;
            for (Object o : rubros) {
                @SuppressWarnings("unchecked")
                Map<String, Object> x = (Map<String, Object>) o;
                n += ((Number) x.get("pagos")).longValue();
                total = total.add((BigDecimal) x.get("monto"));
            }
            return m("fecha", dia.toString(), "pagos", n, "monto", total, "por_rubro", rubros, "por_medio", medios, "por_canal", canales,
                "reembolsos_pendientes", pendientes);
        }
    }

    private Object liquidacion(Usuario u, String comercio, LocalDate fecha) throws Exception {
        soloSupervisor(u);
        if (!Reglas.comercioValido(comercio)) {
            throw new Falla(400, "Comercio no válido");
        }
        LocalDate dia = fecha == null ? LocalDate.now(ZONA) : fecha;
        try (Connection c = Bd.conexion(); PreparedStatement q = c.prepareStatement("SELECT count(*), coalesce(sum(-monto), 0),"
                + " coalesce(sum(-monto) FILTER (WHERE estado = 'aprobado'), 0) FROM app_movimientos"
                + " WHERE tipo = 'pago' AND comercio = ? AND (creado AT TIME ZONE 'America/Santiago')::date = ?")) {
            q.setString(1, comercio);
            q.setDate(2, java.sql.Date.valueOf(dia));
            try (ResultSet r = q.executeQuery()) {
                r.next();
                return m("comercio", comercio, "fecha", dia.toString(), "pagos", r.getLong(1), "bruto", r.getBigDecimal(2),
                    "a_liquidar", r.getBigDecimal(3));
            }
        }
    }

    // ───────── operaciones ─────────
    private Object pagar(Usuario u, JsonObject b, boolean qr) throws Exception {
        BigDecimal monto = Reglas.monto(str(b, "monto"));
        String medio = qr ? "qr" : Reglas.medio(str(b, "medio"));
        String comercio = str(b, "comercio");
        if (!Reglas.comercioValido(comercio)) {
            throw new Falla(400, "Elige un comercio válido");
        }
        String canal = Reglas.canal(str(b, "canal"));
        String region = Reglas.region(str(b, "region"), u.region());
        String glosa = Reglas.texto(str(b, "glosa"), 80);
        return Bd.tx(c -> {
            String nombre = comercio;
            String rubro = Reglas.rubro(str(b, "rubro"));
            try (PreparedStatement q = c.prepareStatement("SELECT nombre, rubro FROM app_comercios WHERE codigo = ?")) {
                q.setString(1, comercio);
                try (ResultSet r = q.executeQuery()) {
                    if (r.next()) {
                        nombre = r.getString(1);
                        rubro = r.getString(2);
                    }
                }
            }
            BigDecimal saldo = bloquear(c, u.cuentaId());
            if (saldo.compareTo(monto) < 0) {
                throw new Falla(422, "Saldo insuficiente para pagar " + monto.toPlainString() + " USD");
            }
            String codigo = insertar(c, "PAG", u.cuentaId(), "pago", medio, comercio, nombre, rubro == null ? "Otros" : rubro, region, canal,
                monto.negate(), glosa, null);
            BigDecimal nuevo = mover(c, u.cuentaId(), monto.negate());
            Map<String, Object> r = m("ok", true, "codigo", codigo, "estado", "aprobado", "saldo", nuevo);
            r.put("pago", detalleCon(c, u, codigo));
            return r;
        });
    }

    private Object transferir(Usuario u, JsonObject b) throws Exception {
        BigDecimal monto = Reglas.monto(str(b, "monto"));
        String destino = str(b, "destino");
        if (!Reglas.cuentaValida(destino)) {
            throw new Falla(400, "Cuenta de destino no válida (formato CTA-000000)");
        }
        if (destino.equals(u.cuenta())) {
            throw new Falla(422, "No puedes transferir a tu propia cuenta");
        }
        String canal = Reglas.canal(str(b, "canal"));
        String region = Reglas.region(str(b, "region"), u.region());
        String glosa = Reglas.texto(str(b, "glosa"), 80);
        return Bd.tx(c -> {
            int destId;
            String titular;
            try (PreparedStatement q = c.prepareStatement("SELECT k.id, u.nombre FROM app_cuentas k JOIN app_usuarios u ON u.id = k.usuario_id"
                    + " WHERE k.numero = ?")) {
                q.setString(1, destino);
                try (ResultSet r = q.executeQuery()) {
                    if (!r.next()) {
                        throw new Falla(404, "La cuenta " + destino + " no existe");
                    }
                    destId = r.getInt(1);
                    titular = r.getString(2);
                }
            }
            BigDecimal saldo;   // se bloquean las dos cuentas en orden para evitar bloqueos cruzados
            if (u.cuentaId() < destId) {
                saldo = bloquear(c, u.cuentaId());
                bloquear(c, destId);
            } else {
                bloquear(c, destId);
                saldo = bloquear(c, u.cuentaId());
            }
            if (saldo.compareTo(monto) < 0) {
                throw new Falla(422, "Saldo insuficiente para transferir " + monto.toPlainString() + " USD");
            }
            String codigo = insertar(c, "TRF", u.cuentaId(), "transferencia", "transferencia", null, destino + " · " + titular, null, region,
                canal, monto.negate(), glosa, null);
            insertar(c, "TRF", destId, "transferencia", "transferencia", null, u.cuenta() + " · " + u.nombre(), null, region, canal, monto,
                glosa, codigo);
            BigDecimal nuevo = mover(c, u.cuentaId(), monto.negate());
            mover(c, destId, monto);
            return m("ok", true, "codigo", codigo, "destino", destino, "titular", titular, "monto", monto, "saldo", nuevo);
        });
    }

    private Object anular(Usuario u, String codigo, JsonObject b) throws Exception {
        if (!Reglas.codigoValido(codigo)) {
            throw new Falla(400, "Código de pago no válido");
        }
        String canal = Reglas.canal(str(b, "canal"));
        return Bd.tx(c -> {
            BigDecimal monto;
            String region;
            try (PreparedStatement q = c.prepareStatement("SELECT monto, estado, region, (creado AT TIME ZONE 'America/Santiago')::date"
                    + " = (now() AT TIME ZONE 'America/Santiago')::date FROM app_movimientos WHERE codigo = ? AND cuenta_id = ? AND tipo = 'pago'"
                    + " FOR UPDATE")) {
                q.setString(1, codigo);
                q.setInt(2, u.cuentaId());
                try (ResultSet r = q.executeQuery()) {
                    if (!r.next()) {
                        throw new Falla(404, "No existe el pago " + codigo + " en tu cuenta");
                    }
                    if (!"aprobado".equals(r.getString(2))) {
                        throw new Falla(409, "El pago está " + r.getString(2) + ": no se puede anular");
                    }
                    if (!r.getBoolean(4)) {
                        throw new Falla(422, "Solo se anulan pagos del día; para pagos anteriores pide un reembolso");
                    }
                    monto = r.getBigDecimal(1).negate();
                    region = r.getString(3);
                }
            }
            bloquear(c, u.cuentaId());
            estado(c, codigo, "anulado");
            insertar(c, "ANU", u.cuentaId(), "anulacion", null, null, "Anulación de " + codigo, null, region, canal, monto,
                Reglas.texto(str(b, "motivo"), 80), codigo);
            BigDecimal nuevo = mover(c, u.cuentaId(), monto);
            return m("ok", true, "codigo", codigo, "estado", "anulado", "saldo", nuevo);
        });
    }

    private Object pedirReembolso(Usuario u, JsonObject b) throws Exception {
        String pago = str(b, "id");
        if (pago == null) {
            pago = str(b, "pago");
        }
        if (!Reglas.codigoValido(pago)) {
            throw new Falla(400, "Indica el código del pago (PAG-00000000)");
        }
        String motivo = Reglas.texto(str(b, "motivo"), 120);
        if (motivo == null) {
            throw new Falla(400, "Indica el motivo del reembolso");
        }
        String canal = Reglas.canal(str(b, "canal"));
        String codigoPago = pago;
        String montoTxt = str(b, "monto");
        return Bd.tx(c -> {
            BigDecimal pagado;
            try (PreparedStatement q = c.prepareStatement("SELECT monto, estado FROM app_movimientos WHERE codigo = ? AND cuenta_id = ?"
                    + " AND tipo = 'pago' FOR UPDATE")) {
                q.setString(1, codigoPago);
                q.setInt(2, u.cuentaId());
                try (ResultSet r = q.executeQuery()) {
                    if (!r.next()) {
                        throw new Falla(404, "No existe el pago " + codigoPago + " en tu cuenta");
                    }
                    if (!"aprobado".equals(r.getString(2))) {
                        throw new Falla(409, "El pago está " + r.getString(2) + ": no admite reembolso");
                    }
                    pagado = r.getBigDecimal(1).negate();
                }
            }
            BigDecimal monto = montoTxt == null || montoTxt.isBlank() ? pagado : Reglas.monto(montoTxt);
            if (monto.compareTo(pagado) > 0) {
                throw new Falla(422, "El reembolso no puede superar el monto pagado (" + pagado.toPlainString() + " USD)");
            }
            try (PreparedStatement q = c.prepareStatement("SELECT 1 FROM app_reembolsos WHERE pago_codigo = ? AND estado <> 'rechazado'")) {
                q.setString(1, codigoPago);
                try (ResultSet r = q.executeQuery()) {
                    if (r.next()) {
                        throw new Falla(409, "Ese pago ya tiene un reembolso en curso o aprobado");
                    }
                }
            }
            long id;
            try (PreparedStatement q = c.prepareStatement("SELECT nextval(pg_get_serial_sequence('app_reembolsos', 'id'))");
                 ResultSet r = q.executeQuery()) {
                r.next();
                id = r.getLong(1);
            }
            String codigo = Reglas.codigo("REE", id);
            try (PreparedStatement q = c.prepareStatement("INSERT INTO app_reembolsos (id, codigo, pago_codigo, cuenta_id, monto, motivo, canal)"
                    + " VALUES (?, ?, ?, ?, ?, ?, ?)")) {
                q.setLong(1, id);
                q.setString(2, codigo);
                q.setString(3, codigoPago);
                q.setInt(4, u.cuentaId());
                q.setBigDecimal(5, monto);
                q.setString(6, motivo);
                q.setString(7, canal);
                q.executeUpdate();
            }
            return m("ok", true, "codigo", codigo, "pago", codigoPago, "monto", monto, "estado", "solicitado");
        });
    }

    private Object resolverReembolso(Usuario u, String codigo, boolean aprobar) throws Exception {
        soloSupervisor(u);
        if (!Reglas.codigoValido(codigo)) {
            throw new Falla(400, "Código de reembolso no válido");
        }
        return Bd.tx(c -> {
            String pago;
            int cuenta;
            BigDecimal monto;
            try (PreparedStatement q = c.prepareStatement("SELECT pago_codigo, cuenta_id, monto, estado FROM app_reembolsos WHERE codigo = ?"
                    + " FOR UPDATE")) {
                q.setString(1, codigo);
                try (ResultSet r = q.executeQuery()) {
                    if (!r.next()) {
                        throw new Falla(404, "No existe el reembolso " + codigo);
                    }
                    if (!"solicitado".equals(r.getString(4))) {
                        throw new Falla(409, "El reembolso ya está " + r.getString(4));
                    }
                    pago = r.getString(1);
                    cuenta = r.getInt(2);
                    monto = r.getBigDecimal(3);
                }
            }
            try (PreparedStatement q = c.prepareStatement("UPDATE app_reembolsos SET estado = ?, resuelto = now() WHERE codigo = ?")) {
                q.setString(1, aprobar ? "aprobado" : "rechazado");
                q.setString(2, codigo);
                q.executeUpdate();
            }
            if (aprobar) {
                String rubro = null;
                String region = null;
                try (PreparedStatement q = c.prepareStatement("SELECT rubro, region FROM app_movimientos WHERE codigo = ?")) {
                    q.setString(1, pago);
                    try (ResultSet r = q.executeQuery()) {
                        if (r.next()) {
                            rubro = r.getString(1);
                            region = r.getString(2);
                        }
                    }
                }
                bloquear(c, cuenta);
                estado(c, pago, "reembolsado");
                insertar(c, "REE", cuenta, "reembolso", null, null, "Reembolso de " + pago, rubro, region, "web", monto, codigo, pago);
                mover(c, cuenta, monto);
            }
            return m("ok", true, "codigo", codigo, "estado", aprobar ? "aprobado" : "rechazado");
        });
    }

    // ───────── apoyo ─────────
    private static void soloSupervisor(Usuario u) {
        if (!u.supervisor()) {
            throw new Falla(403, "Solo un supervisor puede ver esta sección");
        }
    }

    private static BigDecimal bloquear(Connection c, int cuenta) throws Exception {
        try (PreparedStatement q = c.prepareStatement("SELECT saldo FROM app_cuentas WHERE id = ? FOR UPDATE")) {
            q.setInt(1, cuenta);
            try (ResultSet r = q.executeQuery()) {
                r.next();
                return r.getBigDecimal(1);
            }
        }
    }

    private static BigDecimal mover(Connection c, int cuenta, BigDecimal delta) throws Exception {
        try (PreparedStatement q = c.prepareStatement("UPDATE app_cuentas SET saldo = saldo + ? WHERE id = ? RETURNING saldo")) {
            q.setBigDecimal(1, delta);
            q.setInt(2, cuenta);
            try (ResultSet r = q.executeQuery()) {
                r.next();
                return r.getBigDecimal(1);
            }
        }
    }

    private static void estado(Connection c, String codigo, String estado) throws Exception {
        try (PreparedStatement q = c.prepareStatement("UPDATE app_movimientos SET estado = ? WHERE codigo = ?")) {
            q.setString(1, estado);
            q.setString(2, codigo);
            q.executeUpdate();
        }
    }

    private static String insertar(Connection c, String pref, int cuenta, String tipo, String medio, String comercio, String contraparte,
                                   String rubro, String region, String canal, BigDecimal monto, String glosa, String ref) throws Exception {
        long id = Bd.siguienteId(c);
        String codigo = Reglas.codigo(pref, id);
        try (PreparedStatement q = c.prepareStatement("INSERT INTO app_movimientos (id, codigo, cuenta_id, tipo, medio, comercio, contraparte,"
                + " rubro, region, canal, monto, estado, glosa, ref_codigo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'aprobado', ?, ?)")) {
            q.setLong(1, id);
            q.setString(2, codigo);
            q.setInt(3, cuenta);
            q.setString(4, tipo);
            q.setString(5, medio);
            q.setString(6, comercio);
            q.setString(7, contraparte);
            q.setString(8, rubro);
            q.setString(9, region);
            q.setString(10, canal);
            q.setBigDecimal(11, monto);
            q.setString(12, glosa);
            q.setString(13, ref);
            q.executeUpdate();
        }
        return codigo;
    }

    private static Object detalleCon(Connection c, Usuario u, String codigo) throws Exception {
        try (PreparedStatement q = c.prepareStatement(MOV + "WHERE m.codigo = ? AND m.cuenta_id = ?")) {
            q.setString(1, codigo);
            q.setInt(2, u.cuentaId());
            try (ResultSet r = q.executeQuery()) {
                return r.next() ? mov(r) : null;
            }
        }
    }

    private static Map<String, Object> mov(ResultSet r) throws Exception {
        return m("codigo", r.getString(1), "tipo", r.getString(2), "fecha", r.getTimestamp(3).toInstant().toString(), "medio", r.getString(4),
            "comercio", r.getString(5), "contraparte", r.getString(6), "rubro", r.getString(7), "region", r.getString(8),
            "canal", r.getString(9), "monto", r.getBigDecimal(10), "estado", r.getString(11), "glosa", r.getString(12),
            "referencia", r.getString(13), "reembolso", r.getString(14));
    }

    private static Map<String, Object> reembolso(ResultSet r) throws Exception {
        java.sql.Timestamp res = r.getTimestamp(7);
        return m("codigo", r.getString(1), "pago", r.getString(2), "monto", r.getBigDecimal(3), "motivo", r.getString(4),
            "estado", r.getString(5), "fecha", r.getTimestamp(6).toInstant().toString(), "resuelto", res == null ? null : res.toInstant().toString(),
            "comercio", r.getString(8), "rubro", r.getString(9));
    }

    private static List<Object> filas(Connection c, String sql, java.sql.Date d, String... cols) throws Exception {
        List<Object> l = new ArrayList<>();
        try (PreparedStatement q = c.prepareStatement(sql)) {
            q.setDate(1, d);
            try (ResultSet r = q.executeQuery()) {
                while (r.next()) {
                    Map<String, Object> x = m();
                    for (int i = 0; i < cols.length; i++) {
                        Object v = r.getObject(i + 1);
                        x.put(cols[i], v instanceof BigDecimal bd ? bd.setScale(2, java.math.RoundingMode.HALF_UP) : v);
                    }
                    l.add(x);
                }
            }
        }
        return l;
    }

    private static JsonObject cuerpo(HttpServletRequest req) throws IOException {
        byte[] datos = req.getInputStream().readNBytes(16_384);
        if (datos.length == 0) {
            return JsonValue.EMPTY_JSON_OBJECT;
        }
        String tipo = req.getContentType();
        if (tipo == null || !tipo.toLowerCase().contains("application/json")) {
            throw new Falla(415, "Las operaciones se envían como JSON");
        }
        try (JsonReader r = jakarta.json.Json.createReader(new StringReader(new String(datos, StandardCharsets.UTF_8)))) {
            JsonValue v = r.readValue();
            if (v.getValueType() != JsonValue.ValueType.OBJECT) {
                throw new Falla(400, "Se esperaba un objeto JSON");
            }
            return v.asJsonObject();
        } catch (jakarta.json.JsonException e) {
            throw new Falla(400, "JSON no válido");
        }
    }

    static String str(JsonObject b, String k) {
        JsonValue v = b.get(k);
        if (v == null) {
            return null;
        }
        return switch (v.getValueType()) {
            case STRING -> ((jakarta.json.JsonString) v).getString();
            case NUMBER -> v.toString();
            default -> null;
        };
    }

    private static void responder(HttpServletResponse resp, int estado, Object cuerpo) throws IOException {
        resp.setStatus(estado);
        resp.setContentType("application/json; charset=UTF-8");
        resp.setHeader("Cache-Control", "no-store");
        resp.getWriter().write(Json.escribir(cuerpo));
    }
}
