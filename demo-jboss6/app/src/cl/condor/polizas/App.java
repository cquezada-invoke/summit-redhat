package cl.condor.polizas;

import java.io.IOException;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import javax.servlet.ServletException;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import javax.servlet.http.HttpSession;

import cl.condor.polizas.Datos.Cliente;
import cl.condor.polizas.Datos.Corredor;
import cl.condor.polizas.Datos.Cuota;
import cl.condor.polizas.Datos.Evento;
import cl.condor.polizas.Datos.Poliza;
import cl.condor.polizas.Datos.Producto;
import cl.condor.polizas.Datos.Siniestro;
import cl.condor.polizas.Datos.Usuario;

/**
 * Controlador único de Pólizas Cóndor: /app/* (páginas) y /api/* (JSON). Unas 50 páginas, todas con un identificador
 * estable (data-pagina) para el plan de pruebas. Java 6: sin switch de String; el despacho es por prefijo.
 */
public class App extends HttpServlet {
    private static final long serialVersionUID = 1L;
    private static final int TAM = 10;

    @Override
    protected void doGet(HttpServletRequest rq, HttpServletResponse rs) throws ServletException, IOException { atender(rq, rs, false); }

    @Override
    protected void doPost(HttpServletRequest rq, HttpServletResponse rs) throws ServletException, IOException { atender(rq, rs, true); }

    private void atender(HttpServletRequest rq, HttpServletResponse rs, boolean post) throws ServletException, IOException {
        rq.setCharacterEncoding("UTF-8");
        rs.setCharacterEncoding("UTF-8");
        String ruta = rq.getPathInfo() == null ? "/" : rq.getPathInfo();
        if (rq.getServletPath().startsWith("/api")) { Api.atender(rq, rs, ruta); return; }
        HttpSession ses = rq.getSession(true);
        Usuario u = (Usuario) ses.getAttribute("usuario");
        if (ruta.equals("/login")) { login(rq, rs, post); return; }
        if (ruta.equals("/salir")) { ses.invalidate(); rs.sendRedirect(rq.getContextPath() + "/app/login"); return; }
        if (u == null) { rs.sendRedirect(rq.getContextPath() + "/app/login"); return; }
        Pagina p;
        try {
            if (ruta.startsWith("/admin") && !"ADMIN".equals(u.rol) && !(ruta.equals("/admin/auditoria") && "AUDITOR".equals(u.rol))) { p = denegado(rs); }
            else if (post && "AUDITOR".equals(u.rol)) { p = denegado(rs); }
            else p = enrutar(rq, rs, ruta, post, u);
        } catch (IllegalArgumentException e) {
            p = new Pagina("error-validacion", "No se pudo completar", "ficha"); p.error = e.getMessage(); p.enlace("Volver", "javascript:history.back()"); rs.setStatus(400);
        } catch (IllegalStateException e) {
            p = new Pagina("error-regla", "Operación no permitida", "ficha"); p.error = e.getMessage(); p.enlace("Volver", "javascript:history.back()"); rs.setStatus(409);
        }
        if (p == null) return;   // ya redirigió
        rq.setAttribute("p", p);
        rq.setAttribute("u", u);
        String jsp = "especial".equals(p.plantilla) ? p.vista : p.plantilla;
        rq.getRequestDispatcher("/WEB-INF/vistas/" + jsp + ".jsp").forward(rq, rs);
    }

