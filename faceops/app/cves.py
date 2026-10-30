"""CVE de cada equipo: cubiertos (parche de seguridad instalado) y pendientes (parche disponible sin instalar), según las
erratas de seguridad de Red Hat. Los trae «Seguridad · Revisar CVEs» (AAP); «Aplicar parches de seguridad» los corrige."""
import json

from .bd import ahora, auditar, ej, j, q, q1

REVISAR, PARCHAR = "Seguridad · Revisar CVEs", "Seguridad · Aplicar parches de seguridad"
SEV = {"Critical/Sec.": "crítica", "Important/Sec.": "importante", "Moderate/Sec.": "moderada", "Low/Sec.": "baja"}
ORDEN = {"crítica": 0, "importante": 1, "moderada": 2, "baja": 3, "": 4}
ESQUEMA = """
CREATE TABLE IF NOT EXISTS cves_equipo (
  cliente_id INTEGER NOT NULL, equipo_id INTEGER NOT NULL, cve TEXT NOT NULL, severidad TEXT DEFAULT '', paquete TEXT DEFAULT '',
  estado TEXT NOT NULL, visto TEXT, PRIMARY KEY(cliente_id, equipo_id, cve, estado));
CREATE TABLE IF NOT EXISTS cves_revision (
  cliente_id INTEGER NOT NULL, equipo_id INTEGER NOT NULL, repos INTEGER, pendientes INTEGER, cubiertos INTEGER, cuando TEXT, ticket_id INTEGER,
  PRIMARY KEY(cliente_id, equipo_id));
"""


def iniciar():
    from .bd import con
    con().executescript(ESQUEMA)


def registrar(aap, cliente, t):
    """Resultado de «Revisar CVEs»: reemplaza lo conocido de cada equipo revisado."""
    art = (aap.get(f"jobs/{t['aap_job_id']}/").get("artifacts") or {}) if t["aap_job_tipo"] == "job" else {}
    res, lineas = art.get("faceops_cves") or {}, []
    for n, r in res.items():
        e = q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], n)
        if not e:
            continue
        ej("DELETE FROM cves_equipo WHERE cliente_id=? AND equipo_id=?", cliente["id"], e["id"])
        for estado, filas in (("pendiente", r.get("pendientes") or []), ("cubierto", r.get("cubiertos") or [])):
            for f in filas:
                cve, sev, pkg = (f.split("|") + ["", ""])[:3]
                ej("INSERT OR IGNORE INTO cves_equipo(cliente_id, equipo_id, cve, severidad, paquete, estado, visto) VALUES (?,?,?,?,?,?,?)",
                   cliente["id"], e["id"], cve, SEV.get(sev, sev), pkg, estado, ahora())
        ej("INSERT INTO cves_revision(cliente_id, equipo_id, repos, pendientes, cubiertos, cuando, ticket_id) VALUES (?,?,?,?,?,?,?) "
           "ON CONFLICT(cliente_id, equipo_id) DO UPDATE SET repos=excluded.repos, pendientes=excluded.pendientes, cubiertos=excluded.cubiertos, "
           "cuando=excluded.cuando, ticket_id=excluded.ticket_id",
           cliente["id"], e["id"], int(bool(r.get("repos"))), len({x.split("|")[0] for x in r.get("pendientes") or []}),
           len({x.split("|")[0] for x in r.get("cubiertos") or []}), ahora(), t["id"])
        lineas.append(f"{n}: " + (f"{len(r.get('pendientes') or [])} pendientes · {len(r.get('cubiertos') or [])} cubiertos" if r.get("repos")
                                  else "sin repositorios Red Hat (no se puede evaluar)"))
    return "CVE: " + " · ".join(lineas)


def por_equipo(cliente_id, eqs):
    """{equipo_id: {revisado, repos, pendientes, cubiertos, criticos, importantes, cuando, top: [...]}}"""
    rev = {r["equipo_id"]: r for r in q("SELECT * FROM cves_revision WHERE cliente_id=?", cliente_id)}
    pend = {}
    for r in q("SELECT equipo_id, cve, severidad, paquete FROM cves_equipo WHERE cliente_id=? AND estado='pendiente'", cliente_id):
        pend.setdefault(r["equipo_id"], {}).setdefault(r["cve"], r)
    out = {}
    for e in eqs:
        r = rev.get(e["id"])
        ps = sorted(pend.get(e["id"], {}).values(), key=lambda x: (ORDEN.get(x["severidad"], 9), x["cve"]))
        out[e["id"]] = {"revisado": bool(r), "repos": bool(r and r["repos"]), "pendientes": len(ps), "cubiertos": r["cubiertos"] if r else 0,
                        "criticos": len([x for x in ps if x["severidad"] == "crítica"]), "importantes": len([x for x in ps if x["severidad"] == "importante"]),
                        "cuando": r["cuando"] if r else None, "top": ps[:8]}
    return out


def resumen(cliente_id, eqs, estado="pendiente"):
    """Por CVE: severidad, paquete y equipos afectados (pendiente) o protegidos (cubierto)."""
    nombres = {e["id"]: e for e in eqs}
    por = {}
    for r in q("SELECT * FROM cves_equipo WHERE cliente_id=? AND estado=?", cliente_id, estado):
        if r["equipo_id"] not in nombres:
            continue
        x = por.setdefault(r["cve"], {"cve": r["cve"], "severidad": r["severidad"], "paquetes": set(), "equipos": []})
        x["paquetes"].add(r["paquete"].rsplit("-", 2)[0] if r["paquete"] else "")
        if nombres[r["equipo_id"]] not in x["equipos"]:
            x["equipos"].append(nombres[r["equipo_id"]])
    return sorted(por.values(), key=lambda x: (ORDEN.get(x["severidad"], 9), -len(x["equipos"]), x["cve"]))


def solicitar(usuario, cliente, nombre_aap, equipos, params, motivo):
    """Un ticket por inventario de AAP (cada uno con su credencial)."""
    from .aap import AAP
    from .operacion import acceso_equipo, ejecutar, numero_ticket, puede_ejecutar
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], nombre_aap)
    if not a:
        raise PermissionError(f"Falta «{nombre_aap}» en el catálogo (sincroniza con AAP)")
    ok, por_que = puede_ejecutar(usuario, cliente["id"], a)
    if not ok:
        raise PermissionError(por_que)
    aap = AAP.de_cliente(cliente)
    grupos = {}
    from .aap import ErrorAAP
    for e in equipos:
        try:
            grupos.setdefault(acceso_equipo(aap, cliente, e)[1], []).append(e)
        except ErrorAAP:   # sin credencial conocida: se omite
            continue
    out = []
    for es in grupos.values():
        seg = next((s for e in es for s in (e.get("segmentos") or [])), "")
        tid = ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, limite, parametros, motivo, creado, "
                 "valor_min, tarifa, aap_job_tipo, filtros) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 cliente["id"], numero_ticket(cliente), a["id"], usuario["id"], "en_cola", seg, ":".join(e["nombre"] for e in es),
                 json.dumps(params, ensure_ascii=False), motivo, ahora(), a["minutos_manual"], a["tarifa"], a["aap_tipo"],
                 json.dumps({"ambientes": [es[0]["ambiente"]]} if es[0].get("ambiente") else {}))
        t = q1("SELECT * FROM tickets WHERE id=?", tid)
        auditar(usuario["usuario"], cliente["id"], "solicitar", t["numero"], {"automatizacion": a["nombre"], "equipos": [e["nombre"] for e in es]})
        ejecutar(tid)
        out.append(q1("SELECT * FROM tickets WHERE id=?", tid))
    return out
