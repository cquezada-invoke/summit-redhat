package cl.condor.polizas;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Collections;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Pólizas Cóndor (aplicación de demostración, aseguradora ficticia). Datos en memoria con una semilla fija: cada
 * reinicio deja exactamente el mismo estado, para que el plan de pruebas dé el mismo resultado en JBoss 6 y en OpenShift.
 * Compatible con Java 6 (JBoss AS 6.1): sin diamantes, sin lambdas, sin java.time.
 */
public final class Datos {

    public static final String FECHA_PROCESO = "2026-11-01";   // «hoy» del negocio: fijo para que todo sea reproducible
    public static final BigDecimal UF = new BigDecimal("39750.00");
    public static final BigDecimal IPC = new BigDecimal("0.04");
    public static final BigDecimal RECARGO_SINIESTRO = new BigDecimal("0.10");

    public static class Cliente {
        public int id; public String rut; public String nombre; public String correo; public String ciudad; public int edad; public boolean activo = true;
        public int getId() { return id; } public String getRut() { return rut; } public String getNombre() { return nombre; }
        public String getCorreo() { return correo; } public String getCiudad() { return ciudad; } public int getEdad() { return edad; } public boolean isActivo() { return activo; }
    }

    public static class Producto {
        public String codigo; public String nombre; public BigDecimal tasa; public String descripcion;
        public String getCodigo() { return codigo; } public String getNombre() { return nombre; } public BigDecimal getTasa() { return tasa; } public String getDescripcion() { return descripcion; }
    }

    public static class Corredor {
        public int id; public String nombre; public BigDecimal comision;
        public int getId() { return id; } public String getNombre() { return nombre; } public BigDecimal getComision() { return comision; }
    }

    public static class Poliza {
        public String numero; public int clienteId; public String producto; public int corredorId; public BigDecimal sumaUf; public int deducibleUf;
        public BigDecimal prima; public String inicio; public String fin; public String estado = "VIGENTE"; public String motivoAnulacion = ""; public String renovadaDe = "";
        public String getNumero() { return numero; } public int getClienteId() { return clienteId; } public String getProducto() { return producto; }
        public int getCorredorId() { return corredorId; } public BigDecimal getSumaUf() { return sumaUf; } public int getDeducibleUf() { return deducibleUf; }
        public BigDecimal getPrima() { return prima; } public String getInicio() { return inicio; } public String getFin() { return fin; } public String getEstado() { return estado; }
        public String getMotivoAnulacion() { return motivoAnulacion; } public String getRenovadaDe() { return renovadaDe; }
        public String getCliente() { Cliente c = cliente(clienteId); return c == null ? "" : c.nombre; }
    }

    public static class Siniestro {
        public int id; public String poliza; public String fecha; public String descripcion; public BigDecimal montoUf; public String estado = "DENUNCIADO";
        public String resolucion = ""; public BigDecimal pagoUf = BigDecimal.ZERO;
        public int getId() { return id; } public String getPoliza() { return poliza; } public String getFecha() { return fecha; } public String getDescripcion() { return descripcion; }
        public BigDecimal getMontoUf() { return montoUf; } public String getEstado() { return estado; } public String getResolucion() { return resolucion; } public BigDecimal getPagoUf() { return pagoUf; }
    }

    public static class Cuota {
        public int id; public String poliza; public int numero; public String vence; public BigDecimal monto; public boolean pagada; public String fechaPago = "";
        public int getId() { return id; } public String getPoliza() { return poliza; } public int getNumero() { return numero; } public String getVence() { return vence; }
        public BigDecimal getMonto() { return monto; } public boolean isPagada() { return pagada; } public String getFechaPago() { return fechaPago; }
    }

    public static class Usuario {
        public String usuario; public String clave; public String nombre; public String rol;
        public String getUsuario() { return usuario; } public String getNombre() { return nombre; } public String getRol() { return rol; }
    }