    private Pagina enrutar(HttpServletRequest rq, HttpServletResponse rs, String r, boolean post, Usuario u) throws IOException {
        String ctx = rq.getContextPath() + "/app";
        String[] s = r.split("/");   // "", modulo, accion, id
        String mod = s.length > 1 ? s[1] : "", acc = s.length > 2 ? s[2] : "", id = s.length > 3 ? s[3] : "";
        // ── inicio y generales ──
        if (r.equals("/") || r.equals("/inicio")) return inicio(ctx);
        if (r.equals("/perfil")) { Pagina p = new Pagina("perfil", "Mi perfil", "ficha"); p.campo("Usuario", u.usuario).campo("Nombre", u.nombre).campo("Rol", u.rol); return p; }
        if (r.equals("/ayuda")) { Pagina p = especial("ayuda", "Ayuda"); return p; }
        if (r.equals("/acerca")) { Pagina p = new Pagina("acerca", "Acerca de Pólizas Cóndor", "ficha"); p.campo("Aplicación", "Pólizas Cóndor 1.0").campo("Aseguradora", "Aseguradora Cóndor Austral (ficticia)")
                .campo("Servidor", getServletContext().getServerInfo()).campo("Java", System.getProperty("java.specification.version")); return p; }
        if (r.equals("/mapa")) return especial("mapa", "Mapa del sitio");
        // ── clientes ──
        if (mod.equals("clientes")) {
            if (acc.equals("") || acc.equals("lista")) return listaClientes(rq, ctx, "clientes-lista", "Clientes", false);
            if (acc.equals("buscar")) return listaClientes(rq, ctx, "clientes-buscar", "Buscar clientes", true);
            if (acc.equals("nuevo")) {
                if (post) { Cliente c = Datos.guardarCliente(null, rq.getParameter("rut"), rq.getParameter("nombre"), rq.getParameter("correo"), rq.getParameter("ciudad"), rq.getParameter("edad"));
                    Datos.auditar(u.usuario, "crear cliente", c.rut); return ir(rs, ctx + "/clientes/detalle/" + c.id + "?ok=Cliente+creado"); }
                return formCliente(ctx, null);
            }
            Cliente c = Datos.cliente(num(id));
            if (c == null) return noEncontrado(rs);
            if (acc.equals("detalle")) return detalleCliente(rq, ctx, c);
            if (acc.equals("editar")) {
                if (post) { Datos.guardarCliente(c.id, rq.getParameter("rut"), rq.getParameter("nombre"), rq.getParameter("correo"), rq.getParameter("ciudad"), rq.getParameter("edad"));
                    Datos.auditar(u.usuario, "editar cliente", c.rut); return ir(rs, ctx + "/clientes/detalle/" + c.id + "?ok=Cliente+actualizado"); }
                return formCliente(ctx, c);
            }
            if (acc.equals("eliminar")) {
                if (post) { Datos.eliminarCliente(c.id); Datos.auditar(u.usuario, "eliminar cliente", c.rut); return ir(rs, ctx + "/clientes/lista?ok=Cliente+desactivado"); }
                Pagina p = new Pagina("clientes-eliminar", "Desactivar cliente " + c.nombre, "formulario"); p.accion = ctx + "/clientes/eliminar/" + c.id; p.boton = "Confirmar";
                p.subtitulo = "El cliente queda inactivo. No se puede si tiene pólizas vigentes."; return p;
            }
            if (acc.equals("estado-cuenta")) return estadoCuenta(ctx, c);
        }
        // ── pólizas ──
        if (mod.equals("polizas")) {
            if (acc.equals("") || acc.equals("lista")) return listaPolizas(rq, ctx, "polizas-lista", "Pólizas", false);
            if (acc.equals("buscar")) return listaPolizas(rq, ctx, "polizas-buscar", "Buscar pólizas", true);
            if (acc.equals("nueva")) {
                if (post) { Poliza n = Datos.emitir(num(rq.getParameter("cliente")), rq.getParameter("producto"), rq.getParameter("suma"), rq.getParameter("deducible"), num(rq.getParameter("corredor")));
                    Datos.auditar(u.usuario, "emitir póliza", n.numero); return ir(rs, ctx + "/polizas/detalle/" + n.numero + "?ok=P%C3%B3liza+emitida"); }
                return formPoliza(ctx, rq.getParameter("cliente"));
            }
            Poliza po = Datos.poliza(id);
            if (po == null) return noEncontrado(rs);
            if (acc.equals("detalle")) return detallePoliza(rq, ctx, po);
            if (acc.equals("editar")) {
                if (post) { if (!"VIGENTE".equals(po.estado)) throw new IllegalStateException("Solo se edita una póliza vigente");
                    po.corredorId = num(rq.getParameter("corredor")); Datos.auditar(u.usuario, "editar póliza", po.numero); return ir(rs, ctx + "/polizas/detalle/" + po.numero + "?ok=P%C3%B3liza+actualizada"); }
                Pagina p = new Pagina("polizas-editar", "Editar póliza " + po.numero, "formulario"); p.accion = ctx + "/polizas/editar/" + po.numero;
                p.entrada("corredor", "Corredor", "select", po.corredorId, opcionesCorredores()); return p;
            }
            if (acc.equals("renovar")) {
                if (post) { Poliza n = Datos.renovar(po.numero); Datos.auditar(u.usuario, "renovar póliza", po.numero + " → " + n.numero); return ir(rs, ctx + "/polizas/detalle/" + n.numero + "?ok=P%C3%B3liza+renovada"); }
                Pagina p = new Pagina("polizas-renovar", "Renovar póliza " + po.numero, "formulario"); p.accion = ctx + "/polizas/renovar/" + po.numero; p.boton = "Renovar";
                p.subtitulo = "Nueva vigencia desde " + po.fin + ". Prima actual " + po.prima + " UF + IPC 4 % (+10 % si tuvo siniestros aprobados)."; return p;
            }
            if (acc.equals("anular")) {
                if (post) { Datos.anular(po.numero, rq.getParameter("motivo")); Datos.auditar(u.usuario, "anular póliza", po.numero); return ir(rs, ctx + "/polizas/detalle/" + po.numero + "?ok=P%C3%B3liza+anulada"); }
                Pagina p = new Pagina("polizas-anular", "Anular póliza " + po.numero, "formulario"); p.accion = ctx + "/polizas/anular/" + po.numero; p.boton = "Anular";
                p.entrada("motivo", "Motivo", "text", ""); return p;
            }
            if (acc.equals("cuotas")) return cuotasPoliza(ctx, po);
        }
        // ── siniestros ──
        if (mod.equals("siniestros")) {
            if (acc.equals("") || acc.equals("lista")) return listaSiniestros(rq, ctx, "siniestros-lista", "Siniestros", false);
            if (acc.equals("buscar")) return listaSiniestros(rq, ctx, "siniestros-buscar", "Buscar siniestros", true);
            if (acc.equals("denunciar")) {
                if (post) { BigDecimal m; try { m = new BigDecimal(rq.getParameter("monto")); } catch (Exception e) { throw new IllegalArgumentException("El monto debe ser un número"); }
                    if (!Datos.fechaValida(rq.getParameter("fecha"))) throw new IllegalArgumentException("Fecha inválida (AAAA-MM-DD)");
                    Siniestro n = Datos.denunciar(rq.getParameter("poliza"), rq.getParameter("fecha"), rq.getParameter("descripcion"), m);
                    Datos.auditar(u.usuario, "denunciar siniestro", "#" + n.id); return ir(rs, ctx + "/siniestros/detalle/" + n.id + "?ok=Siniestro+denunciado"); }
                Pagina p = new Pagina("siniestros-denunciar", "Denunciar siniestro", "formulario"); p.accion = ctx + "/siniestros/denunciar";
                p.entrada("poliza", "Póliza", "text", rq.getParameter("poliza")).entrada("fecha", "Fecha (AAAA-MM-DD)", "text", Datos.FECHA_PROCESO)
                 .entrada("descripcion", "Descripción", "textarea", "").entrada("monto", "Monto estimado (UF)", "text", ""); p.boton = "Denunciar"; return p;
            }
            Siniestro si = Datos.siniestro(num(id));
            if (si == null) return noEncontrado(rs);
            if (acc.equals("detalle")) return detalleSiniestro(rq, ctx, si);
            if (acc.equals("evaluar")) {
                if (post) { Datos.evaluar(si.id); Datos.auditar(u.usuario, "evaluar siniestro", "#" + si.id); return ir(rs, ctx + "/siniestros/detalle/" + si.id + "?ok=En+evaluaci%C3%B3n"); }
                Pagina p = new Pagina("siniestros-evaluar", "Pasar a evaluación el siniestro #" + si.id, "formulario"); p.accion = ctx + "/siniestros/evaluar/" + si.id; p.boton = "Pasar a evaluación"; return p;
            }
            if (acc.equals("resolver")) {
                if (post) { Datos.resolver(si.id, "aprobar".equals(rq.getParameter("decision")), rq.getParameter("motivo"));
                    Datos.auditar(u.usuario, "resolver siniestro", "#" + si.id + " " + rq.getParameter("decision")); return ir(rs, ctx + "/siniestros/detalle/" + si.id + "?ok=Siniestro+resuelto"); }
                Pagina p = new Pagina("siniestros-resolver", "Resolver siniestro #" + si.id, "formulario"); p.accion = ctx + "/siniestros/resolver/" + si.id; p.boton = "Resolver";
                p.entrada("decision", "Decisión", "select", "aprobar", "aprobar=Aprobar", "rechazar=Rechazar").entrada("motivo", "Motivo (si rechaza)", "text", ""); return p;
            }
        }
        // ── pagos ──
        if (mod.equals("pagos")) {
            if (acc.equals("") || acc.equals("lista")) return listaCuotas(rq, ctx, "pagos-lista", "Pagos recibidos", true);
            if (acc.equals("pendientes")) return listaCuotas(rq, ctx, "pagos-pendientes", "Cuotas pendientes", false);
            Cuota q = Datos.cuota(num(id));
            if (q == null) return noEncontrado(rs);
            if (acc.equals("detalle")) { Pagina p = new Pagina("pagos-detalle", "Cuota " + q.numero + " de " + q.poliza, "ficha");
                p.campo("Póliza", q.poliza).campo("Número", q.numero).campo("Vence", q.vence).campo("Monto (UF)", q.monto).campo("Estado", q.pagada ? "PAGADA" : "PENDIENTE").campo("Fecha de pago", q.fechaPago);
                if (!q.pagada) p.enlace("Registrar pago", ctx + "/pagos/registrar/" + q.id); p.enlace("Ver póliza", ctx + "/polizas/detalle/" + q.poliza); return p; }
            if (acc.equals("registrar")) {
                if (post) { Datos.pagar(q.id); Datos.auditar(u.usuario, "registrar pago", q.poliza + " cuota " + q.numero); return ir(rs, ctx + "/pagos/detalle/" + q.id + "?ok=Pago+registrado"); }
                Pagina p = new Pagina("pagos-registrar", "Registrar pago de la cuota " + q.numero + " de " + q.poliza, "formulario"); p.accion = ctx + "/pagos/registrar/" + q.id; p.boton = "Registrar pago";
                p.subtitulo = "Monto " + q.monto + " UF · vence " + q.vence; return p;
            }
        }
        // ── productos y cotización ──
        if (mod.equals("productos")) {
            if (acc.equals("") || acc.equals("lista")) { Pagina p = new Pagina("productos-lista", "Productos", "tabla"); p.col("Código", "Nombre", "Tasa anual", "Cobertura");
                for (Producto x : Datos.PRODUCTOS) p.fila(new String[] {x.codigo, ctx + "/productos/detalle/" + x.codigo}, x.nombre, x.tasa.movePointRight(2).stripTrailingZeros().toPlainString() + " %", x.descripcion);
                p.enlace("Cotizar", ctx + "/productos/cotizar"); return p; }
            if (acc.equals("cotizar")) {
                if (post) {
                    BigDecimal suma; int edad; int ded;
                    try { suma = new BigDecimal(rq.getParameter("suma")); edad = Integer.parseInt(rq.getParameter("edad")); ded = Integer.parseInt(rq.getParameter("deducible")); }
                    catch (Exception e) { throw new IllegalArgumentException("Suma, edad y deducible deben ser números"); }
                    BigDecimal pr = Datos.prima(rq.getParameter("producto"), suma, edad, ded);
                    Pagina p = new Pagina("productos-cotizacion", "Resultado de la cotización", "ficha");
                    p.campo("Producto", rq.getParameter("producto")).campo("Suma asegurada (UF)", suma).campo("Edad", edad).campo("Deducible (UF)", ded)
                     .campo("Factor de edad", Datos.factorEdad(edad)).campo("Factor de deducible", Datos.factorDeducible(ded)).campo("Prima anual (UF)", pr)
                     .campo("Cuota mensual (UF)", pr.divide(new BigDecimal(12), 2, java.math.RoundingMode.HALF_UP)).campo("Prima anual ($)", pr.multiply(Datos.UF).setScale(0, java.math.RoundingMode.HALF_UP));
                    p.enlace("Cotizar otra", ctx + "/productos/cotizar"); return p;
                }
                Pagina p = new Pagina("productos-cotizar", "Cotizar un seguro", "formulario"); p.accion = ctx + "/productos/cotizar"; p.boton = "Cotizar";
                p.entrada("producto", "Producto", "select", rq.getParameter("producto") == null ? "AUTO" : rq.getParameter("producto"), opcionesProductos())
                 .entrada("suma", "Suma asegurada (UF)", "text", "500").entrada("edad", "Edad del asegurado", "text", "40").entrada("deducible", "Deducible (UF)", "select", "0", "0", "3", "5"); return p;
            }
            Producto x = Datos.producto(id);
            if (x == null) return noEncontrado(rs);
            if (acc.equals("detalle")) { Pagina p = new Pagina("productos-detalle", x.nombre, "ficha"); int n = 0; for (Poliza po : Datos.POLIZAS) if (po.producto.equals(x.codigo) && "VIGENTE".equals(po.estado)) n++;
                p.campo("Código", x.codigo).campo("Tasa anual", x.tasa.movePointRight(2).stripTrailingZeros().toPlainString() + " %").campo("Cobertura", x.descripcion).campo("Pólizas vigentes", n);
                p.enlace("Cotizar este producto", ctx + "/productos/cotizar?producto=" + x.codigo); return p; }
        }
        // ── corredores ──
        if (mod.equals("corredores")) {
            if (acc.equals("") || acc.equals("lista")) { Pagina p = new Pagina("corredores-lista", "Corredores", "tabla"); p.col("Corredor", "Comisión", "Pólizas");
                for (Corredor c : Datos.CORREDORES) { int n = 0; for (Poliza po : Datos.POLIZAS) if (po.corredorId == c.id) n++; p.fila(new String[] {c.nombre, ctx + "/corredores/detalle/" + c.id}, c.comision.movePointRight(2).stripTrailingZeros().toPlainString() + " %", n); }
                p.enlace("Comisiones", ctx + "/corredores/comisiones"); return p; }
            if (acc.equals("comisiones")) { Pagina p = new Pagina("corredores-comisiones", "Comisiones por corredor", "tabla"); p.col("Corredor", "Comisión", "Comisión ganada (UF)");
                for (Corredor c : Datos.CORREDORES) p.fila(c.nombre, c.comision.movePointRight(2).stripTrailingZeros().toPlainString() + " %", Datos.comisiones(c.id)); return p; }
            Corredor c = Datos.corredor(num(id));
            if (c == null) return noEncontrado(rs);
            if (acc.equals("detalle")) { Pagina p = new Pagina("corredores-detalle", c.nombre, "ficha"); p.campo("Comisión", c.comision.movePointRight(2).stripTrailingZeros().toPlainString() + " %").campo("Comisión ganada (UF)", Datos.comisiones(c.id));
                Pagina t = new Pagina("corredores-detalle-polizas", "Pólizas", "tabla"); t.col("Póliza", "Cliente", "Estado");
                for (Poliza po : Datos.POLIZAS) if (po.corredorId == c.id) t.fila(new String[] {po.numero, ctx + "/polizas/detalle/" + po.numero}, po.getCliente(), po.estado);
                p.secciones.add(t); return p; }
        }
        // ── reportes ──
        if (mod.equals("reportes")) {
            if (acc.equals("") || acc.equals("resumen")) { Pagina p = new Pagina("reportes-resumen", "Resumen de la cartera", "ficha");
                for (Map.Entry<String, Object> e : Datos.resumen().entrySet()) p.campo(e.getKey().replace('_', ' '), e.getValue());
                p.enlace("Siniestralidad", ctx + "/reportes/siniestralidad").enlace("Cartera por producto", ctx + "/reportes/cartera").enlace("Vencimientos", ctx + "/reportes/vencimientos")
                 .enlace("Cuotas atrasadas", ctx + "/reportes/atrasados").enlace("Exportar pólizas (CSV)", rq.getContextPath() + "/api/exportar/polizas.csv"); return p; }
            if (acc.equals("siniestralidad")) { Pagina p = new Pagina("reportes-siniestralidad", "Siniestralidad por producto", "tabla"); p.col("Producto", "Primas vigentes (UF)", "Pagos de siniestros (UF)", "Siniestralidad");
                for (Producto x : Datos.PRODUCTOS) { BigDecimal pr = BigDecimal.ZERO, pa = BigDecimal.ZERO;
                    for (Poliza po : Datos.POLIZAS) if (po.producto.equals(x.codigo)) { if ("VIGENTE".equals(po.estado)) pr = pr.add(po.prima); for (Siniestro si : Datos.siniestrosDe(po.numero)) pa = pa.add(si.pagoUf); }
                    p.fila(x.nombre, pr.setScale(2), pa.setScale(2), (pr.signum() == 0 ? "0" : pa.multiply(new BigDecimal(100)).divide(pr, 1, java.math.RoundingMode.HALF_UP).toPlainString()) + " %"); }
                return p; }
            if (acc.equals("cartera")) { Pagina p = new Pagina("reportes-cartera", "Cartera vigente por producto", "tabla"); p.col("Producto", "Pólizas vigentes", "Primas (UF)");
                for (Map.Entry<String, BigDecimal[]> e : Datos.carteraPorProducto().entrySet()) p.fila(e.getKey(), e.getValue()[0].intValue(), e.getValue()[1].setScale(2)); return p; }
            if (acc.equals("vencimientos")) { int dias = rq.getParameter("dias") == null ? 60 : num(rq.getParameter("dias"));
                Pagina p = new Pagina("reportes-vencimientos", "Pólizas que vencen en " + dias + " días", "tabla"); p.col("Póliza", "Cliente", "Vence", "Prima (UF)");
                for (Poliza po : Datos.porVencer(dias)) p.fila(new String[] {po.numero, ctx + "/polizas/detalle/" + po.numero}, po.getCliente(), po.fin, po.prima);
                p.enlace("30 días", ctx + "/reportes/vencimientos?dias=30").enlace("90 días", ctx + "/reportes/vencimientos?dias=90"); p.total = p.filas.size(); return p; }
            if (acc.equals("atrasados")) { Pagina p = new Pagina("reportes-atrasados", "Cuotas atrasadas al " + Datos.FECHA_PROCESO, "tabla"); p.col("Póliza", "Cuota", "Venció", "Monto (UF)");
                for (Cuota q : Datos.atrasadas()) p.fila(new String[] {q.poliza, ctx + "/polizas/cuotas/" + q.poliza}, q.numero, q.vence, q.monto); p.total = p.filas.size(); return p; }
        }
        // ── administración ──
        if (mod.equals("admin")) {
            if (acc.equals("usuarios")) { Pagina p = new Pagina("admin-usuarios", "Usuarios", "tabla"); p.col("Usuario", "Nombre", "Rol"); for (Usuario x : Datos.USUARIOS) p.fila(x.usuario, x.nombre, x.rol); return p; }
            if (acc.equals("parametros")) { Pagina p = new Pagina("admin-parametros", "Parámetros del negocio", "ficha"); for (Map.Entry<String, String> e : Datos.PARAMETROS.entrySet()) p.campo(e.getKey(), e.getValue()); return p; }
            if (acc.equals("auditoria")) { Pagina p = new Pagina("admin-auditoria", "Auditoría", "tabla"); p.col("#", "Usuario", "Acción", "Objeto");
                List<Evento> l = new ArrayList<Evento>(Datos.AUDITORIA); java.util.Collections.reverse(l); for (Evento e : l) p.fila(e.id, e.usuario, e.accion, e.objeto); p.total = l.size(); return p; }
            if (acc.equals("salud")) { Pagina p = new Pagina("admin-salud", "Salud de la aplicación", "ficha"); Runtime rt = Runtime.getRuntime();
                p.campo("Estado", "OK").campo("Servidor", getServletContext().getServerInfo()).campo("Java", System.getProperty("java.version")).campo("Registros", Datos.POLIZAS.size() + " pólizas · " + Datos.CLIENTES.size() + " clientes")
                 .campo("Memoria usada (MB)", (rt.totalMemory() - rt.freeMemory()) / 1048576); return p; }
            if (acc.equals("reiniciar") && post) { Datos.reiniciar(); Datos.auditar(u.usuario, "reiniciar datos", "semilla"); return ir(rs, ctx + "/inicio?ok=Datos+reiniciados"); }
        }
        return noEncontrado(rs);
    }

