"""Madurez operativa del parque: por pilar de salud (radar) y por cada ítem del menú de la izquierda (tabla del panel).
Cada pilar se mide con prácticas verificables por equipo; el % es la parte de las prácticas que hoy se cumplen.
La meta de cada pilar (datos/madurez.yml) es el nivel que el cliente necesita para ser competitivo, no el 100 %."""
import os
from datetime import datetime, timedelta, timezone

import yaml

from .bd import q

PILARES = ["Disponibilidad", "Hallazgos", "Rendimiento", "Seguridad", "Recuperación"]
EXPORTERS = {"node-exporter", "windows-exporter"}
BD = {"postgres", "sqlserver", "mysql", "oracle", "mongodb", "mariadb"}
PLAT = {"jboss", "keycloak", "redis", "vault", "kafka", "activemq", "jenkins", "satellite", "aap", "automation-hub", "gitea",
        "red-hat-idm", "389-directory-server", "active-directory", "certificate-system", "iis", "nginx"}


REMEDIOS = {   # práctica → (cómo abordarlo, búsqueda en el catálogo de acciones)
    "encendido": ("Encender la máquina o, si ya no presta servicio, retirarla del inventario para que no cuente como caída.", "encender"),
    "monitoreado (agente de métricas)": ("Instalar el agente de métricas (Linux o Windows) para ver su estado y recibir alertas.", "agente de monitoreo"),
    "sin hallazgos altos": ("Atender primero los hallazgos de severidad alta: cada uno trae su acción sugerida.", "con hallazgos"),
    "sin hallazgos abiertos": ("Cerrar los hallazgos pendientes con la acción sugerida en la ficha de cada equipo.", "con hallazgos"),
    "inventario al día (7 días)": ("Volver a recolectar el inventario (ideal: programarlo cada día).", "inventariar"),
    "métricas de uso": ("Con el agente de métricas se mide CPU, memoria y disco reales.", "agente de monitoreo"),
    "bien dimensionado (sin recursos ociosos)": ("Ajustar CPU y memoria al uso real, o apagar lo que no se usa.", "ajustar recursos"),
    "CVE revisados": ("Revisar los CVE del servidor contra las erratas del fabricante.", "revisar cve"),
    "sin CVE críticos pendientes": ("Aplicar los parches de seguridad pendientes (críticos e importantes primero).", "parches de seguridad"),
    "cumple las políticas": ("Aplicar la política de seguridad que falta; el hallazgo dice cuál.", "política de seguridad"),
    "configuración respaldada en Git": ("Activar el respaldo continuo de la configuración en Git.", "respaldo"),
    "punto de restauración": ("Crear un punto de restauración antes de cambios y de forma periódica.", "punto de restauración"),
}


def pilares_info():
    try:
        return yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "pilares.yml"), encoding="utf-8")) or {}
    except OSError:
        return {}


def metas():
    try:
        d = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "madurez.yml"), encoding="utf-8")) or {}
    except OSError:
        d = {}
    m = d.get("metas") or {}
    return {p: float(m.get(p, 75)) for p in PILARES}


def _practicas(cliente_id, eqs, hallazgos):
    """Por equipo: {pilar: [(práctica, cumple)]}."""
    sev = {}
    tipos = {}
    for h in hallazgos:
        sev.setdefault(h["equipo_id"], set()).add(h.get("severidad"))
        tipos.setdefault(h["equipo_id"], set()).add(h.get("tipo"))
    rev = {r["equipo_id"] for r in q("SELECT equipo_id FROM cves_revision WHERE cliente_id=?", cliente_id)}
    crit = {r["equipo_id"] for r in q("SELECT DISTINCT equipo_id FROM cves_equipo WHERE cliente_id=? AND estado='pendiente' "
                                      "AND lower(severidad) IN ('critical','crítica','critica','important','importante','alta')", cliente_id)}
    puntos = {r["equipo_id"] for r in q("SELECT DISTINCT equipo_id FROM puntos_restauracion WHERE cliente_id=?", cliente_id)}
    hace7 = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    out = {}
    for e in eqs:
        sk = set(e.get("skills") or [])
        hs_, ts_ = sev.get(e["id"], set()), tipos.get(e["id"], set())
        out[e["id"]] = {
            "Disponibilidad": [("encendido", e.get("energia") != "apagado"), ("monitoreado (agente de métricas)", bool(sk & EXPORTERS))],
            "Hallazgos": [("sin hallazgos altos", "alta" not in hs_), ("sin hallazgos abiertos", not hs_),
                          ("inventario al día (7 días)", (e.get("ultima_recoleccion") or "") >= hace7)],
            "Rendimiento": [("métricas de uso", bool(sk & EXPORTERS)), ("bien dimensionado (sin recursos ociosos)", "sin_uso" not in ts_)],
            "Seguridad": [("CVE revisados", e["id"] in rev), ("sin CVE críticos pendientes", e["id"] in rev and e["id"] not in crit),
                          ("cumple las políticas", "politica" not in ts_)],
            "Recuperación": [("configuración respaldada en Git", "respaldo-git" in sk), ("punto de restauración", e["id"] in puntos)],
        }
    return out


