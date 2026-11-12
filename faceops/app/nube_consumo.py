"""Consumo y estado de cada nube (GCP, AWS, Azure). FaceOps los recupera por AAP con la credencial cifrada de la
conexión («Nube · Recuperar consumo y estado», solo lectura): una vez al día por nube y, a pedido, con un ticket
(«Actualizar ahora»). Lo guarda en su base; la lista de nubes, la ficha y su barra de salud leen solo de aquí, nunca en
vivo contra la nube.

Consumo, en orden: facturación real (export a BigQuery / Cost Explorer / Cost Management) → estimación con precios
públicos sobre lo inventariado (rotulada «estimado») → «sin datos» con lo que falta habilitar. Nada inventado: lo que la
credencial no puede leer queda como «sin permiso: falta rol X»."""
import json
import time
from datetime import datetime, timedelta, timezone

from .bd import ahora, auditar, con, ej, j, q, q1

RECUPERAR = "Nube · Recuperar consumo y estado"
CADA_HORAS = 24
DIAS_SNAP, DIAS_CLAVE = 90, 90
CUOTA_AMARILLO, CUOTA_ROJO = 80, 90
TENDENCIA_AMARILLO = 20   # % de alza contra el mes anterior
# Remediación por ticket (si la acción está publicada y certificada) o guía (enlace a la consola del proveedor)
ACCION_FIREWALL = "Nube · Vigilar firewall"
ACCION_OCIOSOS = "Nube · Reporte de huérfanos con limpieza"
COMO_HABILITAR = "https://cloud.google.com/billing/docs/how-to/export-data-bigquery-setup"
COMO_ROL = "https://cloud.google.com/billing/docs/how-to/billing-access"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS nube_resumen (cliente_id INTEGER, infra_id INTEGER, ts TEXT, job INTEGER, datos TEXT, PRIMARY KEY (cliente_id, infra_id));
CREATE TABLE IF NOT EXISTS nube_resumen_trabajos (id INTEGER PRIMARY KEY, cliente_id INTEGER, infra_id INTEGER, job INTEGER, creado TEXT, estado TEXT DEFAULT 'en curso');
CREATE TABLE IF NOT EXISTS nube_resumen_ult (cliente_id INTEGER PRIMARY KEY, job INTEGER);
CREATE TABLE IF NOT EXISTS nube_costo_recurso (cliente_id INTEGER, infra_id INTEGER, recurso_id TEXT, tipo TEXT, nombre TEXT, servicio TEXT,
  grupo TEXT, zona TEXT, etiquetas TEXT, mes_actual_usd REAL, promedio_usd REAL, fuente TEXT, nota TEXT, muestra_ts TEXT,
  PRIMARY KEY (cliente_id, infra_id, recurso_id));