    // ── páginas ────────────────────────────────────────────────────────────────────────────────────────────────────
    private Pagina inicio(String ctx) {
        Pagina p = especial("inicio", "Inicio");
        for (Map.Entry<String, Object> e : Datos.resumen().entrySet()) p.campo(e.getKey().replace('_', ' '), e.getValue());
        return p;
    }

    private Pagina listaClientes(HttpServletRequest rq, String ctx, String id, String titulo, boolean buscador) {
        String qq = rq.getParameter("q"); String ciudad = rq.getParameter("ciudad");
        List<Cliente> l = new ArrayList<Cliente>();
        for (Cliente c : Datos.CLIENTES) {
            if (!c.activo && !"1".equals(rq.getParameter("inactivos"))) continue;
            if (qq != null && qq.trim().length() > 0 && !(c.nombre.toLowerCase().contains(qq.toLowerCase().trim()) || c.rut.contains(qq.trim()))) continue;
            if (ciudad != null && ciudad.length() > 0 && !c.ciudad.equals(ciudad)) continue;
            l.add(c);
        }
        Pagina p = new Pagina(id, titulo, "tabla"); p.col("RUT", "Nombre", "Ciudad", "Edad", "Correo");
        paginar(rq, p, l.size(), ctx + "/clientes/" + (buscador ? "buscar" : "lista"));
        for (Cliente c : Datos.pagina(l, p.pagina, TAM)) p.fila(new String[] {c.rut, ctx + "/clientes/detalle/" + c.id}, c.nombre, c.ciudad, c.edad, c.correo);
        if (buscador) p.busqueda = qq == null ? "" : qq;
        p.enlace("Nuevo cliente", ctx + "/clientes/nuevo"); return p;
    }

