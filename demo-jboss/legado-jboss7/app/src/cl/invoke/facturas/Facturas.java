package cl.invoke.facturas;

import java.util.ArrayList;
import java.util.List;
import javax.annotation.PostConstruct;
import javax.enterprise.context.ApplicationScoped;

/** Bean CDI con las facturas en memoria. */
@ApplicationScoped
public class Facturas {
    private final List<String[]> lista = new ArrayList<>();

    @PostConstruct
    void iniciar() {
        lista.add(new String[] {"F-1001", "Ferretería Los Andes", "125000"});
        lista.add(new String[] {"F-1002", "Constructora Sur", "980000"});
    }

    public synchronized List<String[]> todas() { return new ArrayList<>(lista); }

    public synchronized String[] agregar(String cliente, String monto) {
        String[] f = {"F-" + (1001 + lista.size()), cliente, monto};
        lista.add(f);
        return f;
    }
}