    public static class Evento {
        public int id; public String usuario; public String accion; public String objeto;
        public int getId() { return id; } public String getUsuario() { return usuario; } public String getAccion() { return accion; } public String getObjeto() { return objeto; }
    }

    public static final List<Cliente> CLIENTES = new ArrayList<Cliente>();
    public static final List<Producto> PRODUCTOS = new ArrayList<Producto>();
    public static final List<Corredor> CORREDORES = new ArrayList<Corredor>();
    public static final List<Poliza> POLIZAS = new ArrayList<Poliza>();
    public static final List<Siniestro> SINIESTROS = new ArrayList<Siniestro>();
    public static final List<Cuota> CUOTAS = new ArrayList<Cuota>();
    public static final List<Usuario> USUARIOS = new ArrayList<Usuario>();
    public static final List<Evento> AUDITORIA = new ArrayList<Evento>();
    public static final Map<String, String> PARAMETROS = new LinkedHashMap<String, String>();

    static { reiniciar(); }

    private static final String[] NOMBRES = {"Ana", "Bruno", "Carla", "Diego", "Elena", "Felipe", "Gabriela", "Hugo", "Isabel", "Javier",
        "Karen", "Lucas", "María", "Nicolás", "Olivia", "Pablo", "Quintina", "Rodrigo", "Sofía", "Tomás"};
    private static final String[] APELLIDOS = {"Araya", "Bravo", "Castro", "Díaz", "Espinoza", "Fuentes", "González", "Herrera", "Ibáñez", "Jara",
        "Muñoz", "Núñez", "Ortiz", "Pérez", "Rojas"};
    private static final String[] CIUDADES = {"Santiago", "Valparaíso", "Concepción", "La Serena", "Temuco", "Antofagasta", "Puerto Montt"};