    private Pagina detalleCliente(HttpServletRequest rq, String ctx, Cliente c) {
        Pagina p = new Pagina("clientes-detalle", c.nombre, "ficha"); p.aviso = s(rq.getParameter("ok"));
        p.campo("RUT", c.rut).campo("Correo", c.correo).campo("Ciudad", c.ciudad).campo("Edad", c.edad).campo("Estado", c.activo ? "ACTIVO" : "INACTIVO").campo("Saldo atrasado (UF)", Datos.saldoCliente(c.id));
        p.enlace("Editar", ctx + "/clientes/editar/" + c.id).enlace("Estado de cuenta", ctx + "/clientes/estado-cuenta/" + c.id).enlace("Nueva póliza", ctx + "/polizas/nueva?cliente=" + c.id).enlace("Desactivar", ctx + "/clientes/eliminar/" + c.id);
        Pagina t = new Pagina("clientes-detalle-polizas", "Pólizas", "tabla"); t.col("Póliza", "Producto", "Vigencia", "Estado");
        for (Poliza po : Datos.polizasDe(c.id)) t.fila(new String[] {po.numero, ctx + "/polizas/detalle/" + po.numero}, po.producto, po.inicio + " a " + po.fin, po.estado);
        p.secciones.add(t); return p;
    }

