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
HAB_SO = {**{f"rhel{v}": f"rhel{v}-base.qcow2" for v in (5, 6, 7, 8, 10)}, "rhel9": "rhel9-base.qcow2", "ubuntu2404": "ubuntu-2404-base.qcow2", "rocky9": "rocky9-base.qcow2", "windows2022": "ws2022-eval.iso"}


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
PLAT_POR_CONTENEDOR = {"jenkins": "jenkins", "keycloak": "keycloak", "redisinsight": "redisinsight", "midpoint": "midpoint", "vault": "vault"}


def _git(ruta, repo="repositorio/respaldo-servidores", **params):
    try:
        r = requests.get(f"{GITEA}/api/v1/repos/{repo}{ruta}", params=params, headers=_h(), timeout=10)
        return r.json() if r.ok else None
    except requests.RequestException:
        return None


def _git_archivo(rama, ruta, repo="repositorio/respaldo-servidores"):
    try:
        r = requests.get(f"{GITEA}/api/v1/repos/{repo}/raw/{ruta}", params={"ref": rama}, headers=_h(), timeout=10)
        return r.text if r.ok else ""
    except requests.RequestException:
        return ""


ESLABONES_OCP = [("version", "Versión del clúster"), ("etcd", "Respaldo de etcd"), ("operadores", "Operadores"),
                 ("git_config", "Git de configuración"), ("git_apps", "Git de aplicaciones"), ("imagenes", "Imágenes (registro)"),
                 ("storage", "Storage"), ("oadp", "OADP (respaldo de datos)")]


def ficha_ocp(cliente, e):
    """Punto de restauración de un clúster OpenShift: todo lo que permite volver a levantar su estado."""
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    o = d.get("ocp") or {}
    r = o.get("respaldo") or {}
    apps = r.get("gitops") or []
    es_config = lambda a: (a.get("ns_destino") or "").startswith(("openshift", "kube")) or any(x in (a.get("ruta") or "").lower() for x in ("cluster", "config", "infra", "platform"))
    segs = []
    try:
        segs = [dict(x) for x in q("SELECT segmento, ambiente, namespace, commit_git, gitops FROM segmentos_ocp WHERE cliente_id=?", cliente["id"])]
    except Exception:  # noqa: BLE001
        pass
    oc = {
        "version": {"version": o.get("version", ""), "canal": o.get("canal", ""), "historial": r.get("historial_version") or [], "irreversible": True},
        "etcd": {"cronjobs": r.get("etcd_cronjobs") or [], "ultimos": r.get("etcd_jobs") or []},
        "operadores": {"lista": sorted(f"{x['n']}|{x.get('csv', '')}|{x.get('aprob', '')}" for x in (o.get("operadores") or []))},
        "git_config": {"apps": [a for a in apps if es_config(a)]},
        "git_apps": {"apps": [a for a in apps if not es_config(a)], "segmentos": segs},
        "imagenes": {"registro": r.get("registro", ""), "imagestreams": r.get("imagestreams") or [], "espejos": r.get("espejos") or []},
        "storage": {"pvs": int(r.get("pvs") or 0), "clases": r.get("clases_storage") or [], "snapshots": r.get("snapshots") or []},
        "oadp": {"backups": (r.get("oadp_backups") or [])[-10:], "programas": r.get("oadp_programas") or []},
    }
    return {"tipo": "openshift", "maquina": {"nombre": e["nombre"], "ip": e.get("ip") or "", "categoria": e.get("categoria") or "",
                                             "ubicacion": e.get("ubicacion") or "", "ambiente": e.get("ambiente") or ""}, "ocp": oc}