    public static synchronized void reiniciar() {
        CLIENTES.clear(); PRODUCTOS.clear(); CORREDORES.clear(); POLIZAS.clear(); SINIESTROS.clear(); CUOTAS.clear(); USUARIOS.clear(); AUDITORIA.clear(); PARAMETROS.clear();
        PARAMETROS.put("fecha_proceso", FECHA_PROCESO); PARAMETROS.put("valor_uf", UF.toPlainString()); PARAMETROS.put("ipc_renovacion", "4 %");
        PARAMETROS.put("recargo_siniestro", "10 %"); PARAMETROS.put("cuotas_por_poliza", "12"); PARAMETROS.put("tamano_pagina", "10");
        usuario("admin", "Condor.2026", "Administración", "ADMIN"); usuario("agente", "Agente.2026", "Agente comercial", "AGENTE"); usuario("auditor", "Auditor.2026", "Auditoría interna", "AUDITOR");
        producto("AUTO", "Seguro automotriz", "0.0350", "Daños propios, robo y responsabilidad civil");
        producto("HOGAR", "Seguro de hogar", "0.0080", "Incendio, sismo y robo de contenidos");
        producto("VIDA", "Seguro de vida", "0.0120", "Fallecimiento e invalidez total");
        producto("SALUD", "Seguro complementario de salud", "0.0450", "Reembolso de gastos médicos");
        producto("VIAJE", "Seguro de viaje", "0.0200", "Asistencia y gastos médicos en el extranjero");
        String[] corr = {"Corredora Pehuén", "Austral Asesores", "Corredores del Pacífico", "Andes Protección", "Litoral Seguros", "Patagonia Riesgos"};
        for (int i = 0; i < corr.length; i++) { Corredor c = new Corredor(); c.id = i + 1; c.nombre = corr[i]; c.comision = new BigDecimal("0.0" + (5 + i % 4)); CORREDORES.add(c); }
        for (int i = 0; i < 30; i++) {
            Cliente c = new Cliente(); c.id = i + 1;
            int base = 10000000 + i * 734521;
            c.rut = base + "-" + dv(base);
            c.nombre = NOMBRES[i % NOMBRES.length] + " " + APELLIDOS[(i * 7) % APELLIDOS.length];
            c.correo = sinTildes(NOMBRES[i % NOMBRES.length]).toLowerCase() + "." + sinTildes(APELLIDOS[(i * 7) % APELLIDOS.length]).toLowerCase() + i + "@correo.cl";
            c.ciudad = CIUDADES[i % CIUDADES.length]; c.edad = 22 + (i * 3) % 50; CLIENTES.add(c);
        }
        for (int i = 0; i < 60; i++) {
            Poliza p = new Poliza(); p.numero = String.format("POL-%05d", 1001 + i); p.clienteId = 1 + (i * 11) % 30;
            p.producto = PRODUCTOS.get(i % PRODUCTOS.size()).codigo; p.corredorId = 1 + i % CORREDORES.size();
            p.sumaUf = new BigDecimal(200 + (i * 37) % 1800); p.deducibleUf = new int[] {0, 3, 5}[i % 3];
            p.prima = prima(p.producto, p.sumaUf, cliente(p.clienteId).edad, p.deducibleUf);
            p.inicio = sumarMeses("2026-01-01", i % 12); p.fin = sumarMeses(p.inicio, 12);
            if (i % 17 == 16) { p.estado = "ANULADA"; p.motivoAnulacion = "Solicitud del cliente"; }
            POLIZAS.add(p); generarCuotas(p);
        }
        String[] desc = {"Choque trasero en semáforo", "Robo de equipo electrónico", "Filtración de agua en baño", "Hospitalización por apendicitis",
            "Pérdida de equipaje", "Rotura de parabrisas", "Incendio en cocina", "Atención de urgencia"};
        for (int i = 0; i < 20; i++) {
            Siniestro s = new Siniestro(); s.id = i + 1; Poliza p = POLIZAS.get((i * 3) % 60);
            s.poliza = p.numero; s.fecha = sumarDias(p.inicio, 20 + i * 5); s.descripcion = desc[i % desc.length];
            s.montoUf = new BigDecimal(5 + (i * 13) % 120);
            if (i % 4 == 0) { s.estado = "APROBADO"; s.pagoUf = s.montoUf.subtract(new BigDecimal(p.deducibleUf)).max(BigDecimal.ZERO); s.resolucion = "Cubierto por la póliza"; }
            else if (i % 4 == 1) { s.estado = "RECHAZADO"; s.resolucion = "Fuera de cobertura"; }
            else if (i % 4 == 2) { s.estado = "EN_EVALUACION"; }
            SINIESTROS.add(s);
        }
        for (Cuota q : CUOTAS) { if (q.vence.compareTo("2026-09-01") < 0 && q.id % 5 != 0) { q.pagada = true; q.fechaPago = q.vence; } }
    }

    // ── reglas de negocio ──────────────────────────────────────────────────────────────────────────────────────────
    public static BigDecimal factorEdad(int edad) { return edad < 25 ? new BigDecimal("1.30") : edad > 60 ? new BigDecimal("1.25") : BigDecimal.ONE; }

    public static BigDecimal factorDeducible(int d) { return d >= 5 ? new BigDecimal("0.85") : d >= 3 ? new BigDecimal("0.90") : BigDecimal.ONE; }

    /** Prima anual en UF: suma asegurada × tasa del producto × factor de edad × factor de deducible, a 2 decimales. */
    public static BigDecimal prima(String producto, BigDecimal sumaUf, int edad, int deducibleUf) {
        Producto pr = producto(producto);
        if (pr == null) throw new IllegalArgumentException("Producto inexistente: " + producto);
        if (sumaUf == null || sumaUf.signum() <= 0) throw new IllegalArgumentException("La suma asegurada debe ser mayor que cero");
        if (edad < 18 || edad > 85) throw new IllegalArgumentException("La edad debe estar entre 18 y 85 años");
        return sumaUf.multiply(pr.tasa).multiply(factorEdad(edad)).multiply(factorDeducible(deducibleUf)).setScale(2, RoundingMode.HALF_UP);
    }