    private Pagina formCliente(String ctx, Cliente c) {
        Pagina p = new Pagina(c == null ? "clientes-nuevo" : "clientes-editar", c == null ? "Nuevo cliente" : "Editar " + c.nombre, "formulario");
        p.accion = ctx + "/clientes/" + (c == null ? "nuevo" : "editar/" + c.id);
        p.entrada("rut", "RUT (12345678-9)", "text", c == null ? "" : c.rut).entrada("nombre", "Nombre", "text", c == null ? "" : c.nombre)
         .entrada("correo", "Correo", "text", c == null ? "" : c.correo).entrada("ciudad", "Ciudad", "text", c == null ? "" : c.ciudad).entrada("edad", "Edad", "text", c == null ? "" : c.edad);
        return p;
    }

    private Pagina estadoCuenta(String ctx, Cliente c) {
        Pagina p = new Pagina("clientes-estado-cuenta", "Estado de cuenta de " + c.nombre, "tabla"); p.col("Póliza", "Cuota", "Vence", "Monto (UF)", "Estado");
        for (Poliza po : Datos.polizasDe(c.id)) if (!"ANULADA".equals(po.estado)) for (Cuota q : Datos.cuotasDe(po.numero))
            p.fila(new String[] {po.numero, ctx + "/polizas/detalle/" + po.numero}, q.numero, q.vence, q.monto, q.pagada ? "PAGADA" : (q.vence.compareTo(Datos.FECHA_PROCESO) < 0 ? "ATRASADA" : "POR VENCER"));
        p.subtitulo = "Saldo atrasado: " + Datos.saldoCliente(c.id) + " UF"; p.total = p.filas.size(); return p;
    }

