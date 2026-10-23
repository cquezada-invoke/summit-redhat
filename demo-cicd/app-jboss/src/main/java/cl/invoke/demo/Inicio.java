package cl.invoke.demo;

import jakarta.servlet.annotation.WebServlet;
import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.net.InetAddress;
import java.sql.Connection;
import java.sql.ResultSet;
import javax.naming.InitialContext;
import javax.sql.DataSource;

/** Página de la app: muestra el mensaje y la versión que dejó el pipeline. */
@WebServlet("")
public class Inicio extends HttpServlet {
    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        resp.setContentType("text/html; charset=UTF-8");
        String servidor = InetAddress.getLocalHost().getHostName();
        String baseDatos;
        try {
            DataSource ds = (DataSource) new InitialContext().lookup("java:jboss/datasources/app-jboss");
            try (Connection c = ds.getConnection();
                 ResultSet r = c.createStatement().executeQuery(
                     "SELECT count(*), coalesce(sum(monto), 0) FROM pedidos")) {
                r.next();
                baseDatos = "conectada · " + r.getInt(1) + " pedidos por $" + String.format("%,.0f", r.getDouble(2));
            }
        } catch (Exception e) {
            baseDatos = "sin conexión";
        }
        resp.getWriter().write("""
            <!doctype html><html lang="es"><head><meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1"><title>App JBoss · Invoke</title>
            <style>body{margin:0;font-family:Arial,sans-serif;background:#f5f7fa;color:#101828}
            header{background:#0b1f3a;color:#fff;padding:16px 24px;font-size:18px}
            main{max-width:720px;margin:40px auto;padding:0 16px}
            .card{background:#fff;border:1px solid #e4e7ec;border-radius:12px;padding:28px;border-top:8px solid #cc0000}
            h1{margin:0 0 14px;font-size:32px}.v{display:inline-block;background:#101828;color:#fff;border-radius:999px;padding:4px 14px;font-weight:bold}
            p{color:#667085}</style></head><body><header><b>Invoke</b> · CI con Tekton + CD con Ansible</header>
            <main><div class="card"><h1>%s</h1><span class="v">versión %s</span>
            <p>Servidor: %s · Red Hat JBoss EAP 8</p><p>Base de datos: %s</p></div></main></body></html>
            """.formatted(Datos.mensaje(), Datos.version(), servidor, baseDatos));
    }
}
