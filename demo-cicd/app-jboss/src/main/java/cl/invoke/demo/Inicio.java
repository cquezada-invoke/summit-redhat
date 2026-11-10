package cl.invoke.demo;

import jakarta.servlet.annotation.WebServlet;
import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.net.InetAddress;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;

/**
 * Portada de la App de pagos: entrega la interfaz (HTML + JS) y muestra la versión que dejó el pipeline, el servidor
 * y el estado de la base. El texto «Base de datos: conectada» lo usan las validaciones de FaceOps y de las demos.
 */
@WebServlet("")
public class Inicio extends HttpServlet {
    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        resp.setContentType("text/html; charset=UTF-8");
        resp.setHeader("Cache-Control", "no-store");
        String servidor = InetAddress.getLocalHost().getHostName();
        String baseDatos;
        try (Connection c = Bd.cruda(); Statement s = c.createStatement(); ResultSet r = s.executeQuery("SELECT 1")) {
            r.next();
            baseDatos = "conectada";
            try (Statement s2 = c.createStatement(); ResultSet r2 = s2.executeQuery("SELECT count(*) FROM app_movimientos WHERE tipo = 'pago'"
                    + " AND (creado AT TIME ZONE 'America/Santiago')::date = (now() AT TIME ZONE 'America/Santiago')::date")) {
                r2.next();
                baseDatos += " · " + r2.getLong(1) + " pagos hoy";
            } catch (Exception e) {
                baseDatos += " · estructura de pagos pendiente";
            }
        } catch (Exception e) {
            baseDatos = "sin conexión";
        }
        String base = req.getContextPath() + "/";
        resp.getWriter().write("""
            <!doctype html><html lang="es"><head><meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <title>Pagos · Invoke</title>
            <link rel="stylesheet" href="%1$sstatic/app.css?v=%2$s">
            </head><body data-base="%1$s">
            <header class="barra"><div class="marca"><span class="logo">P</span><div><b>Pagos</b><small>%3$s</small></div></div>
            <div id="quien" class="quien"></div></header>
            <nav id="menu" class="menu" hidden></nav>
            <main id="vista" class="vista"><p class="cargando">Cargando…</p></main>
            <footer class="pie">versión %2$s · Servidor: %4$s · Red Hat JBoss EAP 8 · Base de datos: %5$s</footer>
            <div id="aviso" class="aviso" role="status" hidden></div>
            <script src="%1$sstatic/app.js?v=%2$s"></script>
            </body></html>
            """.formatted(esc(base), esc(Datos.version()), esc(Datos.mensaje()), esc(servidor), esc(baseDatos)));
    }

    static String esc(String s) {
        return s == null ? "" : s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }
}
