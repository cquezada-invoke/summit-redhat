package cl.invoke.facturas;

import javax.ws.rs.ApplicationPath;
import javax.ws.rs.core.Application;

/** JAX-RS (Java EE): la API queda en /facturas/api. */
@ApplicationPath("/api")
public class Aplicacion extends Application {
}