def estado_ocp(f):
    oc = f.get("ocp") or {}
    v, et, op, gc, ga, im, st, oa = (oc.get(k) or {} for k, _ in ESLABONES_OCP)
    ult = (et.get("ultimos") or [""])[-1].split("|") if et.get("ultimos") else []
    ok_etcd = len(ult) >= 3 and ult[2] not in ("0", "", "None")
    return [("Versión del clúster", "ok" if v.get("version") else "falta", f"{v.get('version') or '—'} · canal {v.get('canal') or '—'} · sin vuelta atrás si se actualiza"),
            ("Respaldo de etcd", "ok" if ok_etcd else ("parcial" if et.get("cronjobs") else "falta"),
             (f"último {ult[1][:16]} {'correcto' if ok_etcd else 'con falla'}" if ult else ("CronJob sin ejecuciones" if et.get("cronjobs") else "sin CronJob de respaldo (T-01)"))),
            ("Operadores", "ok" if op.get("lista") else "falta", f"{len(op.get('lista') or [])} operador(es) con su versión"),
            ("Git de configuración", "ok" if gc.get("apps") else "falta", f"{len(gc.get('apps') or [])} aplicación(es) de Argo CD" if gc.get("apps") else "la configuración del clúster no está en GitOps"),
            ("Git de aplicaciones", "ok" if (ga.get("apps") or ga.get("segmentos")) else "falta",
             f"{len(ga.get('apps') or [])} app(s) de Argo CD · {len(ga.get('segmentos') or [])} segmento(s)" if (ga.get("apps") or ga.get("segmentos")) else "sin aplicaciones en GitOps"),
            ("Imágenes (registro)", "ok" if im.get("registro") and not im.get("registro", "").startswith("Removed") else "parcial", f"registro interno {(im.get('registro') or '').strip(' ·') or 'sin dato'} · {len(im.get('imagestreams') or [])} imagestream(s)"),
            ("Storage", "ok" if st.get("snapshots") or not st.get("pvs") else "parcial",
             f"{st.get('pvs', 0)} PV · {len(st.get('snapshots') or [])} snapshot(s)" if st.get("pvs") else "sin volúmenes persistentes"),
            ("OADP (respaldo de datos)", "ok" if oa.get("backups") else "falta", f"{len(oa.get('backups') or [])} respaldo(s) · {len(oa.get('programas') or [])} programa(s)" if oa.get("backups") or oa.get("programas") else "sin OADP: los datos de los volúmenes no se respaldan (T-12)")]


# Cómo instala cada skill de FaceOps su plataforma (cuando la máquina aún no tiene inventario de paquetes): método, fuente
INSTALADORES = {
    "vault": ("paquete RPM", "rpm.releases.hashicorp.com (repositorio oficial de HashiCorp; skill «Vault · Preparar servidor»)", r"^vault"),
    "sqlserver": ("paquetes RPM", "packages.microsoft.com/rhel (mssql-server 2022; skill «SQL Server · Preparar servidor»)", r"^mssql"),
    "jenkins": ("imagen de contenedor", "docker.io/jenkins/jenkins:lts-jdk17 (skill «Jenkins · Preparar servidor»)", ""),
    "redisinsight": ("imagen de contenedor", "docker.io/redis/redisinsight (skill «Redis · Instalar Redis Insight»)", ""),
    "redis": ("paquete RPM", "AppStream de RHEL (skill «Redis · Preparar servidor»)", r"^(redis|valkey)"),
    "jboss": ("zip del fabricante", "Red Hat: jboss-eap-8 desde el registro propio (skill «JBoss · Preparar servidor»)", r"^eap"),
}


SISTEMA_MONTAJES = ("/", "/boot", "/boot/efi", "/efi", "/usr", "/var", "/tmp", "/home", "/opt")


