"""Certificados: lo que emitió la CA interna (Red Hat Certificate System) y lo que está en uso en cada máquina.
FaceOps NO se conecta a la CA ni a las máquinas: todo sale de AAP.
· «Certificados · Inventario» (AAP, cada hora y tras cada renovación): emitidos por la CA + en uso en las máquinas
· trabajos de la skill «certificados»: cuándo se emitió o renovó cada uno
La renovación automática la decide Event-Driven Ansible (rulebooks/certificados.yml). Cache de 60 s."""
import os
import time
from datetime import datetime, timezone

from .aap import AAP, AAPNoDisponible, ErrorAAP

INVENTARIO = "Certificados · Inventario"
CA_PUBLICA = os.environ.get("FACEOPS_CA_PUBLICA", "https://pki.34.176.42.56.sslip.io")
UMBRAL = 30
JTS = ("Certificados · Emitir para servicio", "Certificados · Renovar por vencer", "Certificados · OpenShift")
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
    base, ult, uso, pv = None, None, {}, []
    for jb in aap.get("jobs/", job_template=jt["id"], status="successful", order_by="-finished", page_size=25).get("results", []):
        d = (aap.get(f"jobs/{jb['id']}/").get("artifacts") or {}).get("faceops_cert_inventario")
        if not d:
            continue
        if base is None:   # lo emitido por la CA y la hora de lectura: del trabajo más reciente
            base, ult = d, jb
        # en uso: un trabajo lanzado con límite (FaceOps, sobre una máquina o un segmento) solo leyó su «alcance»;
        # las demás máquinas se conservan de lecturas anteriores, hasta la última completa
        completo = d.get("alcance") is None or d.get("completo") in (True, "True", "true")
        for eq in (d.get("en_uso") or {}) if completo else (d.get("alcance") or []):
            if eq not in uso:
                uso[eq] = (d.get("en_uso") or {}).get(eq) or []
                pv += [x for x in d.get("por_vencer") or [] if x.get("equipo") == eq]
        if completo:
            break
    if base is None:
        return {}, None, jt
    return dict(base, en_uso=uso, por_vencer=pv), ult, jt


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
                                    "accion": "renovación" if nombre == "Certificados · Renovar por vencer" else "emisión"}
                    actual[(eq, svc)] = s
    prog = None
    jt = (aap.get("job_templates/", name=JTS[1]).get("results") or [None])[0]
    if jt:
        sch = [s for s in aap.get("schedules/", unified_job_template=jt["id"]).get("results", []) if s.get("enabled")]
        prog = min((s.get("next_run") for s in sch if s.get("next_run")), default=None)
    return por_serie, actual, prog


def _demo(cliente):
    """Certificados de demostración (datos/certificados-demo.yml), solo en el laboratorio."""
    import yaml
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "certificados-demo.yml")
    from .semillas import limpia
    if limpia() or os.environ.get("FACEOPS_CERT_DEMO", "1") != "1" or not os.path.exists(ruta):
        return []
    try:
        with open(ruta, encoding="utf-8") as fh:
            return [d for d in (yaml.safe_load(fh) or []) if d.get("equipo") and d.get("serie")]
    except Exception:  # noqa: BLE001
        return []


def leer(cliente, equipos):
    """Panel de certificados. En una página nunca se llama a AAP: se entrega la última lectura (la renueva un hilo de
    fondo); sin lectura previa se espera a lo más 1 s y, si no alcanza, el panel sale con lo local (sin error: si AAP está
    caído lo dice el banner global)."""
    try:
        from flask import has_request_context
        en_pagina = has_request_context()
    except Exception:  # noqa: BLE001
        en_pagina = False
    if en_pagina:
        return leer_sin_esperar(cliente, equipos, espera=1.0) or _leer_aap(cliente, equipos, sin_aap=True)
    return _leer_aap(cliente, equipos)


