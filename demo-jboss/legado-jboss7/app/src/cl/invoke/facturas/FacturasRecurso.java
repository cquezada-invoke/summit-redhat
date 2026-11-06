package cl.invoke.facturas;

import java.sql.Connection;
import javax.annotation.Resource;
import javax.inject.Inject;
import javax.sql.DataSource;
import javax.ws.rs.FormParam;
import javax.ws.rs.GET;
import javax.ws.rs.POST;
import javax.ws.rs.Path;
import javax.ws.rs.Produces;
import javax.ws.rs.core.MediaType;

/**
 * Aplicación de facturas «legada» (Java EE 8 / JAX-RS + CDI sobre JBoss EAP 7).
 * GET  /facturas/api/facturas            → facturas en JSON y estado del datasource (lo que consulta la verificación)
 * POST /facturas/api/facturas (cliente, monto) → agrega una factura
 */
@Path("/facturas")
public class FacturasRecurso {
    @Inject
    Facturas facturas;

    @Resource(lookup = "java:jboss/datasources/FacturasDS")
    DataSource ds;

    private static String esc(String s) {
        return s == null ? "" : s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private String baseDeDatos() {
        try (Connection c = ds.getConnection()) {
            return c.getMetaData().getDatabaseProductName() + " " + c.getMetaData().getDatabaseProductVersion();
        } catch (Exception e) {
            return "no disponible (" + e.getClass().getSimpleName() + ")";
        }
    }

    @GET
    @Produces(MediaType.APPLICATION_JSON)
    public String listar() {
        StringBuilder b = new StringBuilder("{\"servidor\": \"" + esc(System.getProperty("jboss.product.name", "JBoss")) + " "
                + esc(System.getProperty("jboss.product.version", "")) + "\", \"base_de_datos\": \"" + esc(baseDeDatos()) + "\", \"facturas\": [");
        int i = 0;
        for (String[] f : facturas.todas()) {
            b.append(i++ > 0 ? ", " : "").append("{\"numero\": \"").append(esc(f[0])).append("\", \"cliente\": \"").append(esc(f[1]))
             .append("\", \"monto\": ").append(f[2]).append("}");
        }
        return b.append("]}").toString();
    }

    @POST
    @Produces(MediaType.APPLICATION_JSON)
    public String agregar(@FormParam("cliente") String cliente, @FormParam("monto") String monto) {
        if (cliente == null || monto == null || !monto.matches("[0-9]{1,9}")) {
            return "{\"error\": \"cliente y monto (entero) son obligatorios\"}";
        }
        String[] f = facturas.agregar(cliente, monto);
        return "{\"numero\": \"" + esc(f[0]) + "\"}";
    }
}