def _pct(eids, pr):
    """% de cumplimiento por pilar sobre un conjunto de equipos."""
    res = {}
    for p in PILARES:
        tot = sum(len(pr[i][p]) for i in eids)
        res[p] = round(100 * sum(1 for i in eids for _, ok in pr[i][p] if ok) / tot) if tot else None
    return res


def dominios(eqs):
    """Equipos de cada ítem del menú de la izquierda (los que tienen equipos que medir)."""
    from .vistas import clase_eq, grupo_periferico
    nube = lambda e: (e.get("proveedor") or "").lower() in ("gcp", "aws", "azure", "nube") or "cloud" in (e.get("ubicacion") or "").lower()
    defs = [("infraestructura", "Infraestructura", lambda e: True),
            ("servidores", "Servidores", lambda e: clase_eq(e) == "maquinas"),
            ("redes", "Redes", lambda e: clase_eq(e) == "redes"),
            ("storage", "Storage", lambda e: clase_eq(e) == "almacenamiento"),
            ("so", "Sistemas operativos", lambda e: clase_eq(e) == "maquinas" and bool(e.get("so"))),
            ("bd", "Bases de datos", lambda e: bool(set(e.get("skills") or []) & BD)),
            ("plataformas", "Plataformas", lambda e: bool(set(e.get("skills") or []) & PLAT)),
            ("contenedores", "Contenedores", lambda e: grupo_periferico(e) == "clusters" or "openshift" in (e.get("skills") or [])),
            ("hipervisores", "Hipervisores", lambda e: "kvm" in (e.get("skills") or []) or (e.get("categoria") or "").lower() == "hipervisor"),
            ("nube", "Nubes", nube),
            ("aplicaciones", "Aplicaciones", lambda e: bool(e.get("segmentos")))]
    return [(k, n, [e["id"] for e in eqs if f(e)]) for k, n, f in defs]