def volumenes(cliente, e, datos_resp=None):
    """Volúmenes de la máquina con su origen y su nivel de respaldo. Los del sistema se recuperan desde la imagen y Git; los
    de datos necesitan un respaldo propio (restic al repositorio de respaldos o snapshot del storage que los entrega); los de
    red se respaldan en su origen. Sin mecanismo para un volumen de datos queda marcado: ante una catástrofe se pierde."""
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    discos = j(e.get("discos"), []) if isinstance(e.get("discos"), str) else (e.get("discos") or [])
    resp = datos_resp or d.get("respaldo_datos") or {}
    rutas_resp = set(resp.get("rutas") or [])
    try:
        org = origenes(cliente).get(_nombre_vm(e, origenes(cliente)) or "") or {}
    except Exception:  # noqa: BLE001
        org = {}
    out = []
    for dk in discos:
        tipo = (dk.get("tipo") or "").lower()
        if "iscsi" in tipo or dk.get("san"):
            origen = f"volumen SAN {dk.get('san') or ''} (iSCSI)".strip()
            via = "san"
        elif "virtio" in tipo or (e.get("forma") or "") == "virtual":
            base = org.get("disco") if dk.get("dev") in ("vda", "sda") else ""
            origen = f"disco virtual en {org.get('anfitrion') or 'el hipervisor'}" + (f" · {base}" if base else f" · {dk.get('dev')}")
            via = "virtual"
        else:
            origen = f"disco local {dk.get('modelo') or ''} · {dk.get('dev')}".strip()
            via = "local"
        for pt in dk.get("particiones") or [{"montajes": [{"punto": dk.get("punto"), "gb": dk.get("gb"), "fs": ""}]}]:
            for m in pt.get("montajes") or []:
                mp = m.get("punto") or ""
                if not mp.startswith("/") or mp.startswith("/dev"):
                    continue
                es_sis = mp in SISTEMA_MONTAJES
                if es_sis:
                    nivel, como = "recuperable", "imagen de instalación + configuración y paquetes en Git (eslabones 1 a 4)"
                elif mp in rutas_resp or (resp.get("snapshot") and not rutas_resp):
                    nivel, como = "respaldado", f"{resp.get('herramienta') or 'restic'} · {resp.get('snapshot') or ''}".strip(" ·")
                elif via == "san":
                    nivel, como = "parcial", "depende de los snapshots del storage: falta conectarlos al sistema de respaldo"
                else:
                    nivel, como = "sin respaldo", "volumen de datos sin mecanismo de respaldo: ante una catástrofe se pierde"
                out.append({"montaje": mp, "fs": m.get("fs") or "", "gb": m.get("gb") or dk.get("gb"), "uso": m.get("uso"), "origen": origen,
                            "via": via, "sistema": es_sis, "nivel": nivel, "como": como})
    for r in d.get("montajes_red") or []:
        mp = r.get("punto") if isinstance(r, dict) else str(r)
        src = r.get("origen") or r.get("fuente") or "" if isinstance(r, dict) else ""
        out.append({"montaje": mp, "fs": (r.get("fs") if isinstance(r, dict) else "") or "red", "gb": (r.get("gb") if isinstance(r, dict) else None),
                    "uso": None, "origen": f"montaje de red {src}".strip(), "via": "red", "sistema": False, "nivel": "en su origen",
                    "como": "se respalda donde vive (servidor de archivos o storage de origen)"})
    return out


def asegurar_repo_maquina(nombre):
    """Crea repositorio/maquina-<nombre> (rama master) si no existe. Lo crea FaceOps con su token de Gitea (la credencial del
    inventario solo escribe en los repositorios de la organización, no los crea)."""
    org = os.environ.get("FACEOPS_GIT_ORG_MAQUINAS", "repositorio")
    n = f"maquina-{nombre.split('.')[0]}"
    if requests.get(f"{GITEA}/api/v1/repos/{org}/{n}", headers=_h(), timeout=8).status_code == 200:
        return True   # la credencial del inventario ya lo ve
    tok = os.environ.get("FACEOPS_GITEA_ORG_TOKEN") or os.environ.get("FACEOPS_GITEA_TOKEN", "")   # con write:organization
    h = {"Authorization": f"token {tok}"}
    r = requests.post(f"{GITEA}/api/v1/orgs/{org}/repos", headers=h, timeout=15,
                      json={"name": n, "private": True, "auto_init": True, "default_branch": "master",
                            "description": f"Máquina {nombre}: instantánea diaria del inventario (FaceOps). Rama master = estado vigente."})
    if r.status_code not in (201, 409):
        return False
    # mismos colaboradores que el repositorio del respaldo continuo (el usuario de servicio del inventario escribe)
    for c in requests.get(f"{GITEA}/api/v1/repos/{org}/respaldo-servidores/collaborators", headers=h, timeout=10).json() or []:
        requests.put(f"{GITEA}/api/v1/repos/{org}/{n}/collaborators/{c['login']}", headers=h, json={"permission": "write"}, timeout=10)
    return requests.get(f"{GITEA}/api/v1/repos/{org}/{n}", headers=_h(), timeout=8).status_code == 200


def paquetes_de_git(conf):
    """Lista exacta de paquetes (meta/paquetes.txt) del commit del punto en Git: del agente o de la instantánea."""
    conf = conf or {}
    k_ = ("pqgit", conf.get("commit"), (conf.get("continuo") or {}).get("commit"))
    if k_ in _cache and time.time() - _cache[k_][0] < 600:
        return _cache[k_][1]
    r_ = _paquetes_de_git(conf)
    _cache[k_] = (time.time(), r_)
    return r_


