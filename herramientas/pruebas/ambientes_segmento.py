"""Prueba: el mapa de un segmento no arrastra estados de producción a QA ni a desarrollo (solo lectura).
  sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - [segmento ...] < ambientes_segmento.py
Sin argumentos revisa todos los segmentos de aplicaciones del cliente del laboratorio. Para cada ambiente pide lo mismo que
el navegador (/mapa/datos y /mapa/salud/flotante) y verifica:
  · data lake: la derivación solo aparece activa en el ambiente donde está configurada; en los otros, «no configurado en …»
  · métricas de la aplicación, URL de la aplicación, SLA y RTO/RPO: solo en producción
  · OpenShift: solo los namespaces del ambiente que se ve
  · salud y flotante: toda máquina nombrada en un ítem pertenece al ambiente que se ve
Imprime «CASO <segmento> <ambiente> ok|falla · detalle» y al final «RESULTADO ok|falla»."""
import re
import sys
import urllib.parse as U

from app import create_app

app = create_app()
c = app.test_client()
with app.app_context():
    from app.bd import q, q1
    cl = q1("SELECT c.* FROM clientes c JOIN segmentos s ON s.cliente_id=c.id WHERE s.nombre='App de pagos' ORDER BY c.id LIMIT 1") \
        or q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
    u = q1("SELECT * FROM usuarios WHERE usuario='cquezada'") or q1("SELECT * FROM usuarios WHERE es_admin=1 ORDER BY id LIMIT 1")
    segs = sys.argv[1:] or [s["nombre"] for s in q("SELECT nombre FROM segmentos WHERE cliente_id=? AND grupo='aplicaciones' ORDER BY nombre", cl["id"])]
    todas = {e["nombre"]: e for e in q("SELECT nombre, ambiente FROM equipos WHERE cliente_id=? AND estado!='retirado'", cl["id"])}
with c.session_transaction() as s:
    s["uid"], s["csrf"], s["cliente_id"] = u["id"], "prueba", cl["id"]

fallas = 0


def caso(seg, amb, ok, det):
    global fallas
    fallas += 0 if ok else 1
    print(f"CASO {seg} | {amb} {'ok' if ok else 'falla'} · {det}")


for seg in segs:
    vistos = {}
    for amb in ("produccion", "qa", "desarrollo"):
        r = c.get(f"/mapa/datos?vista=negocio&nivel=segmento&valor={U.quote(seg)}&ambiente={amb}", headers={"X-Requested-With": "fetch"})
        d = r.get_json(silent=True) or {}
        s_ = d.get("segmento") or {}
        if r.status_code != 200 or not s_:
            continue
        els = [e["data"] for e in d.get("elementos") or []]
        maqs = {x.get("nombre") for x in els if x.get("tipo") == "equipo" and x.get("nombre")}
        vistos[amb] = maqs
        prod = amb == "produccion"
        bifs = [x["bif"] for x in els if x.get("bif")]
        if bifs:
            b = bifs[0]
            propio = b.get("ambiente_lake") == amb
            caso(seg, amb, (b.get("activa") is False or propio) and (bool(b.get("no_configurado")) != propio),
                 f"data lake {'activo' if b.get('activa') else 'inactivo'} · configurado en {b.get('ambiente_lake')} · {b.get('no_configurado') or 'propio'}")
        if not prod:
            caso(seg, amb, not s_.get("metricas_app"), "sin métricas de la aplicación de producción")
            caso(seg, amb, not any(x.get("app_url") and not x["app_url"].startswith("/") for x in els), "sin URL de la aplicación de producción en sus cajas")
            caso(seg, amb, not any(re.match(r"SLA|Sin RTO/RPO", x.get("titulo") or "") for x in s_.get("salud") or []), "sin SLA ni RTO/RPO en la salud")
        caso(seg, amb, all((o.get("ambiente") or "produccion") == amb for o in s_.get("ocp") or []),
             "OpenShift solo del ambiente: " + (", ".join(o["namespace"] for o in s_.get("ocp") or []) or "ninguno"))
        ajenas = sorted({n for x in s_.get("salud") or [] for n in x.get("maquinas") or [] if (todas.get(n) or {}).get("ambiente") not in (amb, None, "")
                         and n not in maqs})
        caso(seg, amb, not ajenas, "máquinas de la salud del ambiente" + (": ajenas " + ", ".join(ajenas) if ajenas else ""))
        f = c.get(f"/mapa/salud/flotante?segmento={U.quote(seg)}&ambiente={amb}").get_json(silent=True) or {}
        caso(seg, amb, f.get("ambiente") == amb and f.get("salud_pct") == s_.get("salud_pct"),
             f"flotante del ambiente ({f.get('ambiente')}, {f.get('salud_pct')} % = barra {s_.get('salud_pct')} %)")
        nombres = set(re.findall(r'<div class="sl-n">([^<]+)</div>', f.get("html") or ""))
        ajenas_f = sorted(n for n in (todas.keys() & {w for t in nombres for w in re.split(r"[\s→:]+", t)})
                          if (todas[n].get("ambiente") or "") not in (amb, "") and n not in maqs)
        caso(seg, amb, not ajenas_f, "casos del flotante del ambiente" + (": ajenos " + ", ".join(ajenas_f) if ajenas_f else ""))
    for amb in ("qa", "desarrollo"):
        if amb in vistos and "produccion" in vistos:
            cruz = sorted(n for n in vistos[amb] & vistos["produccion"] if (todas.get(n) or {}).get("ambiente") == "produccion")
            caso(seg, amb, not cruz, "sin máquinas de producción en el mapa" + (": " + ", ".join(cruz) if cruz else ""))
print("RESULTADO", "ok" if not fallas else f"falla ({fallas})")