    private Pagina listaPolizas(HttpServletRequest rq, String ctx, String id, String titulo, boolean buscador) {
        String qq = s(rq.getParameter("q")).toLowerCase().trim(), est = s(rq.getParameter("estado")), prod = s(rq.getParameter("producto"));
        List<Poliza> l = new ArrayList<Poliza>();
        for (Poliza po : Datos.POLIZAS) {
            if (qq.length() > 0 && !(po.numero.toLowerCase().contains(qq) || po.getCliente().toLowerCase().contains(qq))) continue;
            if (est.length() > 0 && !po.estado.equals(est)) continue;
            if (prod.length() > 0 && !po.producto.equals(prod)) continue;
            l.add(po);
        }
        Pagina p = new Pagina(id, titulo, "tabla"); p.col("Póliza", "Cliente", "Producto", "Prima (UF)", "Vigencia", "Estado");
        paginar(rq, p, l.size(), ctx + "/polizas/" + (buscador ? "buscar" : "lista"));
        for (Poliza po : Datos.pagina(l, p.pagina, TAM)) p.fila(new String[] {po.numero, ctx + "/polizas/detalle/" + po.numero}, po.getCliente(), po.producto, po.prima, po.inicio + " a " + po.fin, po.estado);
        if (buscador) p.busqueda = qq;
        p.enlace("Nueva póliza", ctx + "/polizas/nueva"); return p;
    }