def _paquetes_de_git(conf):
    for repo_, ref_ in (((conf.get("continuo") or {}).get("repositorio"), (conf.get("continuo") or {}).get("commit")), (conf.get("repositorio"), conf.get("commit"))):
        if not ref_ or not repo_:
            continue
        filas = []
        try:
            from .paquetes import en_registro, sin_epoca
            reg = en_registro()
        except Exception:  # noqa: BLE001
            reg, sin_epoca = set(), (lambda v: v)
        for ln in _git_archivo(ref_, "meta/paquetes.txt", repo=repo_).splitlines():
            n, _, nevra = ln.partition("|")
            if not n or not nevra:
                continue
            arch = nevra.rsplit(".", 1)[-1] if "." in nevra else ""
            ver = nevra[len(n) + 1:len(nevra) - len(arch) - 1] if nevra.startswith(n + "-") and arch else nevra
            resp = "1" if f"{n}|{sin_epoca(ver)}" in reg else ("0" if reg else "")
            filas.append(f"{n}|{ver}|{arch}||{resp}|{'base' if '.el' in ver else 'repo'}||")
        if filas:
            lista = sorted(filas)
            return {"total": len(lista), "lista": lista, "fuente": f"Git · {repo_} · {ref_[:7]}", "respaldados": sum(1 for x in lista if x.split("|")[4] == "1"),
                    "huella": hashlib.sha256("\n".join("|".join(x.split("|")[:3]) for x in lista).encode()).hexdigest()[:16]}
    return None


def recuperacion(f):
    """% de recuperación de un punto: qué tan completa volvería la máquina si se restaura desde él, con el porqué y cómo
    mejorarlo. Con la cobertura medida (inventario con instantánea) es esa cobertura; sin ella, la suma de sus eslabones."""
    f = f or {}
    if (f.get("maquina") or {}).get("categoria") == "clúster openshift" or f.get("tipo") == "ocp":
        return None
    cob = f.get("cobertura")
    if cob and cob.get("pct") is not None:
        return {"pct": cob["pct"], "base": "cobertura medida",
                "ok": [f"{x['que']} · {x['mecanismo']}" for x in cob.get("items") or [] if x.get("ok")],
                "mejorar": [{"t": f"{x['que']}: {x['detalle']}", "como": x["mecanismo"], "d": round(100 / max(1, cob["total"]))}
                            for x in cob.get("items") or [] if not x.get("ok")]}
    o_ = (f.get("sistema") or {}).get("origen") or {}
    conf = f.get("configuracion") or {}
    pq = f.get("paquetes") or {}
    if not pq.get("lista"):
        pq = paquetes_de_git(conf) or pq
    vols = [v for v in ((f.get("datos") or {}).get("volumenes") or []) if not v.get("sistema")]
    pasos = [("Imagen de nacimiento idéntica en el registro", 15, bool((o_.get("registro") or {}).get("igual")),
              "cargarla con «Infra · Cargar imágenes base al registro»"),
             ("Paquetes exactos respaldados en el registro (90 % o más)", 25, bool(pq.get("total")) and pq.get("respaldados", 0) >= 0.9 * pq["total"],
              "inventariar la máquina: sube al registro los RPM que falten"),
             ("Plataformas con su fuente de instalación", 10, all(x.get("metodo") and "sin identificar" not in x.get("metodo", "") for x in f.get("plataformas") or []),
              "instalar las plataformas desde FaceOps o registrar su instalador"),
             ("Configuración en Git", 35, bool(conf.get("commit")), "inventario diario con instantánea en el repositorio de la máquina (rama master)"),
             ("Volúmenes de datos con respaldo", 15, all(v.get("nivel") in ("respaldado", "en su origen") for v in vols), "respaldo de datos con restic o snapshot del storage")]
    return {"pct": sum(p_ for _, p_, ok, _ in pasos if ok), "base": "eslabones del punto",
            "ok": [t for t, _, ok, _ in pasos if ok], "mejorar": [{"t": t, "como": c, "d": p_} for t, p_, ok, c in pasos if not ok]}