    public static Poliza renovar(String numero) {
        Poliza p = poliza(numero);
        if (p == null) throw new IllegalArgumentException("Póliza inexistente");
        if (!"VIGENTE".equals(p.estado)) throw new IllegalStateException("Solo se renueva una póliza vigente");
        boolean conSiniestro = false;
        for (Siniestro s : SINIESTROS) if (s.poliza.equals(numero) && "APROBADO".equals(s.estado)) conSiniestro = true;
        BigDecimal factor = BigDecimal.ONE.add(IPC).add(conSiniestro ? RECARGO_SINIESTRO : BigDecimal.ZERO);
        Poliza n = new Poliza(); n.numero = String.format("POL-%05d", 1001 + POLIZAS.size()); n.clienteId = p.clienteId; n.producto = p.producto;
        n.corredorId = p.corredorId; n.sumaUf = p.sumaUf; n.deducibleUf = p.deducibleUf; n.prima = p.prima.multiply(factor).setScale(2, RoundingMode.HALF_UP);
        n.inicio = p.fin; n.fin = sumarMeses(p.fin, 12); n.renovadaDe = p.numero; p.estado = "RENOVADA";
        POLIZAS.add(n); generarCuotas(n); return n;
    }

    public static void anular(String numero, String motivo) {
        Poliza p = poliza(numero);
        if (p == null) throw new IllegalArgumentException("Póliza inexistente");
        if (motivo == null || motivo.trim().length() < 5) throw new IllegalArgumentException("Indica el motivo de la anulación (al menos 5 caracteres)");
        if (!"VIGENTE".equals(p.estado)) throw new IllegalStateException("Solo se anula una póliza vigente");
        for (Siniestro s : SINIESTROS) if (s.poliza.equals(numero) && ("EN_EVALUACION".equals(s.estado) || "DENUNCIADO".equals(s.estado)))
            throw new IllegalStateException("No se puede anular: tiene siniestros pendientes");
        p.estado = "ANULADA"; p.motivoAnulacion = motivo.trim();
    }

    public static Siniestro denunciar(String numero, String fecha, String descripcion, BigDecimal montoUf) {
        Poliza p = poliza(numero);
        if (p == null) throw new IllegalArgumentException("Póliza inexistente");
        if (!"VIGENTE".equals(p.estado)) throw new IllegalStateException("La póliza no está vigente");
        if (fecha == null || fecha.compareTo(p.inicio) < 0 || fecha.compareTo(p.fin) >= 0) throw new IllegalArgumentException("La fecha del siniestro está fuera de la vigencia");
        if (montoUf == null || montoUf.signum() <= 0) throw new IllegalArgumentException("El monto debe ser mayor que cero");
        if (descripcion == null || descripcion.trim().length() < 10) throw new IllegalArgumentException("Describe el siniestro (al menos 10 caracteres)");
        Siniestro s = new Siniestro(); s.id = SINIESTROS.size() + 1; s.poliza = numero; s.fecha = fecha; s.descripcion = descripcion.trim(); s.montoUf = montoUf;
        SINIESTROS.add(s); return s;
    }

    public static void resolver(int id, boolean aprobar, String motivo) {
        Siniestro s = siniestro(id);
        if (s == null) throw new IllegalArgumentException("Siniestro inexistente");
        if ("APROBADO".equals(s.estado) || "RECHAZADO".equals(s.estado)) throw new IllegalStateException("El siniestro ya está resuelto");
        Poliza p = poliza(s.poliza);
        if (aprobar) {
            if (s.montoUf.compareTo(p.sumaUf) > 0) throw new IllegalStateException("El monto supera la suma asegurada (" + p.sumaUf + " UF)");
            s.estado = "APROBADO"; s.pagoUf = s.montoUf.subtract(new BigDecimal(p.deducibleUf)).max(BigDecimal.ZERO); s.resolucion = "Cubierto por la póliza";
        } else {
            if (motivo == null || motivo.trim().length() < 5) throw new IllegalArgumentException("Indica el motivo del rechazo");
            s.estado = "RECHAZADO"; s.resolucion = motivo.trim();
        }
    }

