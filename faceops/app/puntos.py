"""Puntos de restauración: FaceOps registra, con un código único por máquina, el estado completo en que estaba en un
momento: la imagen de la que nació su sistema, sus paquetes exactos, cada plataforma (cómo se instaló y su versión), su
configuración (commit del respaldo continuo en Git), sus aplicaciones y el respaldo de sus datos. Restaurar un punto
es orquestar la vuelta de todo ese conjunto: FaceOps no toca la máquina, lo hacen las automatizaciones de AAP."""
import hashlib
import json
import os
import re
import time

import requests

from .bd import ahora, ej, j, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS puntos_restauracion (id INTEGER PRIMARY KEY, cliente_id INTEGER, equipo_id INTEGER, equipo TEXT,
  codigo TEXT UNIQUE, creado TEXT, origen TEXT, usuario TEXT, nota TEXT, huella TEXT, ficha TEXT);
"""
ORIGEN_JT = "Infra · Origen de las máquinas"
GITEA = os.environ.get("FACEOPS_GITEA_INTERNO", "http://192.168.122.1:3000").rstrip("/")
_cache = {}
# imagen base / ISO → artefacto del registro propio (Gitea · repositorio, genérico)
ARTEFACTOS_SO = {"rhel9-base.qcow2": ("imagen-rhel9-base", "9.8"), "ubuntu-2404-base.qcow2": ("imagen-ubuntu-2404-base", "24.04"),
                 "ws2022-eval.iso": ("iso-windows-server-2022", "eval")}
HAB_SO = {"rhel9": "rhel9-base.qcow2", "ubuntu2404": "ubuntu-2404-base.qcow2", "windows2022": "ws2022-eval.iso"}


def iniciar():
    from .bd import con
    con().executescript(ESQUEMA)


def _tok():
    return os.environ.get("FACEOPS_RESPALDO_TOKEN", "")


def _h():
    t = _tok()
    return {"Authorization": f"token {t}"} if t else {}


# ------------------------------------------------------------------ de qué imagen nació cada máquina
def origenes(cliente):
    """{vm: {disco, imagen, ruta, iso, so_libvirt, sha256, tam}} del último «Infra · Origen de las máquinas» (caché 10 min)."""
    k = ("origen", cliente["id"])
    if k in _cache and time.time() - _cache[k][0] < 600:
        return _cache[k][1]
    out = {}
    try:
        from .aap import AAP
        aap = AAP.de_cliente(cliente)
        jt = (aap.get("job_templates/", name=ORIGEN_JT).get("results") or [None])[0]
        if jt:
            for jb in aap.get("jobs/", job_template=jt["id"], status="successful", order_by="-finished", page_size=3).get("results", []):
                art = (aap.get(f"jobs/{jb['id']}/").get("artifacts") or {}).get("faceops_origen") or {}
                if not art:
                    continue
                for anf, d in art.items():
                    hu = {}
                    for x in d.get("huellas") or []:
                        p = x.split("|")
                        if len(p) == 3:
                            hu[p[0]] = (p[1], int(p[2] or 0))
                    for v in d.get("vms") or []:
                        p = (v.split("|") + [""] * 5)[:5]
                        ruta = p[2] or p[3]
                        out[p[0]] = {"anfitrion": anf, "disco": p[1], "ruta": ruta, "imagen": os.path.basename(ruta) if ruta else "",
                                     "via": "imagen base (qcow2)" if p[2] else ("ISO de instalación" if p[3] else "disco propio (sin imagen base)"),
                                     "so_libvirt": p[4], "sha256": (hu.get(ruta) or ("", 0))[0], "tam": (hu.get(ruta) or ("", 0))[1],
                                     "leido": jb.get("finished")}
                break
    except Exception:  # noqa: BLE001
        pass
    _cache[k] = (time.time(), out)
    return out


def registro_generico():
    """{sha256: (nombre, versión, archivo)} de los artefactos genéricos del registro propio (imágenes, ISO, instaladores)."""
    if "gen" in _cache and time.time() - _cache["gen"][0] < 600:
        return _cache["gen"][1]
    out = {}
    try:
        for pq in requests.get(f"{GITEA}/api/v1/packages/repositorio", params={"type": "generic", "limit": 50}, headers=_h(), timeout=10).json() or []:
            fs = requests.get(f"{GITEA}/api/v1/packages/repositorio/generic/{pq['name']}/{pq['version']}/files", headers=_h(), timeout=10).json() or []
            for f in fs:
                out[f.get("sha256", "")] = (pq["name"], pq["version"], f.get("name", ""))
    except Exception:  # noqa: BLE001
        pass
    _cache["gen"] = (time.time(), out)
    return out


def _nombre_vm(e, origs):
    for k in (e["nombre"], (e["nombre"] or "").split(".")[0]):
        if k in origs:
            return k
    if (e.get("categoria") or "") == "clúster openshift" and "sno" in origs:
        return "sno"
    return None


# ------------------------------------------------------------------ la ficha: el estado completo de la máquina
PLAT_POR_CONTENEDOR = {"keycloak": "keycloak", "redisinsight": "redisinsight", "midpoint": "midpoint", "vault": "vault"}


def _git(nombre, ruta, **params):
    try:
        r = requests.get(f"{GITEA}/api/v1/repos/repositorio/respaldo-servidores{ruta}", params=params, headers=_h(), timeout=10)
        return r.json() if r.ok else None
    except requests.RequestException:
        return None


def _git_archivo(rama, ruta):
    try:
        r = requests.get(f"{GITEA}/api/v1/repos/repositorio/respaldo-servidores/raw/{ruta}", params={"ref": rama}, headers=_h(), timeout=10)
        return r.text if r.ok else ""
    except requests.RequestException:
        return ""


def ficha(cliente, e):
    """El estado completo de la máquina, como una sola configuración (lo que se guarda en un punto de restauración)."""
    from .identidad import NOMBRES_PLAT
    from .inventario import skills_de
    from .paquetes import PAQ_PLAT, de_equipo
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    sk = skills_de(e) if isinstance(e.get("skills_auto"), str) or e.get("skills_auto") is not None else (e.get("skills") or [])
    # 1. sistema y su imagen de origen
    origs = origenes(cliente)
    vm = _nombre_vm(e, origs)
    o = dict(origs.get(vm) or {}) if vm else {}
    if not o and d.get("origen"):
        o = dict(d["origen"])
    if not o and d.get("vsphere"):
        o = {"via": "plantilla de VMware", "imagen": (d["vsphere"] or {}).get("guest_id", ""), "anfitrion": e.get("ubicacion") or ""}
    gen = registro_generico()
    if o.get("sha256") and o["sha256"] in gen:
        o["registro"] = {"artefacto": gen[o["sha256"]][0], "version": gen[o["sha256"]][1], "archivo": gen[o["sha256"]][2], "igual": True}
    elif o.get("imagen") in ARTEFACTOS_SO:
        o["registro"] = {"artefacto": ARTEFACTOS_SO[o["imagen"]][0], "version": ARTEFACTOS_SO[o["imagen"]][1], "igual": None}
    sistema = {"so": e.get("so") or "", "kernel": d.get("kernel") or "", "origen": o}
    # 2. paquetes exactos (y su respaldo en el registro propio)
    pe = de_equipo(d, sk) or {}
    lista = sorted(f"{x['n']}|{x['v']}|{x['a']}" for x in (pe.get("lista") or []))
    paquetes = {"total": len(lista), "respaldados": pe.get("respaldados", 0), "registro": "repositorio/rpm", "inventario": pe.get("fecha"),
                "huella": hashlib.sha256("\n".join(lista).encode()).hexdigest()[:16] if lista else "", "lista": lista}
    # 3. plataformas: cómo se instaló cada una y su versión
    reglas = {k: re.compile(rx) for k, rx in PAQ_PLAT}
    plats = []
    for s in sk:
        nom = NOMBRES_PLAT.get(s, "_")
        if nom is None or s in ("kvm", "respaldo-git", "node-exporter", "windows-exporter", "monitoreo"):
            continue
        rpm = [x for x in (pe.get("lista") or []) if s in reglas and reglas[s].search(x["n"])]
        if rpm:
            plats.append({"plataforma": NOMBRES_PLAT.get(s) or s, "skill": s, "metodo": "paquetes RPM", "version": rpm[0]["v"],
                          "artefactos": [f"{x['n']}-{x['v']}.{x['a']}" for x in rpm][:12], "respaldado": all(x["respaldo"] for x in rpm),
                          "repositorio": rpm[0]["r"]})
        elif s in PLAT_POR_CONTENEDOR or s in ("keycloak", "midpoint"):
            plats.append({"plataforma": NOMBRES_PLAT.get(s) or s, "skill": s, "metodo": "imagen de contenedor", "version": "",
                          "artefactos": [], "respaldado": None})
        else:
            plats.append({"plataforma": NOMBRES_PLAT.get(s) or s, "skill": s, "metodo": "sin identificar (zip, binario o del fabricante)",
                          "version": "", "artefactos": [], "respaldado": None})
    # 4. configuración: commit del respaldo continuo
    conf = {"repositorio": "repositorio/respaldo-servidores", "rama": e["nombre"], "commit": "", "fecha": "", "titulo": ""}
    if "respaldo-git" in sk:
        cs = _git("/commits", sha=e["nombre"], limit=1, stat="false", files="false") or []
        if cs:
            conf.update(commit=cs[0]["sha"], fecha=cs[0]["commit"]["author"]["date"], titulo=cs[0]["commit"]["message"].strip().splitlines()[0])
    # 5. aplicaciones (registro del respaldo continuo en ese commit)
    apps = []
    if conf["commit"]:
        for ln in _git_archivo(conf["commit"], "meta/aplicaciones.txt").splitlines():
            p = [x.strip() for x in ln.split("|")]
            if p and p[0]:
                apps.append({"aplicacion": p[0], "version": p[1] if len(p) > 1 else "", "artefacto": p[2] if len(p) > 2 else ""})
    # 6. datos: respaldo con restic (lo registra la automatización de respaldos)
    datos = (d.get("respaldo_datos") or {"herramienta": "restic", "snapshot": "", "fecha": ""})
    return {"maquina": {"nombre": e["nombre"], "ip": e.get("ip") or "", "categoria": e.get("categoria") or "", "ubicacion": e.get("ubicacion") or "",
                        "ambiente": e.get("ambiente") or ""},
            "sistema": sistema, "paquetes": paquetes, "plataformas": plats, "configuracion": conf, "aplicaciones": apps, "datos": datos}


def estado_ficha(f):
    """[(eslabón, ok|parcial|falta, detalle)] de la cadena de recuperación."""
    o = f["sistema"]["origen"]
    out = [("Imagen del sistema", "ok" if o.get("registro") and o["registro"].get("igual") else ("parcial" if o.get("imagen") else "falta"),
            (o.get("imagen") or "sin identificar") + (f" · en el registro ({o['registro']['artefacto']})" if o.get("registro") else ""))]
    p = f["paquetes"]
    out.append(("Paquetes", "ok" if p["total"] and p["respaldados"] >= p["total"] else ("parcial" if p["total"] else "falta"),
                f"{p['respaldados']} de {p['total']} en el registro propio" if p["total"] else "sin inventariar"))
    pl = f["plataformas"]
    out.append(("Plataformas", "ok" if pl and all(x["respaldado"] for x in pl) else ("parcial" if pl else "ok"),
                ", ".join(f"{x['plataforma']} ({x['metodo']})" for x in pl) or "solo sistema operativo"))
    c = f["configuracion"]
    out.append(("Configuración", "ok" if c["commit"] else "falta", f"commit {c['commit'][:7]}" if c["commit"] else "sin respaldo continuo en Git"))
    out.append(("Aplicaciones", "ok", f"{len(f['aplicaciones'])} aplicación(es)" if f["aplicaciones"] else "sin aplicaciones desplegadas"))
    dt = f["datos"]
    out.append(("Datos", "ok" if dt.get("snapshot") else "falta", f"restic {dt['snapshot'][:8]}" if dt.get("snapshot") else "sin respaldo de datos aún"))
    return out


def _huella(f):
    base = {k: v for k, v in f.items() if k != "maquina"}
    base["sistema"] = {k: v for k, v in f["sistema"].items()}
    base["sistema"]["origen"] = {k: v for k, v in (f["sistema"]["origen"] or {}).items() if k not in ("leido",)}
    base["paquetes"] = {k: v for k, v in f["paquetes"].items() if k not in ("inventario", "lista")}
    return hashlib.sha256(json.dumps(base, sort_keys=True, default=str).encode()).hexdigest()[:16]


def crear(cliente, e, origen="manual", usuario="", nota="", si_cambio=False):
    """Registra un punto de restauración con el estado actual. si_cambio: solo si difiere del último punto."""
    iniciar()
    f = ficha(cliente, e)
    hu = _huella(f)
    ult = q1("SELECT * FROM puntos_restauracion WHERE cliente_id=? AND equipo_id=? ORDER BY id DESC LIMIT 1", cliente["id"], e["id"])
    if si_cambio and ult and ult["huella"] == hu:
        return ult
    n = (q1("SELECT COUNT(*) n FROM puntos_restauracion WHERE cliente_id=? AND equipo_id=?", cliente["id"], e["id"]) or {"n": 0})["n"] + 1
    cod = f"PR-{e['nombre'].split('.')[0].upper()}-{n:04d}"
    ej("INSERT INTO puntos_restauracion(cliente_id, equipo_id, equipo, codigo, creado, origen, usuario, nota, huella, ficha) VALUES (?,?,?,?,?,?,?,?,?,?)",
       cliente["id"], e["id"], e["nombre"], cod, ahora(), origen, usuario, nota, hu, json.dumps(f, ensure_ascii=False))
    publicar(cod, f)
    return q1("SELECT * FROM puntos_restauracion WHERE codigo=?", cod)


def publicar(cod, f):
    """Versión legible del punto en Git (repositorio/puntos-restauracion), para auditoría."""
    try:
        import base64
        import yaml
        tok = _tok()
        if not tok:
            return
        h = {"Authorization": f"token {tok}"}
        if requests.get(f"{GITEA}/api/v1/repos/repositorio/puntos-restauracion", headers=h, timeout=8).status_code == 404:
            requests.post(f"{GITEA}/api/v1/user/repos", headers=h, json={"name": "puntos-restauracion", "private": True, "auto_init": True,
                                                                         "default_branch": "main", "description": "Puntos de restauración de FaceOps"}, timeout=10)
        doc = dict(f, punto=cod)
        doc["paquetes"] = {k: v for k, v in f["paquetes"].items()}
        txt = f"# Punto de restauración {cod} · FaceOps\n" + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=140)
        ruta = f"{f['maquina']['nombre']}/{cod}.yml"
        requests.post(f"{GITEA}/api/v1/repos/repositorio/puntos-restauracion/contents/{ruta}", headers=h, timeout=15,
                      json={"content": base64.b64encode(txt.encode()).decode(), "message": f"{cod}: punto de restauración de {f['maquina']['nombre']}",
                            "author": {"name": "FaceOps", "email": "faceops@invoke.cl"}})
    except Exception as x:  # noqa: BLE001
        print("Punto de restauración · Git:", str(x)[:200])


def lista(cliente_id, equipo_id):
    iniciar()
    ps = q("SELECT * FROM puntos_restauracion WHERE cliente_id=? AND equipo_id=? ORDER BY id DESC", cliente_id, equipo_id)
    out = []
    for i, p in enumerate(ps):
        f = j(p["ficha"], {})
        ant = j(ps[i + 1]["ficha"], {}) if i + 1 < len(ps) else None
        out.append(dict(p, f=f, estado=estado_ficha(f), diferencias=_diferencias(ant, f) if ant else ["primer punto de la máquina"]))
    return out


def _diferencias(a, b):
    out = []
    if (a["sistema"].get("so"), a["sistema"].get("kernel")) != (b["sistema"].get("so"), b["sistema"].get("kernel")):
        out.append(f"sistema: {a['sistema'].get('so')} {a['sistema'].get('kernel')} → {b['sistema'].get('so')} {b['sistema'].get('kernel')}")
    pa, pb = set(a["paquetes"].get("lista") or []), set(b["paquetes"].get("lista") or [])
    if pa != pb:
        out.append(f"paquetes: +{len(pb - pa)} / −{len(pa - pb)}")
    va = {x["plataforma"]: x["version"] for x in a.get("plataformas") or []}
    vb = {x["plataforma"]: x["version"] for x in b.get("plataformas") or []}
    for k in sorted(set(va) | set(vb)):
        if va.get(k) != vb.get(k):
            out.append(f"{k}: {va.get(k) or '—'} → {vb.get(k) or '—'}")
    if a["configuracion"].get("commit") != b["configuracion"].get("commit"):
        out.append(f"configuración: {(a['configuracion'].get('commit') or '—')[:7]} → {(b['configuracion'].get('commit') or '—')[:7]}")
    aa = {x["aplicacion"]: x["version"] for x in a.get("aplicaciones") or []}
    ab = {x["aplicacion"]: x["version"] for x in b.get("aplicaciones") or []}
    for k in sorted(set(aa) | set(ab)):
        if aa.get(k) != ab.get(k):
            out.append(f"{k}: {aa.get(k) or '—'} → {ab.get(k) or '—'}")
    if (a.get("datos") or {}).get("snapshot") != (b.get("datos") or {}).get("snapshot"):
        out.append("datos: respaldo nuevo")
    return out or ["sin cambios"]