    private Pagina detallePoliza(HttpServletRequest rq, String ctx, Poliza po) {
        Pagina p = new Pagina("polizas-detalle", "Póliza " + po.numero, "ficha"); p.aviso = s(rq.getParameter("ok"));
        Corredor co = Datos.corredor(po.corredorId);
        p.campo("Cliente", po.getCliente()).campo("Producto", po.producto).campo("Corredor", co == null ? "" : co.nombre).campo("Suma asegurada (UF)", po.sumaUf).campo("Deducible (UF)", po.deducibleUf)
         .campo("Prima anual (UF)", po.prima).campo("Vigencia", po.inicio + " a " + po.fin).campo("Estado", po.estado);
        if (po.renovadaDe.length() > 0) p.campo("Renovación de", po.renovadaDe);
        if (po.motivoAnulacion.length() > 0) p.campo("Motivo de anulación", po.motivoAnulacion);
        p.enlace("Cuotas", ctx + "/polizas/cuotas/" + po.numero).enlace("Ver cliente", ctx + "/clientes/detalle/" + po.clienteId);
        if ("VIGENTE".equals(po.estado)) p.enlace("Editar", ctx + "/polizas/editar/" + po.numero).enlace("Renovar", ctx + "/polizas/renovar/" + po.numero)
            .enlace("Anular", ctx + "/polizas/anular/" + po.numero).enlace("Denunciar siniestro", ctx + "/siniestros/denunciar?poliza=" + po.numero);
        Pagina t = new Pagina("polizas-detalle-siniestros", "Siniestros", "tabla"); t.col("#", "Fecha", "Descripción", "Estado");
        for (Siniestro si : Datos.siniestrosDe(po.numero)) t.fila(new String[] {"#" + si.id, ctx + "/siniestros/detalle/" + si.id}, si.fecha, si.descripcion, si.estado);
        p.secciones.add(t); return p;
    }

    private Pagina formPoliza(String ctx, String cliente) {
        Pagina p = new Pagina("polizas-nueva", "Emitir póliza", "formulario"); p.accion = ctx + "/polizas/nueva"; p.boton = "Emitir";
        List<String> cl = new ArrayList<String>(); for (Cliente c : Datos.CLIENTES) if (c.activo) cl.add(c.id + "=" + c.nombre + " (" + c.rut + ")");
        p.entrada("cliente", "Cliente", "select", cliente == null ? "1" : cliente, cl.toArray(new String[0])).entrada("producto", "Producto", "select", "AUTO", opcionesProductos())
         .entrada("suma", "Suma asegurada (UF)", "text", "500").entrada("deducible", "Deducible (UF)", "select", "0", "0", "3", "5").entrada("corredor", "Corredor", "select", "1", opcionesCorredores());
        return p;
    }

    private Pagina cuotasPoliza(String ctx, Poliza po) {
        Pagina p = new Pagina("polizas-cuotas", "Cuotas de " + po.numero, "tabla"); p.col("Cuota", "Vence", "Monto (UF)", "Estado");
        for (Cuota q : Datos.cuotasDe(po.numero)) p.fila(new String[] {String.valueOf(q.numero), ctx + "/pagos/detalle/" + q.id}, q.vence, q.monto, q.pagada ? "PAGADA" : "PENDIENTE");
        p.total = p.filas.size(); return p;
    }

    private Pagina listaSiniestros(HttpServletRequest rq, String ctx, String id, String titulo, boolean buscador) {
        String est = s(rq.getParameter("estado")), qq = s(rq.getParameter("q")).toLowerCase().trim();
        List<Siniestro> l = new ArrayList<Siniestro>();
        for (Siniestro si : Datos.SINIESTROS) { if (est.length() > 0 && !si.estado.equals(est)) continue; if (qq.length() > 0 && !(si.poliza.toLowerCase().contains(qq) || si.descripcion.toLowerCase().contains(qq))) continue; l.add(si); }
        Pagina p = new Pagina(id, titulo, "tabla"); p.col("#", "Póliza", "Fecha", "Descripción", "Monto (UF)", "Estado");
        paginar(rq, p, l.size(), ctx + "/siniestros/" + (buscador ? "buscar" : "lista"));
        for (Siniestro si : Datos.pagina(l, p.pagina, TAM)) p.fila(new String[] {"#" + si.id, ctx + "/siniestros/detalle/" + si.id}, si.poliza, si.fecha, si.descripcion, si.montoUf, si.estado);
        if (buscador) p.busqueda = qq;
        p.enlace("Denunciar siniestro", ctx + "/siniestros/denunciar"); return p;
    }