    public static void evaluar(int id) {
        Siniestro s = siniestro(id);
        if (s == null) throw new IllegalArgumentException("Siniestro inexistente");
        if (!"DENUNCIADO".equals(s.estado)) throw new IllegalStateException("Solo se pasa a evaluación un siniestro denunciado");
        s.estado = "EN_EVALUACION";
    }

    public static Cuota pagar(int cuotaId) {
        Cuota q = cuota(cuotaId);
        if (q == null) throw new IllegalArgumentException("Cuota inexistente");
        if (q.pagada) throw new IllegalStateException("La cuota ya está pagada");
        q.pagada = true; q.fechaPago = FECHA_PROCESO; return q;
    }

    public static boolean rutValido(String rut) {
        if (rut == null) return false;
        String r = rut.replace(".", "").trim().toUpperCase();
        int g = r.indexOf('-');
        if (g < 2) return false;
        try { return dv(Integer.parseInt(r.substring(0, g))).equals(r.substring(g + 1)); } catch (NumberFormatException e) { return false; }
    }

    public static String dv(int n) {
        int s = 0, m = 2;
        while (n > 0) { s += (n % 10) * m; n /= 10; m = m == 7 ? 2 : m + 1; }
        int r = 11 - s % 11;
        return r == 11 ? "0" : r == 10 ? "K" : String.valueOf(r);
    }

    public static Cliente guardarCliente(Integer id, String rut, String nombre, String correo, String ciudad, String edad) {
        if (!rutValido(rut)) throw new IllegalArgumentException("RUT inválido");
        if (nombre == null || nombre.trim().length() < 3) throw new IllegalArgumentException("El nombre es obligatorio");
        if (correo == null || !correo.matches("[^@\\s]+@[^@\\s]+\\.[a-zA-Z]{2,}")) throw new IllegalArgumentException("Correo inválido");
        int e;
        try { e = Integer.parseInt(edad); } catch (Exception x) { throw new IllegalArgumentException("La edad debe ser un número"); }
        if (e < 18 || e > 100) throw new IllegalArgumentException("La edad debe estar entre 18 y 100");
        for (Cliente c : CLIENTES) if (c.rut.equalsIgnoreCase(rut.trim()) && (id == null || c.id != id)) throw new IllegalArgumentException("Ya existe un cliente con ese RUT");
        Cliente c = id == null ? new Cliente() : cliente(id);
        if (c == null) throw new IllegalArgumentException("Cliente inexistente");
        if (id == null) { c.id = CLIENTES.size() + 1; CLIENTES.add(c); }
        c.rut = rut.trim().toUpperCase(); c.nombre = nombre.trim(); c.correo = correo.trim(); c.ciudad = ciudad == null ? "" : ciudad.trim(); c.edad = e;
        return c;
    }

    public static void eliminarCliente(int id) {
        Cliente c = cliente(id);
        if (c == null) throw new IllegalArgumentException("Cliente inexistente");
        for (Poliza p : POLIZAS) if (p.clienteId == id && "VIGENTE".equals(p.estado)) throw new IllegalStateException("No se puede eliminar: tiene pólizas vigentes");
        c.activo = false;
    }

    public static Poliza emitir(int clienteId, String producto, String suma, String deducible, int corredorId) {
        Cliente c = cliente(clienteId);
        if (c == null || !c.activo) throw new IllegalArgumentException("Cliente inexistente o inactivo");
        BigDecimal s;
        try { s = new BigDecimal(suma); } catch (Exception x) { throw new IllegalArgumentException("La suma asegurada debe ser un número"); }
        int d;
        try { d = Integer.parseInt(deducible); } catch (Exception x) { d = 0; }
        Poliza p = new Poliza(); p.numero = String.format("POL-%05d", 1001 + POLIZAS.size()); p.clienteId = clienteId; p.producto = producto;
        p.corredorId = corredorId; p.sumaUf = s; p.deducibleUf = d; p.prima = prima(producto, s, c.edad, d);
        p.inicio = FECHA_PROCESO; p.fin = sumarMeses(FECHA_PROCESO, 12); POLIZAS.add(p); generarCuotas(p); return p;
    }

