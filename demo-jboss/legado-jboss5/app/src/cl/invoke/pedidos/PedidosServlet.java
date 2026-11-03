package cl.invoke.pedidos;

import java.io.IOException;
import java.io.PrintWriter;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;

import javax.servlet.ServletException;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import javax.servlet.http.HttpSession;

/**
 * Aplicación de pedidos «legada» (Java EE 5 / Servlet 2.5 sobre JBoss AS 5.1): registra pedidos en memoria y los lista.
 * GET /pedidos/pedidos            → lista en HTML
 * GET /pedidos/pedidos?formato=json → lista en JSON (lo que consulta la verificación)
 * POST /pedidos/pedidos (cliente, producto, cantidad) → registra un pedido
 */
public class PedidosServlet extends HttpServlet {
    private static final long serialVersionUID = 1L;
    private static final List<String[]> PEDIDOS = new ArrayList<String[]>();

    @Override
    public void init() throws ServletException {
        synchronized (PEDIDOS) {
            if (PEDIDOS.isEmpty()) {
                PEDIDOS.add(new String[] {"1", "Ferretería Los Andes", "Tornillos 3/8", "500", ahora()});
                PEDIDOS.add(new String[] {"2", "Constructora Sur", "Cemento 25 kg", "40", ahora()});
            }
        }
    }

    private static String ahora() {
        return new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new Date());
    }

    private static String esc(String s) {
        return s == null ? "" : s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }

    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {
        HttpSession ses = req.getSession(true);
        Integer visitas = (Integer) ses.getAttribute("visitas");
        ses.setAttribute("visitas", visitas == null ? 1 : visitas + 1);
        String servidor = getServletContext().getServerInfo();
        if ("json".equals(req.getParameter("formato"))) {
            resp.setContentType("application/json; charset=UTF-8");
            PrintWriter out = resp.getWriter();
            out.print("{\"servidor\": \"" + esc(servidor) + "\", \"pedidos\": [");
            synchronized (PEDIDOS) {
                for (int i = 0; i < PEDIDOS.size(); i++) {
                    String[] p = PEDIDOS.get(i);
                    out.print((i > 0 ? ", " : "") + "{\"id\": " + p[0] + ", \"cliente\": \"" + esc(p[1]) + "\", \"producto\": \"" + esc(p[2])
                            + "\", \"cantidad\": " + p[3] + ", \"fecha\": \"" + p[4] + "\"}");
                }
            }
            out.print("]}");
            return;
        }
        req.setAttribute("servidor", servidor);
        req.setAttribute("pedidos", new ArrayList<String[]>(PEDIDOS));
        req.getRequestDispatcher("/lista.jsp").forward(req, resp);
    }

    @Override
    protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {
        req.setCharacterEncoding("UTF-8");
        String cliente = req.getParameter("cliente"), producto = req.getParameter("producto"), cantidad = req.getParameter("cantidad");
        if (cliente != null && producto != null && cantidad != null && cantidad.matches("[0-9]{1,6}")) {
            synchronized (PEDIDOS) {
                PEDIDOS.add(new String[] {String.valueOf(PEDIDOS.size() + 1), cliente, producto, cantidad, ahora()});
            }
        }
        resp.sendRedirect(req.getContextPath() + "/pedidos");
    }
}
