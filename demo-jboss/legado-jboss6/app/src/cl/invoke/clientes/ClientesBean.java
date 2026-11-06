package cl.invoke.clientes;

import java.util.ArrayList;
import java.util.List;
import javax.annotation.PostConstruct;
import javax.ejb.Singleton;

/** EJB 3.1 (Java EE 6) dentro del WAR: la cartera de clientes en memoria. */
@Singleton
public class ClientesBean {
    private final List<String[]> clientes = new ArrayList<String[]>();

    @PostConstruct
    void iniciar() {
        clientes.add(new String[] {"76.123.456-7", "Ferretería Los Andes", "Santiago"});
        clientes.add(new String[] {"77.987.654-3", "Constructora Sur", "Concepción"});
    }

    public synchronized List<String[]> todos() { return new ArrayList<String[]>(clientes); }

    public synchronized void agregar(String rut, String nombre, String ciudad) { clientes.add(new String[] {rut, nombre, ciudad}); }
}