    // ── consultas ─────────────────────────────────────────────────────────────────────────────────────────────────
    public static Cliente cliente(int id) { for (Cliente c : CLIENTES) if (c.id == id) return c; return null; }
    public static Producto producto(String cod) { for (Producto p : PRODUCTOS) if (p.codigo.equals(cod)) return p; return null; }
    public static Corredor corredor(int id) { for (Corredor c : CORREDORES) if (c.id == id) return c; return null; }
    public static Poliza poliza(String n) { for (Poliza p : POLIZAS) if (p.numero.equalsIgnoreCase(n == null ? "" : n.trim())) return p; return null; }
    public static Siniestro siniestro(int id) { for (Siniestro s : SINIESTROS) if (s.id == id) return s; return null; }
    public static Cuota cuota(int id) { for (Cuota q : CUOTAS) if (q.id == id) return q; return null; }

    public static List<Poliza> polizasDe(int clienteId) { List<Poliza> o = new ArrayList<Poliza>(); for (Poliza p : POLIZAS) if (p.clienteId == clienteId) o.add(p); return o; }
    public static List<Cuota> cuotasDe(String poliza) { List<Cuota> o = new ArrayList<Cuota>(); for (Cuota q : CUOTAS) if (q.poliza.equals(poliza)) o.add(q); return o; }
    public static List<Siniestro> siniestrosDe(String poliza) { List<Siniestro> o = new ArrayList<Siniestro>(); for (Siniestro s : SINIESTROS) if (s.poliza.equals(poliza)) o.add(s); return o; }

    public static List<Cuota> atrasadas() {
        List<Cuota> o = new ArrayList<Cuota>();
        for (Cuota q : CUOTAS) { Poliza p = poliza(q.poliza); if (!q.pagada && q.vence.compareTo(FECHA_PROCESO) < 0 && p != null && !"ANULADA".equals(p.estado)) o.add(q); }
        return o;
    }

    public static BigDecimal saldoCliente(int clienteId) {
        BigDecimal s = BigDecimal.ZERO;
        for (Poliza p : polizasDe(clienteId)) if (!"ANULADA".equals(p.estado)) for (Cuota q : cuotasDe(p.numero)) if (!q.pagada && q.vence.compareTo(FECHA_PROCESO) < 0) s = s.add(q.monto);
        return s.setScale(2, RoundingMode.HALF_UP);
    }

    public static BigDecimal comisiones(int corredorId) {
        Corredor c = corredor(corredorId); BigDecimal s = BigDecimal.ZERO;
        for (Poliza p : POLIZAS) if (p.corredorId == corredorId) for (Cuota q : cuotasDe(p.numero)) if (q.pagada) s = s.add(q.monto);
        return s.multiply(c.comision).setScale(2, RoundingMode.HALF_UP);
    }

    public static List<Poliza> porVencer(int dias) {
        List<Poliza> o = new ArrayList<Poliza>(); String limite = sumarDias(FECHA_PROCESO, dias);
        for (Poliza p : POLIZAS) if ("VIGENTE".equals(p.estado) && p.fin.compareTo(FECHA_PROCESO) >= 0 && p.fin.compareTo(limite) <= 0) o.add(p);
        return o;
    }

