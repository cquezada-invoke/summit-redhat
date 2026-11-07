package cl.condor.polizas;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Modelo de una página para las tres plantillas (tabla, ficha y formulario). Java 6: mapas y listas simples, legibles por EL. */
public class Pagina {
    public String id;          // identificador estable de la página (lo lee el plan de pruebas)
    public String titulo;
    public String plantilla;   // tabla | ficha | formulario | especial
    public String vista;       // para las especiales
    public String subtitulo = "";
    public String error = "";
    public String aviso = "";
    public List<String> columnas = new ArrayList<String>();
    public List<List<Map<String, String>>> filas = new ArrayList<List<Map<String, String>>>();
    public List<String[]> campos = new ArrayList<String[]>();             // ficha: etiqueta, valor
    public List<Map<String, Object>> entradas = new ArrayList<Map<String, Object>>();  // formulario
    public List<Map<String, String>> acciones = new ArrayList<Map<String, String>>();  // enlaces o botones (POST)
    public List<Pagina> secciones = new ArrayList<Pagina>();
    public String accion = "";   // formulario: destino
    public String boton = "Guardar";
    public String busqueda = null;  // tabla: texto buscado (null = sin buscador)
    public int pagina = 1, paginas = 1, total = 0;
    public String base = "";

    public Pagina(String id, String titulo, String plantilla) { this.id = id; this.titulo = titulo; this.plantilla = plantilla; }

    public Pagina col(String... c) { for (String x : c) columnas.add(x); return this; }

    public Pagina fila(Object... celdas) {
        List<Map<String, String>> f = new ArrayList<Map<String, String>>();
        for (Object o : celdas) {
            Map<String, String> m = new LinkedHashMap<String, String>();
            if (o instanceof String[]) { String[] a = (String[]) o; m.put("texto", a[0]); m.put("href", a[1]); }
            else m.put("texto", o == null ? "" : String.valueOf(o));
            f.add(m);
        }
        filas.add(f); return this;
    }

    public Pagina campo(String etiqueta, Object valor) { campos.add(new String[] {etiqueta, valor == null ? "" : String.valueOf(valor)}); return this; }

    public Pagina enlace(String texto, String href) {
        Map<String, String> m = new LinkedHashMap<String, String>(); m.put("texto", texto); m.put("href", href); m.put("tipo", "enlace"); acciones.add(m); return this;
    }

    public Pagina entrada(String nombre, String etiqueta, String tipo, Object valor, String... opciones) {
        Map<String, Object> m = new LinkedHashMap<String, Object>(); m.put("nombre", nombre); m.put("etiqueta", etiqueta); m.put("tipo", tipo);
        m.put("valor", valor == null ? "" : String.valueOf(valor));
        List<String[]> ops = new ArrayList<String[]>();
        for (String o : opciones) { int i = o.indexOf('='); ops.add(i > 0 ? new String[] {o.substring(0, i), o.substring(i + 1)} : new String[] {o, o}); }
        m.put("opciones", ops); entradas.add(m); return this;
    }

    public String getId() { return id; } public String getTitulo() { return titulo; } public String getPlantilla() { return plantilla; } public String getVista() { return vista; }
    public String getSubtitulo() { return subtitulo; } public String getError() { return error; } public String getAviso() { return aviso; }
    public List<String> getColumnas() { return columnas; } public List<List<Map<String, String>>> getFilas() { return filas; } public List<String[]> getCampos() { return campos; }
    public List<Map<String, Object>> getEntradas() { return entradas; } public List<Map<String, String>> getAcciones() { return acciones; } public List<Pagina> getSecciones() { return secciones; }
    public String getAccion() { return accion; } public String getBoton() { return boton; } public String getBusqueda() { return busqueda; }
    public int getPagina() { return pagina; } public int getPaginas() { return paginas; } public int getTotal() { return total; } public String getBase() { return base; }
}
