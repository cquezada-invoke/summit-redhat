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


# Cómo se salta entre versiones: «uno_a_uno» pasa por cada versión del catálogo, «directo» va a la destino de una vez,
# «max:N» avanza hasta N versiones por salto. Se puede fijar por producto en datos/ciclo-vida.yml (salto, salto_nota).
SALTOS = {
    "rhel": ("uno_a_uno", "Actualización en sitio con Leapp: una versión mayor por salto, con reinicio y validación entre cada uno"),
    "centos": ("uno_a_uno", "Primero Convert2RHEL a la misma versión de RHEL; después Leapp, una versión mayor por salto"),
    "openshift": ("uno_a_uno", "El operador del clúster actualiza una versión menor por salto (entre EUS se encadenan con los workers en pausa)"),
    "ubuntu": ("uno_a_uno", "do-release-upgrade de LTS a LTS, sin saltarse ninguna"),
    "windows": ("max:2", "Actualización en sitio hasta dos versiones por salto; si no, instalación nueva y migración de roles"),
    "jboss-eap": ("directo", "Migración de las aplicaciones a la versión destino (EAP 8: javax → jakarta)"),
    "postgresql": ("directo", "pg_upgrade lleva los datos directo a la versión mayor destino"),
    "java": ("directo", "Se instala el JDK destino y se recompila y prueba la aplicación"),
    "tomcat": ("directo", "Instalación nueva y despliegue de la aplicación (Tomcat 10+ usa jakarta)"),
    "redis": ("directo", "Réplica o RDB: la versión nueva lee los datos de la anterior; se cambia el tráfico a la réplica"),
    "vmware-vsphere": ("uno_a_uno", "vCenter primero y después los hosts, una versión mayor por salto"),
}


def ruta_actualizacion(p, v, hoy=None):
    """Versiones por las que hay que pasar desde v hasta la destino (la más nueva con soporte vigente), con el estado de cada una."""
    prod = datos().get(p, {})
    vs = sorted((prod.get("versiones") or {}).keys(), key=_orden)
    if v not in vs:
        return None
    tipo, nota = SALTOS.get(p, ("uno_a_uno", "Una versión por salto"))
    tipo, nota = prod.get("salto", tipo), prod.get("salto_nota", nota)
    est = {k: evaluar_producto(p, k, hoy) for k in vs}
    vigentes = [k for k in vs if est[k]["fase"] not in ("fuera", "desconocido")]
    destino = max(vigentes, key=_orden) if vigentes else vs[-1]
    if _orden(destino) <= _orden(v):
        return {"pasos": [dict(est[v], rol="actual")], "saltos": 0, "nota": nota, "tipo": tipo, "al_dia": True, "versiones": [], "destino": v}
    medio = [k for k in vs if _orden(v) < _orden(k) < _orden(destino)]
    if tipo == "directo":
        medio = []
    elif str(tipo).startswith("max:"):
        n = max(1, int(str(tipo).split(":")[1]))
        medio = [k for i, k in enumerate(medio, 1) if i % n == 0]
    pasos = [dict(est[v], rol="actual")] + [dict(est[k], rol="intermedia") for k in medio] + [dict(est[destino], rol="destino")]
    # todas las versiones más nuevas: activas solo las alcanzables desde la actual en un salto (destinos posibles)
    nuevas = [k for k in vs if _orden(k) > _orden(v)]
    if tipo == "directo":
        alc = set(nuevas)
    elif str(tipo).startswith("max:"):
        alc = set(nuevas[:max(1, int(str(tipo).split(":")[1]))])
    else:
        alc = set(nuevas[:1])
    en_ruta = {x["version"] for x in pasos}
    versiones = [dict(est[k], alcanzable=k in alc, en_ruta=k in en_ruta, recomendado=k == destino,
                      sin_soporte=est[k]["fase"] == "fuera") for k in nuevas]
    return {"pasos": pasos, "saltos": len(pasos) - 1, "nota": nota, "tipo": tipo, "al_dia": False, "versiones": versiones,
            "destino": destino}


# Automatización con la que se evalúa un destino, por plataforma (si no está, «Ciclo de vida · Evaluar destino»)
EVALUAR_DESTINO = {
    "rhel": ("Migración · Evaluar sistema operativo", lambda v: {"mig_destino": str(v).split(".")[0], "mig_preupgrade": "true"}),
    "centos": ("Migración · Evaluar sistema operativo", lambda v: {"mig_destino": str(v).split(".")[0], "mig_preupgrade": "true"}),
    "jboss-eap": ("Migración · JBoss a EAP 8", lambda v: {"mj_modo": "evaluar"}),
    "openshift": ("OpenShift · Versión del clúster", lambda v: {"ov_modo": "evaluar"}),
}


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
            nombres_ = {sv.get("nombre") for sv in det.get("servidores") or [] if isinstance(sv, dict)}
            sobre = [e for e in equipos if e.get("ubicacion") == x["ubicacion"] or e["nombre"] in nombres_
                     or (x["tipo"] == "openshift" and e.get("categoria") == "clúster openshift")]
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
