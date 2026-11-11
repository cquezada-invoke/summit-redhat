"""KPI y OKR propios de cada cliente (Monitoreo › KPI y OKR).

Cada cliente tiene su lista en la tabla kpi_okr: los predefinidos se siembran una vez (desde datos/kpi-okr.yml y lo que
FaceOps ya mide de verdad) y quedan editables y eliminables como cualquier otro. Un KPI toma su valor de una métrica de
FaceOps (app/indicadores.py y los cálculos de seguridad) o se registra a mano; cada valor del día queda en kpi_okr_valores
para la tendencia. Un OKR es un objetivo con resultados clave (cada uno con su meta y su avance, manual o de una métrica)."""
import json
import os
from datetime import datetime, timezone

from .bd import ahora, con, ej, j, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS kpi_okr (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, tipo TEXT NOT NULL DEFAULT 'kpi', nombre TEXT NOT NULL,
  descripcion TEXT DEFAULT '', fuente TEXT DEFAULT 'manual', metrica TEXT DEFAULT '', formula TEXT DEFAULT '', unidad TEXT DEFAULT '',
  mejor TEXT DEFAULT 'alto', meta REAL, umbral REAL, frecuencia TEXT DEFAULT 'mensual', responsable TEXT DEFAULT '', pilar TEXT DEFAULT '',
  objetivo TEXT DEFAULT '', resultados TEXT DEFAULT '[]', valor_manual REAL, activo INTEGER DEFAULT 1, predefinido INTEGER DEFAULT 0,
  orden INTEGER DEFAULT 0, creado_por TEXT DEFAULT '', creado TEXT, actualizado_por TEXT DEFAULT '', actualizado TEXT);
