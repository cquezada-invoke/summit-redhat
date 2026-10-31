"""Certificados: lo que emitió la CA interna (Red Hat Certificate System) y lo que está en uso en cada máquina.
FaceOps NO se conecta a la CA ni a las máquinas: todo sale de AAP.
· «Certificados · Inventario» (AAP, cada hora y tras cada renovación): emitidos por la CA + en uso en las máquinas
· trabajos de la skill «certificados»: cuándo se emitió o renovó cada uno
La renovación automática la decide Event-Driven Ansible (rulebooks/certificados.yml). Cache de 60 s."""
import os
import time
from datetime import datetime, timezone

from .aap import AAP, ErrorAAP

INVENTARIO = "Certificados · Inventario"
CA_PUBLICA = os.environ.get("FACEOPS_CA_PUBLICA", "https://pki.34.176.42.56.sslip.io")
UMBRAL = 30
JTS = ("Certificados · Emitir para servicio", "Certificados · Renovar por vencer")
_cache = {}


def _k(d):
    return {k.lower(): v for k, v in d.items()}


def _fecha(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)) or str(v).isdigit():
        return datetime.fromtimestamp(int(v) / (1000 if int(v) > 10**11 else 1), timezone.utc)
    for f in ("%a %b %d %H:%M:%S %Z %Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            d = datetime.strptime(str(v), f)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _serie(s):
    s = str(s or "").lower().removeprefix("0x").lstrip("0")
    return s or "0"


def _dn(dn):
    out = {}
    for p in str(dn or "").split(","):
        if "=" in p:
            k, v = p.split("=", 1)
            out.setdefault(k.strip().upper(), v.strip())
    return out


def _tipo(dn):
    d = _dn(dn)
    cn = d.get("CN", "")
    if "UID" in d:
        return "usuario"
    if any(x in cn for x in ("Signing Certificate", "Subsystem Certificate", "Audit", "OCSP", "Transport", "Storage")):
        return "sistema"
    if cn.endswith(".internal") or cn.startswith("demo-ansible-summit"):
        return "sistema"  # certificado TLS de la propia CA
    return "servidor"


def _inventario(aap):
    """Último resultado de «Certificados · Inventario» en AAP: (datos, trabajo)."""
    jt = (aap.get("job_templates/", name=INVENTARIO).get("results") or [None])[0]
    if not jt:
        raise ErrorAAP(f"No existe la plantilla «{INVENTARIO}» en AAP")
    for jb in aap.get("jobs/", job_template=jt["id"], status="successful", order_by="-finished", page_size=5).get("results", []):
        d = (aap.get(f"jobs/{jb['id']}/").get("artifacts") or {}).get("faceops_cert_inventario")
        if d:
            return d, jb, jt
    return {}, None, jt


def _gestionados(aap):
    """{serie: {equipo, servicio, cuando, job}} y {(equipo, servicio): serie actual} desde los trabajos de la skill."""
    por_serie, actual = {}, {}
    for nombre in JTS:
        jt = (aap.get("job_templates/", name=nombre).get("results") or [None])[0]
        if not jt:
            continue
        for jb in aap.get("jobs/", job_template=jt["id"], status="successful", order_by="finished", page_size=200).get("results", []):
            art = (aap.get(f"jobs/{jb['id']}/").get("artifacts") or {}).get("faceops_certificados") or {}
            for eq, d in art.items():
                for svc, c in (d.get("renovados") or {}).items():
                    s = _serie(c.get("serie"))
                    por_serie[s] = {"equipo": eq, "servicio": svc, "cuando": jb.get("finished"), "job": jb["id"],
                                    "accion": "emisión" if nombre.startswith("Certificados · Emitir") else "renovación"}
                    actual[(eq, svc)] = s
    prog = None
    jt = (aap.get("job_templates/", name=JTS[1]).get("results") or [None])[0]
    if jt:
        sch = [s for s in aap.get("schedules/", unified_job_template=jt["id"]).get("results", []) if s.get("enabled")]
        prog = min((s.get("next_run") for s in sch if s.get("next_run")), default=None)
    return por_serie, actual, prog


def leer(cliente, equipos):
    k = cliente["id"]
    if k in _cache and time.time() - _cache[k][0] < 60:
        return _cache[k][1]
    errores, certs, inv, jb, jt, uso = [], [], {}, None, None, {}
    por_serie, actual, prog, aap_url = {}, {}, None, ""
    try:
        aap = AAP.de_cliente(cliente)
        aap_url = aap.publica
        inv, jb, jt = _inventario(aap)
        certs = [_k(x) for x in inv.get("emitidos", [])]
        uso = inv.get("en_uso") or {}
        por_serie, actual, _ = _gestionados(aap)
        sch = [s_ for s_ in aap.get("schedules/", unified_job_template=jt["id"]).get("results", []) if s_.get("enabled")] if jt else []
        prog = min((s_.get("next_run") for s_ in sch if s_.get("next_run")), default=None)
        if not jb:
            errores.append(f"«{INVENTARIO}» aún no tiene una ejecución exitosa en AAP")
    except ErrorAAP as e:
        errores.append(f"AAP: {str(e)[:160]}")
    # en uso según las máquinas: serie → (equipo, servicio, entrega en el puerto)
    en_uso = {}
    for eq, lst in uso.items():
        for u in lst or []:
            en_uso[_serie(u.get("serie"))] = dict(u, equipo=eq)
            actual.setdefault((eq, u.get("servicio")), _serie(u.get("serie")))
    eqs = {e["nombre"]: e for e in equipos}
    ahora = datetime.now(timezone.utc)
    filas = []
    for c in certs:
        dn = c.get("subjectdn", "")
        cn = _dn(dn).get("CN") or _dn(dn).get("UID") or dn
        s = _serie(c.get("id"))
        g = por_serie.get(s)
        u = en_uso.get(s)
        if u and not g:
            g = {"equipo": u["equipo"], "servicio": u["servicio"], "cuando": None, "job": None, "accion": "registro"}
        eq_nombre = g["equipo"] if g else cn.split(".")[0]
        e = eqs.get(eq_nombre)
        fin = _fecha(c.get("notvalidafter"))
        ini = _fecha(c.get("notvalidbefore")) or _fecha(c.get("issuedon"))
        dias = (fin - ahora).days if fin else None
        emisor = _dn(c.get("issuerdn", "")).get("CN", "")
        filas.append({"serie": s, "serie_txt": str(c.get("id", "")), "cn": cn, "dn": dn, "tipo": "sistema" if cn == emisor else _tipo(dn),
                      "estado_ca": (c.get("status") or "").upper(), "inicio": ini, "fin": fin, "dias": dias,
                      "vida": max(0, min(100, round(100 * (ahora - ini).total_seconds() / max(1, (fin - ini).total_seconds())))) if ini and fin else None,
                      "equipo": e, "equipo_nombre": eq_nombre if (e or g) else "", "servicio": g["servicio"] if g else "",
                      "gestion": g, "emisor": emisor, "uso": u})
    # El vigente de cada nombre/servicio es el más nuevo; los anteriores quedan como «reemplazado»
    grupos = {}
    for f in sorted(filas, key=lambda x: x["inicio"] or ahora, reverse=True):
        clave = (f["equipo_nombre"], f["servicio"]) if f["servicio"] else ("", f["cn"])
        f["reemplazado"] = clave in grupos and f["estado_ca"] == "VALID"
        grupos.setdefault(clave, []).append(f)
    gestionados_cn = {}
    for f in filas:  # el más nuevo gestionado por FaceOps para cada nombre
        if f["servicio"] and not f["reemplazado"] and (f["cn"] not in gestionados_cn or (f["inicio"] or ahora) > (gestionados_cn[f["cn"]]["inicio"] or ahora)):
            gestionados_cn[f["cn"]] = f
    for f in filas:  # un certificado manual anterior con el mismo nombre quedó reemplazado por el de FaceOps
        m = gestionados_cn.get(f["cn"])
        if not f["servicio"] and m and f["estado_ca"] == "VALID" and (f["inicio"] or ahora) < (m["inicio"] or ahora):
            f["reemplazado"] = True
            m.setdefault("previos_manuales", []).append(f)
    for f in filas:
        if f["estado_ca"] == "REVOKED":
            f["estado"], f["nivel"] = "revocado", "gris"
        elif f["reemplazado"]:
            f["estado"], f["nivel"] = "reemplazado", "gris"
        elif f["dias"] is not None and f["dias"] < 0:
            f["estado"], f["nivel"] = "vencido", "s-alta"
        elif f["dias"] is not None and f["dias"] <= UMBRAL:
            f["estado"], f["nivel"] = "por vencer", "s-media"
        else:
            f["estado"], f["nivel"] = "vigente", "s-ok"
        f["auto"] = bool(f["servicio"]) and actual.get((f["equipo_nombre"], f["servicio"])) == f["serie"]
        f["historial"] = [x for x in grupos.get((f["equipo_nombre"], f["servicio"]) if f["servicio"] else ("", f["cn"]), []) if x is not f] + f.get("previos_manuales", [])
    activos = [f for f in filas if f["estado"] not in ("reemplazado", "revocado")]
    serv = [f for f in activos if f["tipo"] == "servidor"]
    r = {"filas": sorted(filas, key=lambda f: (f["estado"] in ("reemplazado", "revocado"), f["dias"] if f["dias"] is not None else 99999)),
         "errores": errores, "programada": prog, "aap": aap_url, "ca_publica": CA_PUBLICA, "umbral": int(inv.get("umbral") or UMBRAL),
         "leido": inv.get("leido"), "job_inv": jb["id"] if jb else None, "jt_inv": jt["id"] if jt else None,
         "por_vencer_eda": inv.get("por_vencer") or [],
         "kpi": {"servidores": len(serv), "por_vencer": sum(1 for f in serv if f["estado"] == "por vencer"),
                 "vencidos": sum(1 for f in serv if f["estado"] == "vencido"), "auto": sum(1 for f in serv if f["auto"]),
                 "renovaciones": sum(1 for g in por_serie.values() if g["accion"] == "renovación"),
                 "proximo": min((f for f in serv), key=lambda f: f["dias"] if f["dias"] is not None else 99999, default=None)}}
    _cache[k] = (time.time(), r)
    return r
