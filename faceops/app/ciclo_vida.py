"""Ciclo de vida: en qué fase de soporte del fabricante está cada equipo (sistema operativo o plataforma).
Los datos del equipo vienen de AAP (recolector y «OpenShift · Recolectar clúster»); las fechas, de datos/ciclo-vida.yml."""
import os
import re
from datetime import date

import yaml

_DATOS = None
FASES = {"completo": ("Soporte completo", "s-ok"), "mantencion": ("Mantención", "s-media"),
         "extendido": ("Solo soporte extendido", "s-alta"), "fuera": ("Fuera de soporte", "s-alta"), "desconocido": ("Sin datos de ciclo de vida", "")}
UMBRAL_DIAS = 180


def datos():
    global _DATOS
    if _DATOS is None:
        _DATOS = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "datos", "ciclo-vida.yml"), encoding="utf-8"))["productos"]
    return _DATOS


def producto_de(e):
    """(clave, versión del ciclo) a partir del sistema o plataforma del equipo."""
    so = (e.get("so") or "").strip()
    s = so.lower()
    if e.get("categoria") == "clúster openshift" or s.startswith("openshift"):
        m = re.search(r"(\d+\.\d+)", so)
        return ("openshift", m.group(1)) if m else ("openshift", "")
    if "red hat" in s or "redhat" in s or s.startswith("rhel"):
        m = re.search(r"(\d+)", so)
        return "rhel", m.group(1) if m else ""
    if "centos" in s:
        m = re.search(r"(\d+)", so)
        return "centos", m.group(1) if m else ""
    if "ubuntu" in s:
        m = re.search(r"(\d+\.\d+)", so)
        return "ubuntu", m.group(1) if m else ""
    if "windows" in s:
        m = re.search(r"(20\d\d)", so)
        return "windows", m.group(1) if m else ""
    return None, ""


def evaluar(e, hoy=None):
    p, v = producto_de(e)
    if not p:
        return None
    return evaluar_producto(p, v, hoy, (e.get("_datos") or {}).get("registrado"))


def version_ciclo(p, version):
    """La versión del ciclo de vida que corresponde a una versión instalada (8.0.3 → 8.0; 15.6 → 15; 17.0.12 → 17)."""
    vs = (datos().get(p) or {}).get("versiones") or {}
    nums = re.findall(r"\d+", str(version))
    for n in (2, 1):
        k = ".".join(nums[:n])
        if k in vs:
            return k
    return ".".join(nums[:2]) or str(version)


def evaluar_producto(p, v, hoy=None, registrado=None):
    hoy = hoy or date.today()
    prod = datos().get(p, {})
    ver = (prod.get("versiones") or {}).get(v)
    fila = {"producto": p, "nombre": prod.get("nombre", p), "version": v, "fuente": prod.get("fuente", ""), "fase": "desconocido",
            "fin_fase": None, "fin_soporte": None, "dias": None, "siguiente": "", "registrado": registrado}
    if not ver:
        return fila
    fechas = {k: (x if isinstance(x, date) else date.fromisoformat(str(x))) for k, x in ver.items()}
    fin_total = fechas.get("extendido") or fechas.get("mantencion")
    fila["fin_soporte"] = fin_total
    for fase in ("completo", "mantencion", "extendido"):
        if fase in fechas and hoy <= fechas[fase]:
            fila.update(fase=fase, fin_fase=fechas[fase], dias=(fechas[fase] - hoy).days)
            break
    else:
        fila.update(fase="fuera", dias=(fin_total - hoy).days if fin_total else None)
    # versión vigente más nueva del mismo producto: a la que conviene ir
    nuevas = [k for k, x in prod["versiones"].items() if _orden(k) > _orden(v)]
    fila["siguiente"] = max(nuevas, key=_orden) if nuevas else ""
    fila["riesgo"] = fila["fase"] in ("fuera", "extendido") or (fila["dias"] is not None and fila["dias"] <= UMBRAL_DIAS and fila["fase"] != "completo")
    return fila


def _orden(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))


SKILL_PRODUCTO = {"jboss": "jboss-eap", "postgres": "postgresql", "redis": "redis", "tomcat": "tomcat", "keycloak": "keycloak", "kafka": "kafka",
                  "nginx": "nginx", "httpd": "httpd", "mysql": "mysql", "activemq": "activemq", "red-hat-idm": "idm", "389-directory-server": "rhds",
                  "midpoint": "midpoint", "active-directory": "active-directory", "vault": "vault", "rabbitmq": "rabbitmq", "iis": "iis",
                  "satellite": "satellite", "aap": "aap", "certificate-system": "rhcs", "gitea": "gitea"}
CLASES = [("so", "Sistemas operativos"), ("plataforma", "Plataformas"), ("infraestructura", "Infraestructuras")]
SIN_VERSION = "s-v"


def _stack_versiones(cliente_id):
    """{(equipo, producto): versión} del último barrido «Arquitectura · Evaluar stack tecnológico» (vía AAP)."""
    import json
    from .bd import q1
    out = {}
    try:
        r = q1("SELECT datos FROM stack_eval WHERE cliente_id=? ORDER BY id DESC LIMIT 1", cliente_id) if cliente_id else None
    except Exception:  # noqa: BLE001
        r = None
    for f in (json.loads(r["datos"]).get("filas") if r else []) or []:
        if f.get("tipo") == "sistema operativo":
            continue
        for eq in f.get("equipos") or []:
            out[(eq, _prod_por_nombre(f))] = f.get("version") or ""
    return out