def _leer_aap(cliente, equipos, sin_aap=False):
    k = cliente["id"]
    if not sin_aap and k in _cache and time.time() - _cache[k][0] < 60:
        return _cache[k][1]
    errores, certs, inv, jb, jt, uso = [], [], {}, None, None, {}
    por_serie, actual, prog, aap_url = {}, {}, None, ""
    try:
        if sin_aap:
            raise AAPNoDisponible("sin lectura aún")
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
    except AAPNoDisponible:   # AAP caído: lo avisa el banner global; se conserva la última lectura buena
        if not sin_aap and _cache.get(k):
            _cache[k] = (time.time() - 30, _cache[k][1])   # reintento en 30 s
            return _cache[k][1]
    except ErrorAAP as e:
        if _cache.get(k) and not sin_aap and not _cache[k][1].get("errores"):
            _cache[k] = (time.time() - 30, _cache[k][1])
            return _cache[k][1]
        errores.append(f"AAP: {str(e)[:160]}")
    demo = _demo(cliente)
    for d in demo:
        certs.append({"id": d["serie"], "subjectdn": f"CN={d['equipo']}.demo.local,OU=demo,O=Invoke", "issuerdn": "CN=Invoke CA interna,O=Invoke",
                      "notvalidbefore": f"{d['desde']} 00:00:00", "notvalidafter": f"{d['hasta']} 00:00:00", "issuedon": f"{d['desde']} 00:00:00", "status": "VALID"})
        uso = dict(uso)
        uso[d["equipo"]] = list(uso.get(d["equipo"]) or []) + [{"serie": d["serie"], "servicio": d["servicio"], "puerto": d.get("puerto"), "entrega": True}]
    # en uso según las máquinas: serie → (equipo, servicio, entrega en el puerto)
    en_uso, instalado = {}, {}
    for eq, lst in uso.items():
        for u in lst or []:
            en_uso[_serie(u.get("serie"))] = dict(u, equipo=eq)
            instalado.setdefault(_serie(u.get("serie")), []).append(dict(u, equipo=eq))
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
                      "gestion": g, "emisor": emisor, "uso": u, "instalado_en": instalado.get(s, []),
                      "estado_ca_txt": c.get("status") or "", "emitido": _fecha(c.get("issuedon")), "issuer_dn": c.get("issuerdn", ""),
                      "crudo": {k_: v_ for k_, v_ in c.items() if k_ not in ("link",) and not isinstance(v_, (dict, list))}})
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
    series_demo = {_serie(d["serie"]) for d in demo}
    for f in filas:
        f["demo"] = f["serie"] in series_demo
        if f["demo"]:   # de demostración: no está instalado de verdad, así que reemplazarlo es emitir uno real (no renovar)
            f["auto"] = False
    activos = [f for f in filas if f["estado"] not in ("reemplazado", "revocado")]
    serv = [f for f in activos if f["tipo"] == "servidor"]
    r = {"filas": sorted(filas, key=lambda f: (f["estado"] in ("reemplazado", "revocado"), f["dias"] if f["dias"] is not None else 99999)),
         "errores": errores, "programada": prog, "en_uso": uso, "aap": aap_url, "ca_publica": CA_PUBLICA, "umbral": int(inv.get("umbral") or UMBRAL),
         "leido": inv.get("leido"), "job_inv": jb["id"] if jb else None, "jt_inv": jt["id"] if jt else None,
         "por_vencer_eda": inv.get("por_vencer") or [],
         "kpi": {"servidores": len(serv), "por_vencer": sum(1 for f in serv if f["estado"] == "por vencer"),
                 "vencidos": sum(1 for f in serv if f["estado"] == "vencido"), "auto": sum(1 for f in serv if f["auto"]),
                 "renovaciones": sum(1 for g in por_serie.values() if g["accion"] == "renovación"),
                 "proximo": min((f for f in serv), key=lambda f: f["dias"] if f["dias"] is not None else 99999, default=None)}}
    if not sin_aap:
        _cache[k] = (time.time(), r)
    return r


_en_curso = {}


def leer_sin_esperar(cliente, equipos, espera=0.8):
    """Para vistas que no deben bloquearse (el mapa): la lectura vigente si está en caché; si venció, la renueva en segundo
    plano (una sola a la vez por cliente) y entrega la anterior sin esperar; sin lectura previa espera a lo más `espera` s
    y, si no alcanza, devuelve None (sin lectura)."""
    import threading
    k = cliente["id"]
    c = _cache.get(k)
    if c and time.time() - c[0] < 60:
        return c[1]
    th = _en_curso.get(k)
    nuevo = not th or not th.is_alive()
    if nuevo:
        def _renovar(cl, eqs):
            try:
                leer(cl, eqs)
            except Exception:  # noqa: BLE001 - AAP caída o lenta: queda la lectura anterior
                pass
        th = threading.Thread(target=_renovar, args=(dict(cliente), list(equipos)), daemon=True, name=f"certificados-{k}")
        _en_curso[k] = th
        th.start()
    if not c and nuevo:   # se espera solo quien lanzó la lectura; las demás páginas no esperan
        th.join(espera)
        c = _cache.get(k)
    return c[1] if c else None


def buscar_serie(r, serie):
    return next((f for f in r["filas"] if f["serie"] == _serie(serie)), None)


def de_equipo(r, nombre):
    """Certificados activos que usa o tiene asignados una máquina."""
    return [f for f in r["filas"] if f["estado"] not in ("reemplazado", "revocado")
            and (f["equipo_nombre"] == nombre or any(u.get("equipo") == nombre for u in f.get("instalado_en") or []))]


def dependencias(f, equipos):
    """Servidores que presentan el certificado (lo tienen instalado) y clientes que dependen de él (consumen ese servicio:
    comparten segmento de negocio y confían en la CA emisora). Solo datos del inventario; nada inventado."""
    servidores, clientes = [], []
    for m in afectadas(f, equipos):
        u = next((x for x in f.get("instalado_en") or [] if x.get("equipo") == m["e"]["nombre"]), {})
        if m["rol"].startswith("lo instala"):
            servidores.append(dict(m, servicio=u.get("servicio") or f.get("servicio") or "", puerto=u.get("puerto") or ((f.get("uso") or {}).get("puerto") if f.get("equipo_nombre") == m["e"]["nombre"] else "")))
        else:
            clientes.append(m)
    return servidores, clientes


def afectadas(f, equipos):
    """Máquinas afectadas por un certificado: donde está instalado y las que consumen ese servicio (comparten segmento)."""
    por_nombre = {e["nombre"]: e for e in equipos}
    out, vistos = [], set()
    host = [u["equipo"] for u in f.get("instalado_en") or []] or ([f["equipo_nombre"]] if f["equipo_nombre"] else [])
    for n in host:
        e = por_nombre.get(n)
        if e and e["id"] not in vistos:
            vistos.add(e["id"])
            u = next((x for x in f.get("instalado_en") or [] if x.get("equipo") == n), {})
            out.append({"e": e, "rol": "lo instala" + (f" · {u.get('servicio')}" if u.get("servicio") else "") + (f" · puerto {u.get('puerto')}" if u.get("puerto") else "")})
    segs = {s for n in host for s in (por_nombre.get(n) or {}).get("segmentos") or []}
    for e in equipos:
        if e["id"] not in vistos and segs & set(e.get("segmentos") or []):
            vistos.add(e["id"])
            out.append({"e": e, "rol": "consume el servicio (" + ", ".join(sorted(segs & set(e["segmentos"]))) + ")"})
    return out