def evaluar_cobertura(cob, vols, origen=None):
    """Todo lo que tiene la máquina, cada cosa con su mecanismo de recuperación y si está cubierta. Meta: 100 % sin copiar el
    disco completo. Mecanismos: imagen base (registro) · paquetes exactos (registro RPM) · configuración (Git, rama master) ·
    rutas extra pequeñas (Git, agregadas solas) · rutas grandes y software sin paquete (registro, artefacto por huella) ·
    secretos (bóveda) · volúmenes de datos (restic / storage)."""
    if not cob:
        return None
    items = []
    o = origen or {}
    items.append({"que": "Imagen del sistema", "detalle": o.get("imagen") or "sin identificar", "mecanismo": "registro propio (imagen base)",
                  "ok": bool((o.get("registro") or {}).get("igual"))})
    items.append({"que": "Configuración del sistema y de los productos", "detalle": "/etc, cron y productos encontrados", "mecanismo": "Git · rama master", "ok": True})
    for r in cob.get("auto") or []:
        items.append({"que": "Ruta agregada sola", "detalle": f"{r['ruta']} · {r['mb']} MB", "mecanismo": "Git · rama master", "ok": True})
    arts = cob.get("artefactos") or {}
    for k, t in (("rutas", "Ruta grande fuera de la configuración"), ("sin_paquete", "Software sin paquete")):
        for r in cob.get(k) or []:
            items.append({"que": t, "detalle": f"{r['ruta']} · {r['mb']} MB", "mecanismo": "registro propio (artefacto por huella)",
                          "ok": r["ruta"] in arts, "ref": arts.get(r["ruta"], "")})
    if cob.get("omitidos"):
        n_ok = sum(1 for k in arts if not any(k == r["ruta"] for x in ("rutas", "sin_paquete") for r in cob.get(x) or []))
        items.append({"que": "Archivos de más de 25 MB", "detalle": f"{cob['omitidos']} archivo(s)", "mecanismo": "registro propio (artefacto por huella)",
                      "ok": n_ok >= cob["omitidos"]})
    if cob.get("n_secretos") or cob.get("secretos"):
        items.append({"que": "Secretos (llaves, keystores, shadow)", "detalle": f"{cob.get('n_secretos') or len(cob.get('secretos') or [])} archivo(s)",
                      "mecanismo": "bóveda · maquinas/<nombre>/archivos", "ok": cob.get("boveda") in ("guardado", "igual"),
                      "nota": {"sin_credencial": "falta la credencial de la bóveda en el inventario"}.get(cob.get("boveda"), "")})
    for v in vols or []:
        if v.get("sistema"):
            continue
        items.append({"que": "Volumen de datos", "detalle": f"{v['montaje']} · {v.get('gb') or '—'} GB · {v.get('origen')}",
                      "mecanismo": v.get("como") or "", "ok": v.get("nivel") in ("respaldado", "en su origen")})
    for e_ in cob.get("errores") or []:
        items.append({"que": "No se pudo subir al registro", "detalle": e_, "mecanismo": "registro propio", "ok": False})
    ok = sum(1 for x in items if x["ok"])
    return {"items": items, "ok": ok, "total": len(items), "pct": round(100 * ok / len(items)) if items else None}


def origen_de(cliente, e, d=None):
    """De qué imagen nació la máquina y si esa imagen está en el registro propio. No cambia en la vida de la máquina."""
    d = d if d is not None else (j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {}))
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
        o["registro"] = {"artefacto": ARTEFACTOS_SO[o["imagen"]][0], "version": ARTEFACTOS_SO[o["imagen"]][1],
                         "igual": False if (o.get("sha256") and gen) else None}
    return o