    public static Map<String, Object> resumen() {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        int vig = 0; BigDecimal primas = BigDecimal.ZERO, pagos = BigDecimal.ZERO;
        for (Poliza p : POLIZAS) if ("VIGENTE".equals(p.estado)) { vig++; primas = primas.add(p.prima); }
        for (Siniestro s : SINIESTROS) pagos = pagos.add(s.pagoUf);
        int act = 0; for (Cliente c : CLIENTES) if (c.activo) act++;
        m.put("clientes_activos", act); m.put("polizas_vigentes", vig); m.put("primas_vigentes_uf", primas.setScale(2, RoundingMode.HALF_UP));
        m.put("siniestros", SINIESTROS.size()); m.put("pagos_siniestros_uf", pagos.setScale(2, RoundingMode.HALF_UP));
        m.put("siniestralidad_pct", primas.signum() == 0 ? BigDecimal.ZERO : pagos.multiply(new BigDecimal(100)).divide(primas, 2, RoundingMode.HALF_UP));
        m.put("cuotas_atrasadas", atrasadas().size());
        return m;
    }

    public static Map<String, BigDecimal[]> carteraPorProducto() {
        Map<String, BigDecimal[]> m = new LinkedHashMap<String, BigDecimal[]>();
        for (Producto pr : PRODUCTOS) m.put(pr.codigo, new BigDecimal[] {BigDecimal.ZERO, BigDecimal.ZERO});
        for (Poliza p : POLIZAS) if ("VIGENTE".equals(p.estado)) { BigDecimal[] v = m.get(p.producto); v[0] = v[0].add(BigDecimal.ONE); v[1] = v[1].add(p.prima); }
        return m;
    }

    public static <T> List<T> pagina(List<T> l, int pag, int tam) {
        int d = Math.max(0, (pag - 1) * tam);
        if (d >= l.size()) return Collections.emptyList();
        return l.subList(d, Math.min(l.size(), d + tam));
    }

    public static void auditar(String usuario, String accion, String objeto) {
        Evento e = new Evento(); e.id = AUDITORIA.size() + 1; e.usuario = usuario; e.accion = accion; e.objeto = objeto; AUDITORIA.add(e);
    }

    public static Usuario login(String u, String c) {
        for (Usuario x : USUARIOS) if (x.usuario.equals(u) && x.clave.equals(c)) return x;
        return null;
    }

    // ── auxiliares ────────────────────────────────────────────────────────────────────────────────────────────────
    private static void usuario(String u, String c, String n, String r) { Usuario x = new Usuario(); x.usuario = u; x.clave = c; x.nombre = n; x.rol = r; USUARIOS.add(x); }
    private static void producto(String c, String n, String t, String d) { Producto p = new Producto(); p.codigo = c; p.nombre = n; p.tasa = new BigDecimal(t); p.descripcion = d; PRODUCTOS.add(p); }

    private static void generarCuotas(Poliza p) {
        BigDecimal m = p.prima.divide(new BigDecimal(12), 2, RoundingMode.HALF_UP);
        for (int k = 1; k <= 12; k++) { Cuota q = new Cuota(); q.id = CUOTAS.size() + 1; q.poliza = p.numero; q.numero = k; q.vence = sumarMeses(p.inicio, k - 1); q.monto = m; CUOTAS.add(q); }
    }

    public static String sumarMeses(String f, int n) { return mover(f, Calendar.MONTH, n); }
    public static String sumarDias(String f, int n) { return mover(f, Calendar.DAY_OF_MONTH, n); }

    private static String mover(String f, int campo, int n) {
        try {
            SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd"); Calendar c = Calendar.getInstance(); c.setTime(sdf.parse(f)); c.add(campo, n);
            return sdf.format(c.getTime());
        } catch (Exception e) { throw new IllegalArgumentException("Fecha inválida: " + f); }
    }

    public static boolean fechaValida(String f) {
        if (f == null || !f.matches("\\d{4}-\\d{2}-\\d{2}")) return false;
        try { SimpleDateFormat s = new SimpleDateFormat("yyyy-MM-dd"); s.setLenient(false); Date d = s.parse(f); return d != null; } catch (Exception e) { return false; }
    }

    private static String sinTildes(String s) {
        return s.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n").replace("Á", "A");
    }

    private Datos() { }
}