def _prod_por_nombre(f):
    for k, v in datos().items():
        if v.get("nombre") == f.get("nombre"):
            return k
    return {"JBoss EAP": "jboss-eap", "PostgreSQL": "postgresql", "Redis / Valkey": "redis", "Apache Tomcat": "tomcat", "nginx": "nginx",
            "Apache httpd": "httpd", "Kafka / AMQ Streams": "kafka", "Keycloak / RHBK": "keycloak", "MariaDB / MySQL": "mysql"}.get(f.get("nombre"), f.get("nombre", ""))


def panel(equipos, hoy=None, cliente_id=None):
    """Sistemas operativos, plataformas e infraestructuras del cliente con su fase de soporte, agrupados por producto y versión."""
    import json
    from .identidad import NOMBRES_PLAT
    filas = []
    for e in equipos:
        if e.get("categoria") == "clúster openshift":
            continue   # el clúster se evalúa como infraestructura
        ev = evaluar(e, hoy)
        if ev:
            filas.append(dict(ev, equipo=e, clase="so"))
    # plataformas: las que corren en cada máquina (skills); la versión, del barrido del stack o de la lectura de versiones
    stack = _stack_versiones(cliente_id)
    for e in equipos:
        sk = e.get("skills")
        sk = json.loads(sk) if isinstance(sk, str) else (sk or [])
        dat = e.get("_datos") or {}
        for s_ in dict.fromkeys(sk):
            if s_ not in SKILL_PRODUCTO:
                continue
            p = SKILL_PRODUCTO[s_]
            v = stack.get((e["nombre"], p)) or ((dat.get("versiones") or {}).get(s_) or {}).get("version") or ""
            v = version_ciclo(p, v) if v and p in datos() else (v or SIN_VERSION)
            ev = evaluar_producto(p, v, hoy) if p in datos() else {"producto": p, "nombre": NOMBRES_PLAT.get(s_) or s_.capitalize(), "version": v,
                                                                   "fuente": "", "fase": "desconocido", "fin_fase": None, "fin_soporte": None,
                                                                   "dias": None, "siguiente": "", "registrado": None}
            if ev["nombre"] == p:
                ev["nombre"] = NOMBRES_PLAT.get(s_) or s_.capitalize()
            filas.append(dict(ev, equipo=e, clase="plataforma", registrado=None))
    # infraestructuras: hipervisores, nubes y clústeres; sus equipos son las máquinas que corren sobre ella
    if cliente_id:
        from .bd import q
        try:
            infras = q("SELECT * FROM infraestructuras WHERE cliente_id=?", cliente_id)
        except Exception:  # noqa: BLE001
            infras = []
        for x in infras:
            det = {}
            try:
                det = json.loads(x["detalle"] or "{}")
            except (ValueError, TypeError):
                pass
            p = {"openshift": "openshift", "vmware": "vmware-vsphere", "kvm": "kvm", "gcp": "gcp", "fisico": "hardware"}.get(x["tipo"], x["tipo"])
            v = str(det.get("version") or "")
            v = version_ciclo(p, v) if v and p in datos() else (v or SIN_VERSION)
            sobre = [e for e in equipos if e.get("ubicacion") == x["ubicacion"]]
            base = evaluar_producto(p, v, hoy) if p in datos() else {"producto": p, "version": v, "fuente": "", "fase": "desconocido", "fin_fase": None,
                                                                     "fin_soporte": None, "dias": None, "siguiente": ""}
            base = dict(base, nombre=x["nombre"], producto=p, infra=x["ubicacion"],
                        nota="servicio de nube: el proveedor lo mantiene al día" if x["tipo"] == "gcp" else "")
            for e in sobre or [None]:
                filas.append(dict(base, equipo=e, clase="infraestructura", registrado=None))
    grupos = {}
    for f in filas:
        grupos.setdefault((f["clase"], f["producto"], f["version"], f.get("infra", "")), []).append(f)
    lista = []
    for (cl, p, v, inf), fs in grupos.items():
        f0 = fs[0]
        eqs_ = [x["equipo"] for x in fs if x["equipo"]]
        lista.append({k: f0.get(k) for k in ("producto", "nombre", "version", "fuente", "fase", "fin_fase", "fin_soporte", "dias", "siguiente")}
                     | {"clase": cl, "infra": inf, "nota": f0.get("nota", ""), "riesgo": f0.get("riesgo", False), "equipos": eqs_,
                        "sin_registro": [x["equipo"] for x in fs if x["equipo"] and x.get("registrado") is False],
                        "registro": {x["equipo"]["id"]: x.get("registrado") for x in fs if x["equipo"]}})
    orden = {"fuera": 0, "extendido": 1, "mantencion": 2, "completo": 3, "desconocido": 4}
    lista.sort(key=lambda g: (orden[g["fase"]], g["dias"] if g["dias"] is not None else 99999, g["nombre"]))
    cuenta = {k: sum(len(g["equipos"]) for g in lista if g["fase"] == k) for k in orden}
    return {"grupos": lista, "cuenta": cuenta, "total": sum(len(g["equipos"]) for g in lista), "umbral": UMBRAL_DIAS,
            "sin_registro": sum(len(g["sin_registro"]) for g in lista),
            "por_clase": {c: sum(1 for g in lista if g["clase"] == c) for c, _ in CLASES},
            "proximos": sum(len(g["equipos"]) for g in lista if g["dias"] is not None and 0 <= g["dias"] <= UMBRAL_DIAS)}