def madurez(cliente_id, eqs, hallazgos):
    pr = _practicas(cliente_id, eqs, hallazgos)
    meta = metas()
    todos = [e["id"] for e in eqs]
    actual = {p: (v if v is not None else 0) for p, v in _pct(todos, pr).items()}
    filas = []
    for k, n, ids in dominios(eqs):
        if not ids:
            filas.append({"clave": k, "nombre": n, "equipos": 0, "pct": None, "meta": None, "pilares": {}})
            continue
        pp = _pct(ids, pr)
        vals = [v for v in pp.values() if v is not None]
        filas.append({"clave": k, "nombre": n, "equipos": len(ids), "pilares": pp,
                      "pct": round(sum(vals) / len(vals)) if vals else None, "meta": round(sum(meta.values()) / len(meta))})
    nombres = {e["id"]: e["nombre"] for e in eqs}
    detalle = {}
    for p in PILARES:
        ps = []
        for nom, _ in (pr[todos[0]][p] if todos else []):
            faltan = [i for i in todos if not dict(pr[i][p])[nom]]
            como, buscar = REMEDIOS.get(nom, ("", ""))
            ps.append({"practica": nom, "cumple": len(todos) - len(faltan), "de": len(todos), "como": como, "buscar": buscar,
                       "faltan": sorted(({"id": i, "nombre": nombres[i]} for i in faltan), key=lambda x: x["nombre"])})
        total = sum(x["de"] for x in ps)
        hechas = sum(x["cumple"] for x in ps)
        objetivo = -(-int(meta[p] * total) // 100)   # correcciones para llegar a la meta (redondeo hacia arriba)
        detalle[p] = {"practicas": sorted(ps, key=lambda x: -len(x["faltan"])), "total": total, "hechas": hechas,
                      "por_hacer": max(0, objetivo - hechas), "brecha": max(0, round(meta[p] - actual[p]))}
    return {"actual": actual, "meta": meta, "filas": filas, "detalle": detalle,
            "pct": round(sum(actual.values()) / len(actual)), "meta_pct": round(sum(meta.values()) / len(meta))}


def radar_svg(actual, meta, tam=760, enlace=None):
    """Radar de madurez: anillos 25/50/75/100 % (100 % = máximo de la escala), contorno ámbar punteado = meta competitiva,
    área azul = nivel de hoy; la diferencia entre ambos es la brecha."""
    import math
    from markupsafe import Markup, escape
    W, H = 620, 420
    cx, cy, R = W / 2, 215, 150
    def pt(i, v):
        a = math.radians(-90 + 72 * i)
        r = R * max(0, min(100, v)) / 100
        return cx + r * math.cos(a), cy + r * math.sin(a)
    pol = lambda vals: " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, v) for i, v in enumerate(vals)))
    act = [actual.get(p, 0) for p in PILARES]
    met = [meta.get(p, 100) for p in PILARES]
    o = [f'<svg class="estrella" viewBox="0 0 {W} {H}" width="{tam}" style="max-width:100%;height:auto" role="img" aria-label="Radar de madurez">']
    for f in (1, .75, .5, .25):
        o.append(f'<polygon points="{pol([100 * f] * 5)}" fill="none" stroke="var(--line)" stroke-width="{1.4 if f == 1 else 1}"/>')
        x, y = pt(0, 100 * f)
        o.append(f'<text x="{x + 4:.1f}" y="{y + 11:.1f}" font-size="10" fill="var(--muted)">{int(100 * f)} %{" · máximo" if f == 1 else ""}</text>')
    for i in range(5):
        x, y = pt(i, 100)
        o.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="var(--line)" stroke-width="1"/>')
    a0, a1 = (f'<a href="{escape(enlace)}"><title>Ver qué falta para llegar a la meta y cómo abordarlo</title>', '</a>') if enlace else ("", "")
    o.append(f'{a0}<polygon points="{pol(met)}" fill="#fac775" fill-opacity=".45" stroke="#ba7517" stroke-width="2" stroke-dasharray="7 4" style="cursor:pointer"/>{a1}')
    o.append(f'{a0}<polygon points="{pol(act)}" fill="#cfe3f7" fill-opacity=".9" stroke="#185fa5" stroke-width="2.4" style="cursor:pointer"/>{a1}')
    for i in range(5):
        for vals, col in ((met, "#ba7517"), (act, "#185fa5")):
            x, y = pt(i, vals[i])
            o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{col}"/>')
        a = math.radians(-90 + 72 * i)
        x, y = cx + (R + 26) * math.cos(a), cy + (R + 26) * math.sin(a)
        anc = "middle" if abs(math.cos(a)) < .3 else ("start" if math.cos(a) > 0 else "end")
        dy = -18 if i == 0 else (8 if i in (2, 3) else 0)
        b0, b1 = (f'<a href="{escape(enlace)}#{escape(PILARES[i])}">', '</a>') if enlace else ("", "")
        o.append(f'{b0}<text x="{x:.1f}" y="{y + dy:.1f}" text-anchor="{anc}" font-size="14" font-weight="700" fill="currentColor" style="cursor:pointer">{escape(PILARES[i])}</text>'
                 f'<text x="{x:.1f}" y="{y + dy + 16:.1f}" text-anchor="{anc}" font-size="12" fill="var(--muted)">hoy {act[i]:.0f} % · meta {met[i]:.0f} % · faltan {max(0, met[i] - act[i]):.0f}</text>{b1}')
    o.append("</svg>")
    return Markup("".join(o))
