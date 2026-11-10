package cl.invoke.ventas;

import java.io.FileInputStream;
import java.io.IOException;
import java.util.Properties;
import javax.naming.InitialContext;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

// Ventas (aplicación de ejemplo de WebLogic): responde 200 solo si el datasource, la cola JMS y las propiedades están enlazados
public class Salud extends HttpServlet {
    private static String revisar(InitialContext ic, String jndi) {
        try { return ic.lookup(jndi) != null ? "enlazado" : "sin enlazar"; } catch (Exception e) { return "sin enlazar"; }
    }

    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse res) throws IOException {
        String ds = "sin enlazar", cola = "sin enlazar", props = "sin cargar";
        try {
            InitialContext ic = new InitialContext();
            ds = revisar(ic, "java:comp/env/jdbc/VentasDS");
            cola = revisar(ic, "java:comp/env/jms/PedidosQueue");
        } catch (Exception e) { /* sin contexto JNDI */ }
        String ruta = System.getProperty("ventas.config", "/u01/apps/ventas/ventas.properties");
        try (FileInputStream f = new FileInputStream(ruta)) {
            Properties p = new Properties(); p.load(f);
            props = p.getProperty("ventas.moneda") != null ? "cargadas" : "incompletas";
        } catch (IOException e) { /* sin archivo */ }
        boolean ok = "enlazado".equals(ds) && "enlazado".equals(cola) && "cargadas".equals(props);
        res.setStatus(ok ? 200 : 503);
        res.setContentType("application/json");
        res.getWriter().print("{\"estado\":\"" + (ok ? "ok" : "degradado") + "\",\"datasource\":\"" + ds + "\",\"cola\":\"" + cola
                + "\",\"propiedades\":\"" + props + "\"}");
    }
}