CREATE INDEX IF NOT EXISTS nube_costo_recurso_nombre ON nube_costo_recurso(cliente_id, nombre);
"""
_listo = set()


def _esquema():
    if "ok" not in _listo:
        con().executescript(ESQUEMA)
        _listo.add("ok")


# ------------------------------------------------------------------ lectura (solo FaceOps)
def de_nube(cliente_id, infra_id):
    _esquema()
    r = q1("SELECT * FROM nube_resumen WHERE cliente_id=? AND infra_id=?", cliente_id, infra_id)
    if not r:
        return None
    d = j(r["datos"], {}) or {}
    d["_ts"], d["_job"] = r["ts"], r["job"]
    return d


def de_cliente(cliente_id):
    _esquema()
    return {r["infra_id"]: dict(j(r["datos"], {}) or {}, _ts=r["ts"], _job=r["job"]) for r in q("SELECT * FROM nube_resumen WHERE cliente_id=?", cliente_id)}


def usd(v):
    """«USD 1.234» con separador de miles chileno (sin decimales; bajo 10, con dos)."""
    if v is None:
        return "—"
    v = float(v)
    if 0 < abs(v) < 10:
        return "USD " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "USD " + f"{v:,.0f}".replace(",", ".")


# ------------------------------------------------------------------ lanzar (diario) y recoger
def parametros(inf):
    from .infraestructuras import DATO_CUENTA
    det = j(inf.get("detalle"), {}) if isinstance(inf.get("detalle"), str) else (inf.get("detalle_d") or inf.get("detalle") or {})
    k = DATO_CUENTA.get(inf["tipo"], ("proyecto", ""))[0]
    return {"nr_nube": inf["nombre"], "nr_infra_id": inf["id"], "nr_proveedor": inf["tipo"], "nr_cuenta": det.get(k) or "",
            "nr_zona": det.get("zona") or "", "nr_tabla_bq": det.get("tabla_facturacion") or ""}


def credencial(aap, inf):
    from .aap import ErrorAAP
    cr = (aap.get("credentials/", name=inf.get("credencial") or "").get("results") or [None])[0] if inf.get("credencial") else None
    if not cr:
        raise ErrorAAP(f"{inf['nombre']} no tiene su credencial en AAP: usa «Conectar» en la ficha de la nube")
    return cr["id"]


def _certificada(cliente_id):
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, RECUPERAR)
    if not a or not a.get("aap_id"):
        return None
    from .pruebas import estado, exceptuada
    if estado(dict(a, cliente_id=cliente_id))["estado"] != "certificada" and not exceptuada(RECUPERAR):
        return None
    return a


def lanzar_diario(c, forzar=False, prueba=False):
    """Una vez al día por nube conectada (solo lectura, sin ticket, como la medición de uso): lanza la plantilla con la
    credencial de la conexión. Solo si la acción está certificada (regla de prueba); prueba=True lo salta y forzar=True
    ignora la última muestra: los usa solo el arnés del plan de prueba."""
    from .aap import AAP, ErrorAAP
    _esquema()
    a = _certificada(c["id"]) if not prueba else q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", c["id"], RECUPERAR)
    if not a:
        return []
    nubes = q("SELECT * FROM infraestructuras WHERE cliente_id=? AND tipo IN ('gcp','aws','azure') AND credencial!=''", c["id"])
    if not nubes:
        return []
    aap, jobs = AAP.de_cliente(c), []
    limite = (datetime.now(timezone.utc) - timedelta(hours=CADA_HORAS)).isoformat(timespec="seconds")
    for inf in nubes:
        ult = q1("SELECT ts FROM nube_resumen WHERE cliente_id=? AND infra_id=?", c["id"], inf["id"])
        if not forzar and ult and (ult["ts"] or "") > limite:
            continue
        if not forzar and q1("SELECT 1 x FROM nube_resumen_trabajos WHERE cliente_id=? AND infra_id=? AND estado='en curso' AND creado>?", c["id"], inf["id"], limite):
            continue
        try:
            job = aap.lanzar("job", a["aap_id"], extra_vars=parametros(inf), credenciales=[credencial(aap, inf)])
        except ErrorAAP as e:
            auditar("sistema", c["id"], "error consumo de nube", inf["nombre"], {"error": str(e)[:300]})
            continue
        ej("INSERT INTO nube_resumen_trabajos(cliente_id, infra_id, job, creado) VALUES (?,?,?,?)", c["id"], inf["id"], job, ahora())
        jobs.append(job)
    return jobs


_ult_recogida = {}


def recoger(c, forzar=False):
    """Guarda lo que trajo cada trabajo terminado de la plantilla (diarios y por ticket). Cada 60 s como máximo."""
    from .aap import AAP
    _esquema()
    if not forzar and time.time() - _ult_recogida.get(c["id"], 0) < 60:
        return 0
    _ult_recogida[c["id"]] = time.time()
    a = q1("SELECT aap_id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", c["id"], RECUPERAR)
    if not a or not a["aap_id"]:
        return 0
    ult = (q1("SELECT job FROM nube_resumen_ult WHERE cliente_id=?", c["id"]) or {}).get("job") or 0
    aap = AAP.de_cliente(c)
    jobs = aap.get("jobs/", job_template=a["aap_id"], id__gt=ult, order_by="id", status__in="successful,failed,error,canceled", page_size=50).get("results") or []
    propias = {r["id"] for r in q("SELECT id FROM infraestructuras WHERE cliente_id=?", c["id"])}
    n = 0
    for jb in jobs:
        art = (aap.get(f"jobs/{jb['id']}/").get("artifacts") or {}).get("faceops_nube_resumen") or {}
        for iid, d in art.items():
            try:
                iid = int(iid)
            except ValueError:
                continue
            if iid not in propias or not isinstance(d, dict):
                continue
            ts = jb.get("finished") or ahora()
            ej("INSERT INTO nube_resumen(cliente_id, infra_id, ts, job, datos) VALUES (?,?,?,?,?) ON CONFLICT(cliente_id, infra_id) DO UPDATE SET "
               "ts=excluded.ts, job=excluded.job, datos=excluded.datos", c["id"], iid, ts, jb["id"], json.dumps(d, ensure_ascii=False))
            _guardar_recursos(c["id"], iid, d, ts)
            n += 1
        ej("UPDATE nube_resumen_trabajos SET estado=? WHERE cliente_id=? AND job=?", jb.get("status") or "terminado", c["id"], jb["id"])
        ult = jb["id"]
    if jobs:
        ej("INSERT INTO nube_resumen_ult(cliente_id, job) VALUES (?, ?) ON CONFLICT(cliente_id) DO UPDATE SET job=excluded.job", c["id"], ult)
    ej("UPDATE nube_resumen_trabajos SET estado='sin respuesta' WHERE cliente_id=? AND estado='en curso' AND creado<?", c["id"],
       (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(timespec="seconds"))
    return n


def _guardar_recursos(cliente_id, infra_id, d, ts):
    """Reemplaza el costo por recurso de la nube con la última muestra (solo si la fuente entregó consumo)."""
    cons = (d or {}).get("consumo") or {}
    if not cons.get("ok"):
        return
    ej("DELETE FROM nube_costo_recurso WHERE cliente_id=? AND infra_id=?", cliente_id, infra_id)
    for r in cons.get("recursos") or []:
        ej("INSERT OR REPLACE INTO nube_costo_recurso(cliente_id, infra_id, recurso_id, tipo, nombre, servicio, grupo, zona, etiquetas, mes_actual_usd, "
           "promedio_usd, fuente, nota, muestra_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cliente_id, infra_id, str(r.get("id") or r.get("nombre")), r.get("tipo") or "",
           r.get("nombre") or "", r.get("servicio") or "", r.get("grupo") or "", r.get("zona") or "", json.dumps(r.get("etiquetas") or {}, ensure_ascii=False),
           r.get("mes_actual_usd"), r.get("promedio_usd"), cons.get("fuente") or "", r.get("nota") or "", ts)


# ------------------------------------------------------------------ API pública: costo por recurso (la leen otras pantallas)
def costo_recursos(cliente_id, infra_id=None, tipo=None):
    """Costo por recurso de las nubes del cliente, de la última muestra recuperada por AAP. Una fila por recurso:
    {infra_id, recurso_id, tipo (vm | disco | ip | snapshot | producto | servicio), nombre, servicio (Compute Engine,
    Cloud SQL, Cloud Storage…), grupo (Compute | Storage | Red | Otros), zona, etiquetas (dict), mes_actual_usd,
    promedio_usd, fuente (facturacion | estimado), nota, muestra_ts (UTC ISO)}. Los USD pueden ser None (sin precio)."""
    _esquema()
    sql, args = "SELECT * FROM nube_costo_recurso WHERE cliente_id=?", [cliente_id]
    if infra_id is not None:
        sql, args = sql + " AND infra_id=?", args + [infra_id]
    if tipo:
        sql, args = sql + " AND tipo=?", args + [tipo]
    return [dict(r, etiquetas=j(r["etiquetas"], {})) for r in q(sql + " ORDER BY COALESCE(mes_actual_usd, -1) DESC, nombre", *args)]


def costo_de(cliente_id, nombre, infra_id=None):
    """Costo de un recurso por su nombre (VM, bucket, instancia de Cloud SQL…) o su id; None si la fuente no lo trae."""
    _esquema()
    sql, args = "SELECT * FROM nube_costo_recurso WHERE cliente_id=? AND (nombre=? OR recurso_id=?)", [cliente_id, nombre, nombre]
    if infra_id is not None:
        sql, args = sql + " AND infra_id=?", args + [infra_id]
    r = q1(sql + " ORDER BY (tipo='vm') DESC, muestra_ts DESC", *args)
    return dict(r, etiquetas=j(r["etiquetas"], {})) if r else None


def costo_maquinas(cliente_id):
    """{nombre de VM: fila de costo_recursos} de todas las nubes del cliente (para listas de máquinas)."""
    return {r["nombre"]: r for r in costo_recursos(cliente_id, tipo="vm")}


def muestra(cliente_id, infra_id):
    """Nivel de consumo de la nube en la última muestra: {mes_actual_usd, promedio_usd, fuente, ts, ok, falta}."""
    d = de_nube(cliente_id, infra_id)
    if not d:
        return None
    c = d.get("consumo") or {}
    return {"ok": bool(c.get("ok")), "mes_actual_usd": c.get("mes_actual_usd"), "promedio_usd": c.get("promedio_usd"), "fuente": c.get("fuente") or "sin datos",
            "detalle": c.get("detalle_fuente") or "", "falta": c.get("falta") or d.get("error") or "", "ts": d.get("_ts"), "desglose": c.get("desglose") or {}}


def ultimo_intento(cliente_id, nube):
    """Último pedido de recuperación de la nube (ticket o cálculo diario): {numero, estado, fecha, tid}. Sirve para mostrar un
    estado claro mientras no hay muestra («sin datos aún · último intento …»), nunca una carga indefinida."""
    _esquema()
    t = q1("SELECT t.id, t.numero, t.estado, t.creado FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
           "WHERE t.cliente_id=? AND a.nombre_aap=? AND t.parametros LIKE ? ORDER BY t.id DESC LIMIT 1", cliente_id, RECUPERAR, f'%{json.dumps(nube)[1:-1]}%')
    inf = q1("SELECT id FROM infraestructuras WHERE cliente_id=? AND nombre=?", cliente_id, nube)
    d = q1("SELECT job, creado, estado FROM nube_resumen_trabajos WHERE cliente_id=? AND infra_id=? ORDER BY id DESC LIMIT 1", cliente_id, inf["id"]) if inf else None
    if d and (not t or (d["creado"] or "") > (t["creado"] or "")):
        return {"numero": f"trabajo {d['job']} (diario)", "estado": d["estado"], "fecha": d["creado"], "tid": None}
    return {"numero": t["numero"], "estado": t["estado"], "fecha": t["creado"], "tid": t["id"]} if t else None


# ------------------------------------------------------------------ consolidado y salud
def _sec(d, k):
    s = (d or {}).get(k) or {}
    return s if isinstance(s, dict) else {}


def _color_n(n, malo="rojo"):
    return "verde" if not n else malo


def consolidado(d, tipo="gcp"):
    """Ítems del consolidado de la ficha, por grupo: [{grupo, items: [{nombre, valor, color, detalle, falta, accion, enlace}]}].
    color: verde | amarillo | rojo | gris (sin dato)."""
    if not d:
        return []
    out = []

    def it(nombre, sec, valor_fn, color_fn, detalle_fn=None, accion=None, enlace=None):
        if not sec.get("ok"):
            return {"nombre": nombre, "valor": "—", "color": "gris", "falta": sec.get("falta") or "sin datos", "detalle": [], "accion": None, "enlace": None}
        v = valor_fn(sec)
        return {"nombre": nombre, "valor": v[0], "n": v[1], "color": color_fn(v[1]), "falta": "", "detalle": detalle_fn(sec) if detalle_fn else [],
                "accion": accion if v[1] else None, "enlace": enlace if v[1] else None}

    # Costo
    c = _sec(d, "consumo")
    pres = _sec(d, "presupuesto")
    costo = []
    if c.get("ok"):
        est = c.get("fuente") == "estimado"
        pref = "≈ " if est else ""
        ant, act = c.get("mes_anterior_usd"), c.get("mes_actual_usd")
        costo.append({"nombre": "Consumo del mes en curso", "valor": pref + usd(act), "color": "verde", "falta": "", "detalle": [c.get("detalle_fuente", "")] if est else [], "accion": None, "enlace": None,
                      "estimado": est})
        costo.append({"nombre": "Promedio mensual", "valor": pref + usd(c.get("promedio_usd")), "color": "verde", "falta": "",
                      "detalle": [("Estimado: " if est else "Facturación real · últimos meses cerrados: " + ", ".join(c.get("meses") or []) + " · ") + (c.get("detalle_fuente") or "")],
                      "accion": None, "enlace": None, "estimado": est})
        dg = c.get("desglose") or {}
        costo.append({"nombre": "Desglose mensual por servicio", "valor": " · ".join(f"{k} {pref}{usd(dg[k])}" for k in ("Compute", "Storage", "Red", "Otros") if k in dg) or "—",
                      "color": "verde", "falta": "", "detalle": [], "accion": None, "enlace": None})
        if ant:
            # tendencia: mes en curso proyectado a 30 días contra el mes anterior cerrado
            dia = max(1, datetime.now(timezone.utc).day)
            proy = (act or 0) * 30.4 / dia
            tend = round(100 * (proy - ant) / ant)
            costo.append({"nombre": "Tendencia contra el mes anterior", "valor": f"{'+' if tend >= 0 else ''}{tend} % (proyectado {usd(proy)} vs {usd(ant)})",
                          "color": "amarillo" if tend > TENDENCIA_AMARILLO else "verde", "falta": "", "detalle": [], "accion": None, "enlace": None})
        else:
            costo.append({"nombre": "Tendencia contra el mes anterior", "valor": "—", "color": "gris",
                          "falta": "requiere facturación real (export a BigQuery)" if est else "sin mes anterior facturado", "detalle": [], "accion": None, "enlace": COMO_HABILITAR if est else None})
        top = c.get("top_servicios") or []
        costo.append({"nombre": "Top 3 servicios por costo", "valor": " · ".join(f"{s['servicio']} {usd(s['usd'])}" for s in top) or "sin consumo",
                      "color": "verde", "falta": "", "detalle": [], "accion": None, "enlace": None})
        if pres.get("ok") and pres.get("usd_mes"):
            sobre = (c.get("promedio_usd") or 0) > pres["usd_mes"]
            costo.append({"nombre": "Presupuesto", "valor": f"{usd(pres['usd_mes'])}/mes{(' · ' + pres.get('nombre')) if pres.get('nombre') else ''}",
                          "color": "rojo" if sobre else "verde", "falta": "", "detalle": ["El promedio mensual supera el presupuesto"] if sobre else [], "accion": None, "enlace": None})
        else:
            costo.append({"nombre": "Presupuesto", "valor": "—" if not pres.get("ok") else "sin presupuesto definido", "color": "gris",
                          "falta": pres.get("falta") or "", "detalle": [], "accion": None, "enlace": None})
    else:
        costo.append({"nombre": "Consumo", "valor": "sin datos", "color": "gris", "falta": c.get("falta") or "no se pudo leer",
                      "detalle": [f"{k}: {v}" for k, v in (c.get("intentos") or {}).items()], "accion": None, "enlace": COMO_HABILITAR})
    out.append({"grupo": "Costo", "items": costo})

    # Recursos
    maq = _sec(d, "maquinas")
    rec = []
    if maq.get("ok"):
        ls = maq.get("lista") or []
        on = sum(1 for m in ls if (m.get("estado") or "").upper() in ("RUNNING", "VM RUNNING"))
        por_r = {}
        for m in ls:
            por_r[m.get("region") or "—"] = por_r.get(m.get("region") or "—", 0) + 1
        rec.append({"nombre": "Máquinas", "valor": f"{len(ls)} · {on} encendidas · {len(ls) - on} apagadas", "n": len(ls), "color": "verde", "falta": "",
                    "detalle": [f"{r}: {n}" for r, n in sorted(por_r.items())], "accion": None, "enlace": None})
    else:
        rec.append({"nombre": "Máquinas", "valor": "—", "color": "gris", "falta": maq.get("falta") or "sin datos", "detalle": [], "accion": None, "enlace": None})
    rec.append(it("Discos sin usar", _sec(d, "discos"), lambda s: (str(len([x for x in s.get("lista") or [] if not x.get("en_uso")])), len([x for x in s.get("lista") or [] if not x.get("en_uso")])),
                  lambda n: _color_n(n, "amarillo"), lambda s: [f"{x['nombre']} · {x.get('gb')} GB · {x.get('zona')}" for x in s.get("lista") or [] if not x.get("en_uso")][:10], ACCION_OCIOSOS))
    rec.append(it("IPs estáticas sin uso", _sec(d, "ips"), lambda s: (str(len([x for x in s.get("lista") or [] if not x.get("en_uso") and x.get("tipo") == "EXTERNAL"])), len([x for x in s.get("lista") or [] if not x.get("en_uso") and x.get("tipo") == "EXTERNAL"])),
                  lambda n: _color_n(n, "amarillo"), lambda s: [f"{x.get('ip')} · {x.get('region')}" for x in s.get("lista") or [] if not x.get("en_uso") and x.get("tipo") == "EXTERNAL"][:10], ACCION_OCIOSOS))
    viejos = lambda s: [x for x in s.get("lista") or [] if (x.get("dias") or 0) > DIAS_SNAP]
    rec.append(it(f"Snapshots de más de {DIAS_SNAP} días", _sec(d, "snapshots"), lambda s: (str(len(viejos(s))), len(viejos(s))), lambda n: _color_n(n, "amarillo"),
                  lambda s: [f"{x['nombre']} · {x.get('dias')} días" for x in viejos(s)][:10], ACCION_OCIOSOS))
    out.append({"grupo": "Recursos", "items": rec})

    # Exposición y seguridad
    abiertas = lambda s: [x for x in s.get("lista") or [] if x.get("abierta")]
    expo = [it("Reglas de firewall abiertas a 0.0.0.0/0", _sec(d, "firewall"), lambda s: (str(len(abiertas(s))), len(abiertas(s))), _color_n,
               lambda s: [f"{x['nombre']} · {x.get('red')} · {x.get('puertos')}" for x in abiertas(s)][:10], ACCION_FIREWALL)]
    con_pub = lambda s: [x for x in s.get("lista") or [] if x.get("publicas")]
    expo.append(it("Máquinas con IP pública", maq, lambda s: (str(len(con_pub(s))), len(con_pub(s))), lambda n: _color_n(n, "amarillo"),
                   lambda s: [f"{x['nombre']} · {', '.join(x['publicas'])}" for x in con_pub(s)][:10]))
    vie_k = lambda s: [x for x in s.get("lista") or [] if (x.get("dias") or 0) > DIAS_CLAVE]
    expo.append(it(f"Claves de cuentas de servicio con más de {DIAS_CLAVE} días", _sec(d, "claves"), lambda s: (str(len(vie_k(s))), len(vie_k(s))), _color_n,
                   lambda s: [f"{x['cuenta']} · clave {x['clave']}… · {x.get('dias')} días" for x in vie_k(s)][:10], None,
                   "https://console.cloud.google.com/iam-admin/serviceaccounts" if tipo == "gcp" else None))
    out.append({"grupo": "Exposición y seguridad", "items": expo})

    # Optimización
    r = _sec(d, "recomendaciones")
    if r.get("ok"):
        ls = r.get("lista") or []
        ahorro = sum(x.get("ahorro_usd_mes") or 0 for x in ls)
        opt = [{"nombre": "Recomendaciones (rightsizing y recursos ociosos)", "valor": f"{len(ls)} · ahorro estimado {usd(ahorro)}/mes", "n": len(ls),
                "color": _color_n(len(ls), "amarillo"), "falta": "", "detalle": [f"{x['tipo']} · {x['lugar']} · {x['descripcion'][:120]}{(' · ' + usd(x['ahorro_usd_mes']) + '/mes') if x.get('ahorro_usd_mes') else ''}" for x in ls][:10],
                "accion": ACCION_OCIOSOS if ls else None, "enlace": None}]
    else:
        opt = [{"nombre": "Recomendaciones (rightsizing y recursos ociosos)", "valor": "—", "color": "gris", "falta": r.get("falta") or "sin datos", "detalle": [], "accion": None, "enlace": None}]
    out.append({"grupo": "Optimización", "items": opt})

    # Cuotas y regiones
    cu = _sec(d, "cuotas")
    cerca = lambda s: sorted([x for x in s.get("lista") or [] if (x.get("pct") or 0) >= CUOTA_AMARILLO], key=lambda x: -x["pct"])
    qi = it(f"Cuotas sobre {CUOTA_AMARILLO} %", cu, lambda s: (str(len(cerca(s))), len(cerca(s))),
            lambda n: "verde", lambda s: [f"{x['metrica']} · {x['ambito']} · {x['uso']:g}/{x['limite']:g} ({x['pct']} %)" for x in cerca(s)][:10], None,
            "https://console.cloud.google.com/iam-admin/quotas" if tipo == "gcp" else None)
    if cu.get("ok") and cerca(cu):
        qi["color"] = "rojo" if cerca(cu)[0]["pct"] >= CUOTA_ROJO else "amarillo"
    rg = _sec(d, "regiones")
    out.append({"grupo": "Cuotas y regiones", "items": [qi, {"nombre": "Regiones usadas", "valor": ", ".join(rg.get("lista") or []) or "ninguna", "n": len(rg.get("lista") or []),
                                                          "color": "verde" if rg.get("ok") else "gris", "falta": "" if rg.get("ok") else "sin datos", "detalle": [], "accion": None, "enlace": None}]})
    return out


def criterios(d):
    """Criterios de salud de la nube a partir de lo recuperado (pct None = no se pudo leer: no cuenta).
    rem: «accion:<nombre en AAP>» (ticket), «actualizar» (recuperar de nuevo) o «enlace:<url>» (guía)."""
    if not d:
        return []
    out = []

    def c_(cid, pr, nom, exp, pct, rem, faltan=None):
        out.append({"id": cid, "principio": pr, "nombre": nom, "explica": exp, "pct": pct, "rem": rem,
                    "faltan": [{"nombre": f} for f in (faltan or [])][:10], "n_faltan": len(faltan or [])})

    fw = _sec(d, "firewall")
    if fw.get("ok"):
        ing = fw.get("lista") or []
        ab = [x["nombre"] for x in ing if x.get("abierta")]
        c_("NB7", "Seguridad", "Firewall sin reglas abiertas a Internet", "Ninguna regla de entrada permite 0.0.0.0/0",
           round(100 * (len(ing) - len(ab)) / len(ing)) if ing else 100, f"accion:{ACCION_FIREWALL}", ab)
    k = _sec(d, "claves")
    if k.get("ok"):
        ls = k.get("lista") or []
        vie = [f"{x['cuenta']} ({x.get('dias')} días)" for x in ls if (x.get("dias") or 0) > DIAS_CLAVE]
        c_("NB8", "Seguridad", f"Claves de cuentas de servicio rotadas (≤ {DIAS_CLAVE} días)", "Claves de usuario creadas hace menos de 90 días",
           round(100 * (len(ls) - len(vie)) / len(ls)) if ls else 100, "enlace:https://console.cloud.google.com/iam-admin/serviceaccounts", vie)
    oc, tot = [], 0
    for sec, fil in (("discos", lambda x: not x.get("en_uso")), ("ips", lambda x: not x.get("en_uso") and x.get("tipo") == "EXTERNAL"),
                     ("snapshots", lambda x: (x.get("dias") or 0) > DIAS_SNAP)):
        s = _sec(d, sec)
        if s.get("ok"):
            tot += len(s.get("lista") or [])
            oc += [x.get("nombre") or x.get("ip") for x in s.get("lista") or [] if fil(x)]
    if any(_sec(d, s_).get("ok") for s_ in ("discos", "ips", "snapshots")):
        c_("NB9", "Costo", "Sin recursos ociosos", "Discos sin usar, IPs estáticas sin uso y snapshots de más de 90 días",
           round(100 * (tot - len(oc)) / tot) if tot else 100, f"accion:{ACCION_OCIOSOS}", oc)
    cu = _sec(d, "cuotas")
    if cu.get("ok"):
        ls = cu.get("lista") or []
        cerca = [f"{x['metrica']} {x['ambito']} {x['pct']} %" for x in ls if (x.get("pct") or 0) >= CUOTA_AMARILLO]
        c_("NB10", "Disponibilidad", f"Cuotas holgadas (< {CUOTA_AMARILLO} %)", "Ninguna cuota del proyecto o de sus regiones cerca del límite",
           round(100 * (len(ls) - len(cerca)) / len(ls)) if ls else 100, "enlace:https://console.cloud.google.com/iam-admin/quotas", cerca)
    r = _sec(d, "recomendaciones")
    if r.get("ok"):
        ls = r.get("lista") or []
        maqs = len(_sec(d, "maquinas").get("lista") or []) + len(_sec(d, "discos").get("lista") or [])
        c_("NB11", "Rendimiento", "Recomendaciones de optimización atendidas", "Sin recomendaciones activas de rightsizing ni de recursos ociosos",
           max(0, round(100 * (maqs - len(ls)) / maqs)) if maqs else (0 if ls else 100), f"accion:{ACCION_OCIOSOS}", [x["tipo"] + " · " + x["lugar"] for x in ls])
    c = _sec(d, "consumo")
    p = _sec(d, "presupuesto")
    if c.get("ok") and p.get("ok") and p.get("usd_mes"):
        prom = c.get("promedio_usd") or 0
        c_("NB12", "Costo", "Costo dentro del presupuesto", f"Promedio mensual {usd(prom)} contra un presupuesto de {usd(p['usd_mes'])}",
           100 if prom <= p["usd_mes"] else max(0, round(100 * p["usd_mes"] / prom)), "actualizar")
    return out
