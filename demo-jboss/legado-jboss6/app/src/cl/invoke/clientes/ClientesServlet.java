package cl.invoke.clientes;

import java.io.IOException;
import java.io.PrintWriter;
import java.sql.Connection;
import javax.ejb.EJB;
import javax.naming.InitialContext;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import javax.sql.DataSource;

/**
 * Aplicación de clientes «legada» (Java EE 6 / Servlet 3.0 + EJB 3.1 sobre JBoss AS 6.1).
 * GET /clientes/clientes               → lista en HTML
 * GET /clientes/clientes?formato=json  → lista en JSON y estado del datasource (lo que consulta la verificación)
 * POST /clientes/clientes (rut, nombre, ciudad) → agrega un cliente
 * El datasource llega por resource-ref (web.xml) y su JNDI real está en jboss-web.xml: la migración lo ajusta.
 */
@WebServlet("/clientes")
public class ClientesServlet extends HttpServlet {
    private static final long serialVersionUID = 1L;

    @EJB
    private ClientesBean bean;

    private static String esc(String s) {
        return s == null ? "" : s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }

    private String baseDeDatos() {
        try {
            DataSource ds = (DataSource) new InitialContext().lookup("java:comp/env/jdbc/Clientes");
            Connection c = ds.getConnection();
            try { return c.getMetaData().getDatabaseProductName() + " " + c.getMetaData().getDatabaseProductVersion(); } finally { c.close(); }
        } catch (Exception e) {
            return "no disponible (" + e.getClass().getSimpleName() + ")";
        }
    }

    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {
        String servidor = getServletContext().getServerInfo(), bd = baseDeDatos();
        if ("json".equals(req.getParameter("formato"))) {
            resp.setContentType("application/json; charset=UTF-8");
            PrintWriter out = resp.getWriter();
            out.print("{\"servidor\": \"" + esc(servidor) + "\", \"base_de_datos\": \"" + esc(bd) + "\", \"clientes\": [");
            int i = 0;
            for (String[] c : bean.todos()) {
                out.print((i++ > 0 ? ", " : "") + "{\"rut\": \"" + esc(c[0]) + "\", \"nombre\": \"" + esc(c[1]) + "\", \"ciudad\": \"" + esc(c[2]) + "\"}");
            }
            out.print("]}");
            return;
        }
        resp.setContentType("text/html; charset=UTF-8");
        PrintWriter out = resp.getWriter();
        out.print("<!DOCTYPE html><html><head><meta charset='UTF-8'><title>Clientes</title><style>body{font-family:sans-serif;margin:32px;color:#222}"
                + "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px 10px}th{background:#eee}.srv{color:#666;font-size:13px}</style></head><body>"
                + "<h1>Clientes</h1><p class='srv'>Servidor: " + esc(servidor) + " · base de datos: " + esc(bd) + "</p><table><tr><th>RUT</th><th>Nombre</th><th>Ciudad</th></tr>");
        for (String[] c : bean.todos()) {
            out.print("<tr><td>" + esc(c[0]) + "</td><td>" + esc(c[1]) + "</td><td>" + esc(c[2]) + "</td></tr>");
        }
        out.print("</table><h3>Nuevo cliente</h3><form method='post' action='" + req.getContextPath() + "/clientes'>"
                + "<input name='rut' placeholder='RUT' required> <input name='nombre' placeholder='Nombre' required> <input name='ciudad' placeholder='Ciudad' required> "
                + "<button>Agregar</button></form></body></html>");
    }

    @Override
    protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {
        req.setCharacterEncoding("UTF-8");
        String rut = req.getParameter("rut"), nombre = req.getParameter("nombre"), ciudad = req.getParameter("ciudad");
        if (rut != null && nombre != null && ciudad != null && rut.length() <= 20) {
            bean.agregar(rut, nombre, ciudad);
        }
        resp.sendRedirect(req.getContextPath() + "/clientes");
    }
}