def ficha(cliente, e):
    """El estado completo de la máquina, como una sola configuración (lo que se guarda en un punto de restauración)."""
    if (e.get("categoria") or "") == "clúster openshift":
        return ficha_ocp(cliente, e)
    from .identidad import NOMBRES_PLAT
    from .inventario import skills_de
    from .paquetes import PAQ_PLAT, de_equipo
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    sk = skills_de(e) if isinstance(e.get("skills_auto"), str) or e.get("skills_auto") is not None else (e.get("skills") or [])
    # 1. sistema y su imagen de origen
    o = origen_de(cliente, e, d)
    sistema = {"so": e.get("so") or "", "kernel": d.get("kernel") or "", "origen": o}
    # 2. paquetes exactos (y su respaldo en el registro propio)
    pe = de_equipo(d, sk) or {}
    lista = sorted(f"{x['n']}|{x['v']}|{x['a']}|{x.get('r') or ''}|{'1' if x.get('respaldo') else ('0' if x.get('respaldo') is not None else '')}"
                   f"|{'base' if x.get('b') else ('local' if x.get('local') else 'repo')}|{x.get('ho') or ''}|{x.get('hr') or ''}" for x in (pe.get("lista") or []))
    paquetes = {"total": len(lista), "respaldados": pe.get("respaldados", 0), "registro": "repositorio/rpm", "inventario": pe.get("fecha"),
                "huella": hashlib.sha256("\n".join("|".join(x.split("|")[:3]) for x in lista).encode()).hexdigest()[:16] if lista else "", "lista": lista}
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
        elif s in INSTALADORES:   # sin inventario de paquetes: cómo la instala su skill de FaceOps (fuente fija y conocida)
            met, fuente, rx = INSTALADORES[s]
            try:
                from .versiones import de as _ver_de
                ver_ = (_ver_de(e, s) or {}).get("version") or ""
            except Exception:  # noqa: BLE001
                ver_ = ""
            plats.append({"plataforma": NOMBRES_PLAT.get(s) or s, "skill": s, "metodo": met, "version": ver_, "artefactos": [],
                          "respaldado": None, "repositorio": fuente})
        else:
            plats.append({"plataforma": NOMBRES_PLAT.get(s) or s, "skill": s, "metodo": "sin identificar (zip, binario o del fabricante)",
                          "version": "", "artefactos": [], "respaldado": None})
    # 4. configuración: commit del respaldo continuo
    # fuente principal: el repositorio propio de la máquina (rama master), que llena el inventario diario sin agente;
    # si además tiene el agente de respaldo continuo, se anota su último commit (recuperación al minuto)
    conf = {"repositorio": "repositorio/respaldo-servidores", "rama": e["nombre"], "commit": "", "fecha": "", "titulo": "", "fuente": ""}
    gm = d.get("git") or {}
    if gm.get("repo"):
        cs = _git("/commits", repo=gm["repo"], sha=gm.get("rama") or "master", limit=1, stat="false", files="false") or []
        conf.update(repositorio=gm["repo"], rama=gm.get("rama") or "master", fuente="inventario diario")
        if cs:
            conf.update(commit=cs[0]["sha"], fecha=cs[0]["commit"]["author"]["date"], titulo=cs[0]["commit"]["message"].strip().splitlines()[0])
    if "respaldo-git" in sk:
        cs = _git("/commits", sha=e["nombre"], limit=1, stat="false", files="false") or []
        if cs:
            ag_ = {"repositorio": "repositorio/respaldo-servidores", "rama": e["nombre"], "commit": cs[0]["sha"], "fecha": cs[0]["commit"]["author"]["date"],
                   "titulo": cs[0]["commit"]["message"].strip().splitlines()[0]}
            if conf["commit"]:
                conf["continuo"] = ag_
            else:
                conf.update(ag_, fuente="respaldo continuo (agente)")
    # 2b. sin inventario de paquetes: la lista exacta que guardó Git (agente o instantánea) en ese commit
    if not lista:
        pg_ = paquetes_de_git(conf)
        if pg_:
            lista = pg_["lista"]
            paquetes.update(pg_)
    # 5. aplicaciones (registro del respaldo continuo en ese commit)
    apps = []
    c_apps = (conf.get("continuo") or {}).get("commit") or (conf["commit"] if conf["repositorio"].endswith("respaldo-servidores") else "")
    if c_apps:
        for ln in _git_archivo(c_apps, "meta/aplicaciones.txt").splitlines():
            p = [x.strip() for x in ln.split("|")]
            if p and p[0]:
                apps.append({"aplicacion": p[0], "version": p[1] if len(p) > 1 else "", "artefacto": p[2] if len(p) > 2 else ""})
    # 6. datos: respaldo con restic (lo registra la automatización de respaldos)
    datos = (d.get("respaldo_datos") or {"herramienta": "restic", "snapshot": "", "fecha": ""})
    datos = dict(datos, volumenes=volumenes(cliente, e, datos))
    return {"maquina": {"nombre": e["nombre"], "ip": e.get("ip") or "", "categoria": e.get("categoria") or "", "ubicacion": e.get("ubicacion") or "",
                        "ambiente": e.get("ambiente") or ""},
            "sistema": sistema, "paquetes": paquetes, "plataformas": plats, "configuracion": conf, "aplicaciones": apps, "datos": datos,
            "cobertura": evaluar_cobertura((d.get("git") or {}).get("cobertura"), datos["volumenes"], sistema.get("origen") or {})}