CREATE INDEX IF NOT EXISTS kpi_okr_cliente ON kpi_okr(cliente_id, tipo, orden);
CREATE TABLE IF NOT EXISTS kpi_okr_valores (kpi_id INTEGER NOT NULL, dia TEXT NOT NULL, valor REAL, autor TEXT DEFAULT '', PRIMARY KEY (kpi_id, dia));
CREATE TABLE IF NOT EXISTS kpi_okr_sembrado (cliente_id INTEGER PRIMARY KEY, cuando TEXT);
"""
FRECUENCIAS = ["diaria", "semanal", "mensual", "trimestral", "semestral", "anual"]
PILARES = ["Disponibilidad", "Rendimiento", "Seguridad", "Recuperación", "Costo", "Excelencia operativa", "Operación", "Negocio"]
_listo = set()


def _tabla():
    from .bd import RUTA
    if RUTA in _listo:
        return
    con().executescript(ESQUEMA)
    con().commit()
    _listo.add(RUTA)


# ── Catálogo de métricas que FaceOps mide (clave → nombre, unidad, hacia dónde mejora, grupo) ──
def metricas():
    from .radar import CRITERIOS_EQ, CRITERIOS_SEG
    from .seguridad_mapa import FUNCIONES
    m = {
        "tickets_30d": ("Tickets de los últimos 30 días", "", "alto", "Tickets"),
        "tickets_exito_pct": ("Tickets exitosos", "%", "alto", "Tickets"),
        "tickets_ejecucion_min": ("Tiempo medio de ejecución de tickets", "min", "bajo", "Tickets"),
        "tickets_con_externo_pct": ("Tickets reflejados en la herramienta del cliente", "%", "alto", "Tickets"),
        "aprobaciones_pendientes": ("Aprobaciones pendientes", "", "bajo", "Aprobaciones"),
        "aprobacion_espera_h": ("Espera media de aprobación", "h", "bajo", "Aprobaciones"),
        "ahorro_h_30d": ("Horas manuales ahorradas (30 días)", "h", "alto", "Productividad"),
        "factor_beneficio": ("Factor de beneficio (automatizado ÷ manual)", "", "bajo", "Productividad"),
        "autos_total": ("Automatizaciones del catálogo", "", "alto", "Automatizaciones"),
        "autos_certificadas_pct": ("Automatizaciones certificadas (plan de prueba aprobado)", "%", "alto", "Automatizaciones"),
        "autos_con_aprobacion_pct": ("Cambios de escritura con aprobación", "%", "alto", "Automatizaciones"),
        "equipos": ("Equipos en el inventario", "", "alto", "Inventario"),
        "alertas_cobertura_pct": ("Objetos con alertas configuradas", "%", "alto", "Alertas"),
        "inventario_reciente_pct": ("Inventario recolectado en los últimos 30 días", "%", "alto", "Inventario"),
        "equipos_con_segmento_pct": ("Equipos asociados a un segmento de negocio", "%", "alto", "Inventario"),
        "segmentos_rto_pct": ("Segmentos con RTO y RPO definidos", "%", "alto", "Recuperación"),
        "hallazgos_altos": ("Hallazgos graves abiertos", "", "bajo", "Hallazgos"),
        "kb_problemas": ("Errores conocidos en la base de conocimiento", "", "alto", "Incidentes"),
        "mttr_incidentes_h": ("MTTR de incidentes (90 días)", "h", "bajo", "Incidentes"),
        "incidentes_abiertos": ("Incidentes abiertos", "", "bajo", "Incidentes"),
        "radar_eq_pct": ("Salud promedio del parque (equipos)", "%", "alto", "Radar de salud"),
        "radar_seg_pct": ("Salud promedio de los segmentos en producción", "%", "alto", "Radar de salud"),
        "flujos_tls_pct": ("Flujos con TLS", "%", "alto", "Seguridad"),
        "nist_pct": ("Cumplimiento NIST CSF 2.0 (perfil actual)", "%", "alto", "Seguridad"),
        "nist_nivel": ("Nivel de madurez NIST CSF 2.0", "nivel", "alto", "Seguridad"),
    }
    for p in ("Disponibilidad", "Rendimiento", "Seguridad", "Recuperación", "Costo", "Excelencia operativa"):
        m[f"radar_eq_{p.lower()}"] = (f"{p} del parque (equipos)", "%", "alto", "Radar de salud")
        m[f"radar_seg_{p.lower()}"] = (f"{p} de los segmentos", "%", "alto", "Radar de salud")
    for c in CRITERIOS_EQ:
        m[f"crit_{c[0]}"] = (f"Equipos: {c[2].lower()}", "%", "alto", f"Criterio · {c[1]}")
    for c in CRITERIOS_SEG:
        m[f"crit_{c[0]}"] = (f"Segmentos: {c[2].lower()}", "%", "alto", f"Criterio · {c[1]}")
    for fk, fn in FUNCIONES:
        m[f"nist_{fk}_nivel"] = (f"Nivel NIST · {fn} ({fk})", "nivel", "alto", "Seguridad")
    return m


def extras(cliente, eqs, hs, v):
    """Métricas que no están en indicadores.calcular: TLS y NIST (mapa de seguridad) y MTTR (incidentes)."""
    out = {}
    try:
        from . import seguridad_mapa as SM
        from .bd import j as _j
        from .vistas import seccion_eq
        nube = {e["id"] for e in eqs if seccion_eq(e) == "nubes"}
        m = SM.calcular(cliente, [dict(e, _datos=_j(e["datos"], {})) for e in eqs], hs, nube)
        out["flujos_tls_pct"] = round(100 * (m["n_flujos"] - m["n_sin_tls"]) / m["n_flujos"]) if m["n_flujos"] else None
        try:
            from .politicas_seg import tablero
            pols = tablero(cliente["id"], eqs)[0]
        except Exception:  # noqa: BLE001
            pols = None
        fs = SM.nist(cliente, m, v, pols)
        if fs:
            out["nist_pct"] = round(sum(f["pct"] for f in fs) / len(fs))
            out["nist_nivel"] = SM.nivel(out["nist_pct"])
            for f in fs:
                out[f"nist_{f['id']}_nivel"] = f["nivel"]
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import kb
        if kb.disponible():
            r = kb.q1("SELECT avg(EXTRACT(EPOCH FROM (resuelto - creado))) s, count(*) FILTER (WHERE resuelto IS NULL) abiertos "
                      "FROM incidentes WHERE cliente_id=%s AND creado >= now() - interval '90 days'", cliente["id"])
            if r:
                out["mttr_incidentes_h"] = round(float(r["s"]) / 3600, 1) if r.get("s") is not None else None
                out["incidentes_abiertos"] = int(r.get("abiertos") or 0)
    except Exception:  # noqa: BLE001
        pass
    return out


# ── Siembra de predefinidos (una vez por cliente) ──
EXTRA_KPI = [  # lo que FaceOps mide y no estaba en kpi-okr.yml
    ("Disponibilidad de los segmentos", "radar_seg_disponibilidad", 99, 90, "Disponibilidad", "diaria"),
    ("Máquinas con respaldo continuo", "crit_R1", 100, 80, "Recuperación", "semanal"),
    ("Flujos con TLS", "flujos_tls_pct", 100, 80, "Seguridad", "semanal"),
    ("Nivel de madurez NIST CSF 2.0", "nist_nivel", 3, 2, "Seguridad", "trimestral"),
    ("MTTR de incidentes", "mttr_incidentes_h", 4, 8, "Excelencia operativa", "mensual"),
]
OKR_SEGURIDAD = {"nombre": "Subir la madurez de Seguridad a N3 en el trimestre", "pilar": "Seguridad", "frecuencia": "trimestral",
                 "resultados": [
                     {"kr": "Nivel NIST CSF 2.0 en N3 o más", "fuente": "metrica", "metrica": "nist_nivel", "meta": 3, "unidad": "nivel"},
                     {"kr": "Proteger (PR) en N3 o más", "fuente": "metrica", "metrica": "nist_PR_nivel", "meta": 3, "unidad": "nivel"},
                     {"kr": "90 % de los flujos con TLS", "fuente": "metrica", "metrica": "flujos_tls_pct", "meta": 90, "unidad": "%"},
                     {"kr": "90 % de los equipos sin hallazgos de seguridad", "fuente": "metrica", "metrica": "crit_S1", "meta": 90, "unidad": "%"}]}


def _umbral(meta, mejor):
    if meta is None:
        return None
    return round(meta * 1.5, 2) if mejor == "bajo" and meta else (None if mejor == "bajo" else round(meta * 0.85, 2))


def sembrar(cliente_id, usuario="faceops"):
    """Siembra los predefinidos la primera vez que el cliente abre la página (después solo los cambia el usuario)."""
    _tabla()
    if q1("SELECT 1 FROM kpi_okr_sembrado WHERE cliente_id=?", cliente_id):
        return False
    import yaml
    from .vistas import RAIZ_DATOS
    try:
        d = yaml.safe_load(open(os.path.join(RAIZ_DATOS, "kpi-okr.yml"), encoding="utf-8")) or {}
    except OSError:
        d = {}
    cat, ts, n = metricas(), ahora(), 0
    vistos = set()

    def ins(**k):
        nonlocal n
        n += 1
        k.setdefault("resultados", "[]")
        cols = ["cliente_id", "predefinido", "creado_por", "creado", "actualizado", "orden"] + list(k)
        ej(f"INSERT INTO kpi_okr({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", cliente_id, 1, usuario, ts, ts, n, *k.values())
    for sec in d.values():
        for x in sec.get("kpis") or []:
            if x.get("valor") in vistos:
                continue
            vistos.add(x.get("valor"))
            c = cat.get(x.get("valor"), ("", x.get("unidad", ""), "alto", ""))
            mejor = x.get("mejor", c[2])
            ins(tipo="kpi", nombre=x["nombre"], descripcion=f"Medido por FaceOps · {c[0] or x['valor']}", fuente="metrica", metrica=x["valor"],
                unidad=x.get("unidad", c[1]), mejor=mejor, meta=x.get("meta"), umbral=_umbral(x.get("meta"), mejor),
                frecuencia="mensual", pilar=sec.get("titulo", ""))
    for nom, met, meta, umb, pilar, frec in EXTRA_KPI:
        if met in vistos:
            continue
        c = cat[met]
        ins(tipo="kpi", nombre=nom, descripcion=f"Medido por FaceOps · {c[0]}", fuente="metrica", metrica=met, unidad=c[1], mejor=c[2],
            meta=meta, umbral=umb, frecuencia=frec, pilar=pilar)
    okrs = [{"nombre": o["objetivo"], "pilar": sec.get("titulo", ""), "frecuencia": "trimestral",
             "resultados": [{"kr": r["kr"], "fuente": "metrica", "metrica": r["valor"], "meta": r.get("meta"), "unidad": r.get("unidad", ""),
                             "mejor": r.get("mejor", "alto")} for r in o.get("resultados") or []]}
            for sec in d.values() for o in sec.get("okrs") or []]
    for o in [OKR_SEGURIDAD] + okrs:
        ins(tipo="okr", nombre=o["nombre"], objetivo=o["nombre"], descripcion="", fuente="", pilar=o["pilar"], frecuencia=o["frecuencia"],
            resultados=json.dumps([dict({"mejor": cat.get(r.get("metrica"), ("", "", "alto"))[2]}, **r) for r in o["resultados"]], ensure_ascii=False))
    ej("INSERT INTO kpi_okr_sembrado(cliente_id, cuando) VALUES (?,?)", cliente_id, ts)
    return True


# ── Cálculo: valor, avance, semáforo y tendencia ──
def _num(x):
    try:
        return None if x in (None, "") else float(str(x).replace(",", "."))
    except ValueError:
        return None


def avance(val, meta, mejor):
    if val is None or meta is None:
        return None
    if mejor == "bajo":
        return 100 if val <= meta else (max(0, round(100 * meta / val)) if val else 100)
    return min(100, round(100 * val / meta)) if meta else 100


def semaforo(val, meta, umbral, mejor):
    if val is None or meta is None:
        return "gris"
    alto = mejor != "bajo"
    if (val >= meta) if alto else (val <= meta):
        return "verde"
    if umbral is not None and ((val >= umbral) if alto else (val <= umbral)):
        return "amarillo"
    return "rojo"


def _hoy():
    return datetime.now(timezone.utc).date().isoformat()


def lista(cliente_id, v, guardar_historial=True):
    """KPI y OKR del cliente con su valor actual, meta, avance, semáforo y tendencia."""
    _tabla()
    cat = metricas()
    filas = [dict(r) for r in q("SELECT * FROM kpi_okr WHERE cliente_id=? ORDER BY tipo, activo DESC, orden, id", cliente_id)]
    hoy = _hoy()
    hist = {}
    ids = [f["id"] for f in filas if f["tipo"] == "kpi"]
    if ids:
        for r in q(f"SELECT kpi_id, dia, valor FROM kpi_okr_valores WHERE kpi_id IN ({','.join('?' * len(ids))}) ORDER BY dia", *ids):
            hist.setdefault(r["kpi_id"], []).append((r["dia"], r["valor"]))
    for f in filas:
        f["resultados"] = j(f.get("resultados"), []) or []
        if f["tipo"] == "kpi":
            val = v.get(f["metrica"]) if f["fuente"] == "metrica" else f.get("valor_manual")
            val = float(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else None
            h = hist.get(f["id"], [])
            if f["fuente"] == "metrica" and val is not None and f["activo"] and guardar_historial and (not h or h[-1][0] != hoy or h[-1][1] != val):
                ej("INSERT INTO kpi_okr_valores(kpi_id, dia, valor, autor) VALUES (?,?,?,?) ON CONFLICT(kpi_id, dia) DO UPDATE SET valor=excluded.valor",
                   f["id"], hoy, val, "faceops")
                h = [x for x in h if x[0] != hoy] + [(hoy, val)]
            f["actual"] = val
            f["avance"] = avance(val, f["meta"], f["mejor"])
            f["semaforo"] = semaforo(val, f["meta"], f["umbral"], f["mejor"]) if f["activo"] else "gris"
            f["historial"] = h[-12:]
            f["tendencia"] = None
            if len(h) >= 2 and h[-1][1] is not None and h[-2][1] is not None:
                d = h[-1][1] - h[-2][1]
                f["tendencia"] = {"delta": round(d, 2), "dir": "sube" if d > 0 else "baja" if d < 0 else "igual",
                                  "mejora": (d > 0) if f["mejor"] != "bajo" else (d < 0), "desde": h[-2][0]}
            f["metrica_nombre"] = cat.get(f["metrica"], (f["metrica"],))[0] if f["fuente"] == "metrica" else ""
        else:
            avs = []
            for r in f["resultados"]:
                val = v.get(r.get("metrica")) if r.get("fuente") == "metrica" else _num(r.get("avance"))
                val = float(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else None
                r["actual"] = val
                r["avance_pct"] = avance(val, _num(r.get("meta")), r.get("mejor", "alto"))
                r["semaforo"] = semaforo(val, _num(r.get("meta")), None, r.get("mejor", "alto"))
                r["metrica_nombre"] = cat.get(r.get("metrica"), (r.get("metrica"),))[0] if r.get("fuente") == "metrica" else ""
                if r["avance_pct"] is not None:
                    avs.append(r["avance_pct"])
            f["avance"] = round(sum(avs) / len(avs)) if avs else None
            f["semaforo"] = "gris" if f["avance"] is None or not f["activo"] else ("verde" if f["avance"] >= 100 else "amarillo" if f["avance"] >= 70 else "rojo")
    return filas


# ── Escritura ──
CAMPOS = ["tipo", "nombre", "descripcion", "fuente", "metrica", "formula", "unidad", "mejor", "meta", "umbral", "frecuencia", "responsable",
          "pilar", "objetivo", "resultados", "valor_manual", "activo"]


def normalizar(f):
    """Valida el formulario (dict) y devuelve los campos a guardar; ValueError con el motivo si algo no cuadra."""
    tipo = f.get("tipo") if f.get("tipo") in ("kpi", "okr") else None
    if not tipo:
        raise ValueError("Tipo inválido: kpi u okr")
    nombre = (f.get("nombre") or "").strip()[:200]
    if not nombre:
        raise ValueError("Falta el nombre")
    d = {"tipo": tipo, "nombre": nombre, "descripcion": (f.get("descripcion") or "").strip()[:1000],
         "frecuencia": f.get("frecuencia") if f.get("frecuencia") in FRECUENCIAS else "mensual",
         "responsable": (f.get("responsable") or "").strip()[:120], "pilar": (f.get("pilar") or "").strip()[:80],
         "activo": 0 if str(f.get("activo", "1")) in ("0", "false", "") else 1, "mejor": "bajo" if f.get("mejor") == "bajo" else "alto"}
    cat = metricas()
    if tipo == "kpi":
        fuente = "metrica" if f.get("fuente") == "metrica" else "manual"
        met = (f.get("metrica") or "").strip()
        if fuente == "metrica" and met not in cat:
            raise ValueError("Elige una métrica de FaceOps de la lista")
        d.update(fuente=fuente, metrica=met if fuente == "metrica" else "", formula=(f.get("formula") or "").strip()[:500],
                 unidad=(f.get("unidad") or "").strip()[:20], meta=_num(f.get("meta")), umbral=_num(f.get("umbral")),
                 valor_manual=_num(f.get("valor_manual")) if fuente == "manual" else None, objetivo="", resultados="[]")
    else:
        rs = f.get("resultados")
        rs = j(rs, []) if isinstance(rs, str) else (rs or [])
        out = []
        for r in rs[:12]:
            kr = (str(r.get("kr") or "")).strip()[:200]
            if not kr:
                continue
            fu = "metrica" if r.get("fuente") == "metrica" else "manual"
            if fu == "metrica" and r.get("metrica") not in cat:
                raise ValueError(f"Resultado clave «{kr}»: elige una métrica de FaceOps")
            out.append({"kr": kr, "fuente": fu, "metrica": r.get("metrica") if fu == "metrica" else "", "meta": _num(r.get("meta")),
                        "avance": _num(r.get("avance")) if fu == "manual" else None, "unidad": str(r.get("unidad") or "")[:20],
                        "mejor": "bajo" if r.get("mejor") == "bajo" else "alto"})
        if not out:
            raise ValueError("Un OKR necesita al menos un resultado clave")
        d.update(objetivo=nombre, resultados=json.dumps(out, ensure_ascii=False), fuente="", metrica="", formula="", unidad="",
                 meta=None, umbral=None, valor_manual=None)
    return d


def obtener(cliente_id, kid):
    _tabla()
    r = q1("SELECT * FROM kpi_okr WHERE id=? AND cliente_id=?", kid, cliente_id)
    return dict(r) if r else None


def guardar(cliente_id, datos, usuario, kid=None):
    """Crea (kid=None) o actualiza; devuelve (id, antes)."""
    _tabla()
    ts = ahora()
    antes = None
    if kid:
        antes = obtener(cliente_id, kid)
        if not antes:
            raise LookupError("No existe")
        ej(f"UPDATE kpi_okr SET {', '.join(k + '=?' for k in datos)}, actualizado_por=?, actualizado=? WHERE id=? AND cliente_id=?",
           *datos.values(), usuario, ts, kid, cliente_id)
    else:
        orden = (q1("SELECT COALESCE(MAX(orden), 0) + 1 o FROM kpi_okr WHERE cliente_id=?", cliente_id) or {}).get("o", 1)
        cols = list(datos) + ["cliente_id", "predefinido", "orden", "creado_por", "creado", "actualizado_por", "actualizado"]
        kid = ej(f"INSERT INTO kpi_okr({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", *datos.values(), cliente_id, 0, orden, usuario, ts, usuario, ts)
    if datos.get("tipo") == "kpi" and datos.get("fuente") == "manual" and datos.get("valor_manual") is not None and \
            (not antes or antes.get("valor_manual") != datos["valor_manual"]):
        ej("INSERT INTO kpi_okr_valores(kpi_id, dia, valor, autor) VALUES (?,?,?,?) ON CONFLICT(kpi_id, dia) DO UPDATE SET valor=excluded.valor, autor=excluded.autor",
           kid, _hoy(), datos["valor_manual"], usuario)
    return kid, antes


def eliminar(cliente_id, kid):
    _tabla()
    antes = obtener(cliente_id, kid)
    if not antes:
        raise LookupError("No existe")
    ej("DELETE FROM kpi_okr_valores WHERE kpi_id=?", kid)
    ej("DELETE FROM kpi_okr WHERE id=? AND cliente_id=?", kid, cliente_id)
    return antes