    private Pagina detalleSiniestro(HttpServletRequest rq, String ctx, Siniestro si) {
        Pagina p = new Pagina("siniestros-detalle", "Siniestro #" + si.id, "ficha"); p.aviso = s(rq.getParameter("ok"));
        p.campo("Póliza", si.poliza).campo("Fecha", si.fecha).campo("Descripción", si.descripcion).campo("Monto (UF)", si.montoUf).campo("Estado", si.estado).campo("Resolución", si.resolucion).campo("Pago (UF)", si.pagoUf);
        if ("DENUNCIADO".equals(si.estado)) p.enlace("Pasar a evaluación", ctx + "/siniestros/evaluar/" + si.id);
        if ("DENUNCIADO".equals(si.estado) || "EN_EVALUACION".equals(si.estado)) p.enlace("Resolver", ctx + "/siniestros/resolver/" + si.id);
        p.enlace("Ver póliza", ctx + "/polizas/detalle/" + si.poliza); return p;
    }

    private Pagina listaCuotas(HttpServletRequest rq, String ctx, String id, String titulo, boolean pagadas) {
        List<Cuota> l = new ArrayList<Cuota>();
        for (Cuota q : Datos.CUOTAS) if (q.pagada == pagadas && (pagadas || q.vence.compareTo(Datos.FECHA_PROCESO) < 0)) l.add(q);
        Pagina p = new Pagina(id, titulo, "tabla"); p.col("Póliza", "Cuota", "Vence", "Monto (UF)", pagadas ? "Pagada el" : "Estado");
        paginar(rq, p, l.size(), ctx + "/pagos/" + (pagadas ? "lista" : "pendientes"));
        for (Cuota q : Datos.pagina(l, p.pagina, TAM)) p.fila(new String[] {q.poliza, ctx + "/pagos/detalle/" + q.id}, q.numero, q.vence, q.monto, pagadas ? q.fechaPago : "ATRASADA");
        return p;
    }

    // ── auxiliares ────────────────────────────────────────────────────────────────────────────────────────────────
    private void paginar(HttpServletRequest rq, Pagina p, int total, String base) {
        p.total = total; p.paginas = Math.max(1, (total + TAM - 1) / TAM); p.pagina = Math.min(p.paginas, Math.max(1, num(rq.getParameter("pag"))));
        StringBuilder b = new StringBuilder(base).append('?');
        for (String k : new String[] {"q", "estado", "producto", "ciudad"}) if (rq.getParameter(k) != null) b.append(k).append('=').append(enc(rq.getParameter(k))).append('&');
        p.base = b.toString();
    }

    private Pagina especial(String vista, String titulo) { Pagina p = new Pagina(vista, titulo, "especial"); p.vista = vista; return p; }

    private Pagina noEncontrado(HttpServletResponse rs) { rs.setStatus(404); Pagina p = especial("error-404", "Página no encontrada"); return p; }

    private Pagina denegado(HttpServletResponse rs) { rs.setStatus(403); return especial("error-403", "Acceso denegado"); }

    private Pagina ir(HttpServletResponse rs, String url) throws IOException { rs.sendRedirect(url); return null; }

    private void login(HttpServletRequest rq, HttpServletResponse rs, boolean post) throws ServletException, IOException {
        Pagina p = especial("login", "Ingresar");
        if (post) {
            Usuario u = Datos.login(rq.getParameter("usuario"), rq.getParameter("clave"));
            if (u != null) { rq.getSession(true).setAttribute("usuario", u); Datos.auditar(u.usuario, "ingresar", "sesión"); rs.sendRedirect(rq.getContextPath() + "/app/inicio"); return; }
            p.error = "Usuario o clave incorrectos"; rs.setStatus(401);
        }
        rq.setAttribute("p", p);
        rq.getRequestDispatcher("/WEB-INF/vistas/login.jsp").forward(rq, rs);
    }

    private static String[] opcionesProductos() { List<String> o = new ArrayList<String>(); for (Producto x : Datos.PRODUCTOS) o.add(x.codigo + "=" + x.nombre); return o.toArray(new String[0]); }
    private static String[] opcionesCorredores() { List<String> o = new ArrayList<String>(); for (Corredor c : Datos.CORREDORES) o.add(c.id + "=" + c.nombre); return o.toArray(new String[0]); }
    static int num(String x) { try { return Integer.parseInt(x == null ? "" : x.trim()); } catch (NumberFormatException e) { return 0; } }
    static String s(String x) { return x == null ? "" : x; }
    static String enc(String x) { try { return java.net.URLEncoder.encode(x, "UTF-8"); } catch (Exception e) { return ""; } }
}