def estado_ficha(f):
    """[(eslabón, ok|parcial|falta, detalle)] de la cadena de recuperación."""
    if f.get("tipo") == "openshift":
        return estado_ocp(f)
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
    if f.get("tipo") == "openshift":
        return hashlib.sha256(json.dumps(f.get("ocp"), sort_keys=True, default=str).encode()).hexdigest()[:16]
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


def dueno_git():
    """Usuario de Gitea dueño del repositorio de puntos (el de la credencial de respaldo)."""
    if "dueno" in _cache:
        return _cache["dueno"]
    try:
        u = requests.get(f"{GITEA}/api/v1/user", headers=_h(), timeout=8).json().get("login") or "repositorio"
    except Exception:  # noqa: BLE001
        u = "repositorio"
    _cache["dueno"] = u
    return u


def publicar(cod, f):
    """Versión legible del punto en Git (repositorio/puntos-restauracion), para auditoría."""
    try:
        import base64
        import yaml
        tok = _tok()
        if not tok:
            return
        h = {"Authorization": f"token {tok}"}
        if requests.get(f"{GITEA}/api/v1/repos/{dueno_git()}/puntos-restauracion", headers=h, timeout=8).status_code == 404:
            requests.post(f"{GITEA}/api/v1/user/repos", headers=h, json={"name": "puntos-restauracion", "private": True, "auto_init": True,
                                                                         "default_branch": "main", "description": "Puntos de restauración de FaceOps"}, timeout=10)
        doc = dict(f, punto=cod)
        txt = f"# Punto de restauración {cod} · FaceOps\n" + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=140)
        ruta = f"{f['maquina']['nombre']}/{cod}.yml"
        requests.post(f"{GITEA}/api/v1/repos/{dueno_git()}/puntos-restauracion/contents/{ruta}", headers=h, timeout=15,
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
        out.append(dict(p, f=f, estado=estado_ficha(f), cambio=_cambios_eslabon(ant, f),
                        diferencias=_diferencias(ant, f) if ant else ["primer punto de la máquina"]))
    # clúster OpenShift: un punto de una versión anterior ya no se puede restaurar (una actualización no tiene vuelta atrás)
    e = q1("SELECT datos FROM equipos WHERE id=?", equipo_id)
    actual = ((j(e["datos"], {}) if e else {}).get("ocp") or {}).get("version", "")
    for p in out:
        if p["f"].get("tipo") == "openshift":
            v = ((p["f"].get("ocp") or {}).get("version") or {}).get("version", "")
            if actual and v and v != actual:
                p["bloqueado"] = f"El clúster se actualizó de {v} a {actual}: una actualización de OpenShift no tiene vuelta atrás, este punto ya no se puede restaurar"
    return out


def _cambios_eslabon(a, b):
    """[bool] por eslabón (sistema, paquetes, plataformas, configuración, aplicaciones, datos): cuál cambió respecto del punto anterior."""
    if (b or {}).get("tipo") == "openshift":
        if not a or a.get("tipo") != "openshift":
            return [True] * len(ESLABONES_OCP)
        return [json.dumps((a["ocp"] or {}).get(k), sort_keys=True) != json.dumps((b["ocp"] or {}).get(k), sort_keys=True) for k, _ in ESLABONES_OCP]
    if not a:
        return [True] * 6
    sis = lambda f: (f["sistema"].get("so"), f["sistema"].get("kernel"), (f["sistema"].get("origen") or {}).get("sha256") or (f["sistema"].get("origen") or {}).get("imagen"))
    paq = lambda f: sorted("|".join(x.split("|")[:3]) for x in f["paquetes"].get("lista") or [])
    pla = lambda f: sorted((x["plataforma"], x["version"]) for x in f.get("plataformas") or [])
    hay_paq = bool(a["paquetes"].get("lista")) and bool(b["paquetes"].get("lista"))   # sin inventario previo no hay cambio que marcar
    return [sis(a) != sis(b), hay_paq and paq(a) != paq(b), pla(a) != pla(b), (a.get("configuracion") or {}).get("commit") != (b.get("configuracion") or {}).get("commit"),
            sorted((x["aplicacion"], x["version"]) for x in a.get("aplicaciones") or []) != sorted((x["aplicacion"], x["version"]) for x in b.get("aplicaciones") or []),
            (a.get("datos") or {}).get("snapshot") != (b.get("datos") or {}).get("snapshot")]


def _diferencias(a, b):
    out = []
    if b.get("tipo") == "openshift" or a.get("tipo") == "openshift":
        if a.get("tipo") != b.get("tipo"):
            return ["primer punto con formato de clúster"]
        va, vb = a["ocp"]["version"].get("version"), b["ocp"]["version"].get("version")
        if va != vb:
            out.append(f"versión: {va} → {vb} (actualización: no tiene vuelta atrás)")
        for k, t in ESLABONES_OCP[1:]:
            if json.dumps(a["ocp"].get(k), sort_keys=True) != json.dumps(b["ocp"].get(k), sort_keys=True):
                out.append(f"{t.lower()}: cambió")
        return out or ["sin cambios"]
    if (a["sistema"].get("so"), a["sistema"].get("kernel")) != (b["sistema"].get("so"), b["sistema"].get("kernel")):
        out.append(f"sistema: {a['sistema'].get('so')} {a['sistema'].get('kernel')} → {b['sistema'].get('so')} {b['sistema'].get('kernel')}")
    pa, pb = {"|".join(x.split("|")[:3]) for x in a["paquetes"].get("lista") or []}, {"|".join(x.split("|")[:3]) for x in b["paquetes"].get("lista") or []}
    if pa and pb and pa != pb:
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


# ------------------------------------------------------------------ creación automática (cada hora o cada día)
FRECUENCIAS = {"hora": 3600, "dia": 86400, "no": None}
ETQ_FREC = {"hora": "cada hora", "dia": "cada día", "no": "solo manual"}


def frecuencia(e):
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    return d.get("puntos_frecuencia") or "dia"


def programados():
    """Crea los puntos que tocan según la frecuencia de cada máquina (lo llama un hilo de fondo cada 5 minutos)."""
    from datetime import datetime
    iniciar()
    for c in q("SELECT * FROM clientes WHERE activo=1"):
        for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", c["id"]):
            fr = frecuencia(e)
            seg = FRECUENCIAS.get(fr)
            if not seg:
                continue
            ult = q1("SELECT creado FROM puntos_restauracion WHERE cliente_id=? AND equipo_id=? ORDER BY id DESC LIMIT 1", c["id"], e["id"])
            try:
                edad = (datetime.now() - datetime.fromisoformat(str(ult["creado"])[:19].replace("T", " "))).total_seconds() if ult else None
            except ValueError:
                edad = None
            if edad is None or edad >= seg - 60:
                try:
                    crear(c, e, origen=f"automático · {ETQ_FREC[fr]}", usuario="FaceOps")
                except Exception as x:  # noqa: BLE001
                    print("Punto automático", e["nombre"], str(x)[:160])


def inventarios_diarios():
    """Una vez al día (04:00) inventaría las máquinas con frecuencia «cada día» (la opción por defecto)."""
    from .inventariado import AUTOMATIZACION
    from .operacion import solicitar
    for c in q("SELECT * FROM clientes WHERE activo=1 AND aap_url!=''"):
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", c["id"], AUTOMATIZACION)
        u = q1("SELECT * FROM usuarios WHERE usuario='cquezada'") or q1("SELECT * FROM usuarios ORDER BY id LIMIT 1")
        if not a or not u:
            continue
        for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado' AND ip!='' AND energia='encendido' "
                   "AND COALESCE(categoria,'') IN ('', 'servidor', 'estación de trabajo') AND COALESCE(inventario,'')!='OpenShift · nodos'", c["id"]):
            d = j(e.get("datos"), {})
            if (d.get("inventario_frecuencia") or "dia") != "dia":
                continue
            try:
                solicitar(u, c, a, None, {"pq_respaldar": "si"}, f"Inventario diario de {e['nombre']}", None,
                          {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=e)
            except Exception as x:  # noqa: BLE001
                print("Inventario diario", e["nombre"], str(x)[:160])


def hilo():
    import threading

    def bucle():
        from datetime import datetime
        time.sleep(90)
        dia_inv = None
        while True:
            try:
                programados()
            except Exception as x:  # noqa: BLE001
                print("Puntos automáticos:", str(x)[:200])
            hoy = datetime.now()
            if hoy.hour == 4 and dia_inv != hoy.date():
                dia_inv = hoy.date()
                try:
                    inventarios_diarios()
                except Exception as x:  # noqa: BLE001
                    print("Inventarios diarios:", str(x)[:200])
            time.sleep(300)
    threading.Thread(target=bucle, daemon=True, name="faceops-puntos").start()
