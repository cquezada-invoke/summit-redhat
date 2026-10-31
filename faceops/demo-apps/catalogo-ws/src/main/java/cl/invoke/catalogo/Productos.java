package cl.invoke.catalogo;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import javax.ws.rs.GET;
import javax.ws.rs.Path;
import javax.ws.rs.Produces;
import javax.ws.rs.core.MediaType;

/** Productos del catálogo desde PostgreSQL (crea la tabla y datos de ejemplo la primera vez). */
@Path("/")
public class Productos {
    private static String env(String k, String d) { String v = System.getenv(k); return v == null || v.isEmpty() ? d : v; }

    private Connection conexion() throws Exception {
        Class.forName("org.postgresql.Driver");
        String url = "jdbc:postgresql://" + env("DB_HOST", "bd") + ":5432/" + env("DB_NAME", "catalogo");
        return DriverManager.getConnection(url, env("DB_USER", "catalogo"), env("DB_PASSWORD", ""));
    }

    private static String js(String s) { return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""; }

    @GET @Path("salud") @Produces(MediaType.TEXT_PLAIN)
    public String salud() { return "ok"; }

    @GET @Path("productos") @Produces(MediaType.APPLICATION_JSON + ";charset=utf-8")
    public String productos() {
        String servidor = env("HOSTNAME", "?");
        try (Connection c = conexion(); Statement s = c.createStatement()) {
            s.execute("CREATE TABLE IF NOT EXISTS productos (id serial PRIMARY KEY, nombre text, categoria text, precio integer)");
            try (ResultSet r = s.executeQuery("SELECT count(*) FROM productos")) {
                r.next();
                if (r.getInt(1) == 0) {
                    s.execute("INSERT INTO productos(nombre, categoria, precio) VALUES "
                            + "('Notebook 14 pulgadas','Computación',649990),('Monitor 27 pulgadas','Computación',219990),"
                            + "('Audífonos inalámbricos','Audio',89990),('Teclado mecánico','Accesorios',59990),"
                            + "('Router Wi-Fi 6','Redes',79990),('Disco SSD 1 TB','Almacenamiento',69990)");
                }
            }
            StringBuilder b = new StringBuilder("{\"servidor\":" + js("JBoss EAP · " + servidor) + ",\"base\":\"conectada\",\"productos\":[");
            try (ResultSet r = s.executeQuery("SELECT nombre, categoria, precio FROM productos ORDER BY id")) {
                boolean primero = true;
                while (r.next()) {
                    if (!primero) b.append(',');
                    primero = false;
                    b.append("{\"nombre\":").append(js(r.getString(1))).append(",\"categoria\":").append(js(r.getString(2)))
                     .append(",\"precio\":").append(r.getInt(3)).append('}');
                }
            }
            return b.append("]}").toString();
        } catch (Exception e) {
            return "{\"servidor\":" + js("JBoss EAP · " + servidor) + ",\"base\":" + js("sin conexión: " + e.getMessage()) + ",\"productos\":[]}";
        }
    }
}
