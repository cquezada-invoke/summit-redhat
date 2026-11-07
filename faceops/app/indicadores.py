"""Indicadores medidos con datos reales de FaceOps. Los usan los KPI y OKR de Monitoreo y la evaluación ITIL.
Cada valor sale de la base (tickets, equipos, segmentos, alertas, pruebas, conocimiento); si no hay datos queda None."""
from datetime import datetime, timedelta, timezone

from .bd import j, q, q1


def _pct(a, b):
    return round(100 * a / b) if b else None


def calcular(cliente_id, eqs=None, hallazgos=None, radar_eq=None, radar_seg=None):
    """{clave: valor} con todo lo que FaceOps sabe medir hoy."""
    v = {}
    desde = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    ts = q("SELECT estado, creado, dur_ejecucion_s, dur_aprobacion_s, ahorro, factor, externo_ref, aprobado_por FROM tickets WHERE cliente_id=? AND creado>=?", cliente_id, desde)
    fin = [t for t in ts if t["estado"] in ("exitoso", "fallido")]
    v["tickets_30d"] = len(ts)
    v["tickets_exito_pct"] = _pct(sum(1 for t in fin if t["estado"] == "exitoso"), len(fin))
    durs = [t["dur_ejecucion_s"] for t in fin if t["dur_ejecucion_s"]]
    v["tickets_ejecucion_min"] = round(sum(durs) / len(durs) / 60, 1) if durs else None
    apr = [t["dur_aprobacion_s"] for t in ts if t["dur_aprobacion_s"]]
    v["aprobacion_espera_h"] = round(sum(apr) / len(apr) / 3600, 1) if apr else None
    v["aprobaciones_pendientes"] = (q1("SELECT COUNT(*) n FROM tickets WHERE cliente_id=? AND estado='pendiente_aprobacion'", cliente_id) or {}).get("n", 0)
    v["ahorro_h_30d"] = round(sum((t["ahorro"] or 0) for t in ts) / 60, 1) if any(t["ahorro"] for t in ts) else None
    fs = [t["factor"] for t in ts if t["factor"]]
    v["factor_beneficio"] = round(sum(fs) / len(fs), 3) if fs else None
    v["tickets_con_externo_pct"] = _pct(sum(1 for t in ts if t["externo_ref"]), len(ts))
    # automatizaciones y regla de prueba
    autos = q("SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=1", cliente_id)
    from .pruebas import no_certificadas
    bloq = no_certificadas(cliente_id)
    v["autos_total"] = len(autos)
    v["autos_certificadas_pct"] = _pct(sum(1 for a in autos if a["id"] not in bloq), len(autos))
    v["autos_con_aprobacion_pct"] = _pct(sum(1 for a in autos if a["tipo"] == "escritura" and a["requiere_aprobacion"]), sum(1 for a in autos if a["tipo"] == "escritura"))
    # inventario (CMDB) y alertas por objeto
    eqs = eqs if eqs is not None else q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cliente_id)
    v["equipos"] = len(eqs)
    from .alertas_objeto import aplicadas, tipo_de
    vig = [e for e in eqs if tipo_de(e)]
    v["alertas_cobertura_pct"] = _pct(sum(1 for e in vig if (aplicadas(e).get("alertas"))), len(vig))
    v["inventario_reciente_pct"] = _pct(sum(1 for e in eqs if (e.get("ultima_recoleccion") or "") >= desde), len(eqs))
    v["equipos_con_segmento_pct"] = None
    try:
        from .inventario import segmentos_de
        segs = q("SELECT * FROM segmentos WHERE cliente_id=?", cliente_id)
        v["equipos_con_segmento_pct"] = _pct(sum(1 for e in eqs if segmentos_de(e, segs)), len(eqs))
        v["segmentos_rto_pct"] = _pct(sum(1 for s in segs if (j(s.get("ficha"), {}) or {}).get("rto") and (j(s.get("ficha"), {}) or {}).get("rpo")),
                                      sum(1 for s in segs if s.get("grupo") == "aplicaciones") or len(segs))
    except Exception:  # noqa: BLE001
        v["segmentos_rto_pct"] = None
    v["hallazgos_altos"] = sum(1 for h in (hallazgos or []) if str(h.get("severidad")).lower() in ("alta", "critica", "crítica"))
    # conocimiento (errores conocidos)
    try:
        from . import kb
        v["kb_problemas"] = len(kb.q("SELECT id FROM problemas") or [])
    except Exception:  # noqa: BLE001
        v["kb_problemas"] = None
    # radar
    for nombre, r in (("eq", radar_eq), ("seg", radar_seg)):
        if not r:
            continue
        v[f"radar_{nombre}_pct"] = r.get("pct")
        for p in r["principios"]:
            v[f"radar_{nombre}_{p['nombre'].lower()}"] = p["pct"]
            for c in p["criterios"]:
                v[f"crit_{c['id']}"] = c["pct"]
    return v
