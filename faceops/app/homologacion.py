"""Homologación de ambientes: la misma pieza (por ejemplo, el JBoss de un segmento) debe ser igual en desarrollo, QA y
producción, salvo lo que por diseño cambia entre ambientes (tamaño, direcciones). FaceOps compara la huella de entorno que
deja «Homologación · Relevar entorno» (datos.entorno de cada máquina) entre las máquinas de un mismo grupo y muestra cada
desviación con su severidad y la recomendación para alinearla con la referencia (producción, si existe).

Grupo homologable = segmento de negocio × plataforma (skills que no son de soporte). Una máquina sin segmento se agrupa
por su plataforma sola.
"""
import hashlib
import re

from .inventario import SOPORTE, skills_de
from .bd import j

ORDEN_AMB = {"desarrollo": 0, "dev": 0, "qa": 1, "test": 1, "certificacion": 2, "preproduccion": 2, "produccion": 3}
ETIQ_AMB = {"desarrollo": "Desarrollo", "qa": "QA", "certificacion": "Certificación", "preproduccion": "Preproducción", "produccion": "Producción"}

# clave → (etiqueta, categoría, política, severidad). Política: «igual» debe coincidir en todos los ambientes ·
# «tamaño» puede ser menor fuera de producción (informativo, salvo que un ambiente bajo supere a producción).
DIMENSIONES = [
    ("so.distribucion", "Distribución", "Sistema", "igual", "alta"),
    ("so.version", "Versión del sistema", "Sistema", "igual", "alta"),
    ("so.arquitectura", "Arquitectura", "Sistema", "igual", "alta"),
    ("so.kernel", "Kernel", "Sistema", "igual", "media"),
    ("paquetes.glibc", "glibc", "Sistema", "igual", "media"),
    ("paquetes.openssl", "OpenSSL", "Sistema", "igual", "alta"),
    ("recursos.vcpu", "vCPU", "Recursos", "tamaño", "baja"),
    ("recursos.ram_gb", "Memoria (GB)", "Recursos", "tamaño", "baja"),
    ("recursos.swap_gb", "Swap (GB)", "Recursos", "tamaño", "baja"),
    ("recursos.fs", "Sistemas de archivos", "Recursos", "tamaño", "baja"),
    ("entorno.zona_horaria", "Zona horaria", "Entorno", "igual", "media"),
    ("entorno.locale", "Idioma (locale)", "Entorno", "igual", "media"),
    ("entorno.ntp", "Hora sincronizada (NTP)", "Entorno", "igual", "media"),
    ("entorno.selinux", "SELinux", "Entorno", "igual", "alta"),
    ("entorno.firewall", "Firewall", "Entorno", "igual", "media"),
    ("entorno.puertos", "Puertos abiertos", "Entorno", "igual", "media"),
    ("entorno.ca_confianza", "Autoridades de confianza (CA)", "Entorno", "igual", "media"),
    ("sysctl.vm.swappiness", "vm.swappiness", "Kernel (sysctl)", "igual", "media"),
    ("sysctl.net.core.somaxconn", "net.core.somaxconn", "Kernel (sysctl)", "igual", "media"),
    ("sysctl.fs.file-max", "fs.file-max", "Kernel (sysctl)", "igual", "baja"),
    ("sysctl.net.ipv4.tcp_keepalive_time", "tcp_keepalive_time", "Kernel (sysctl)", "igual", "baja"),
    ("sysctl.net.ipv4.ip_local_port_range", "ip_local_port_range", "Kernel (sysctl)", "igual", "baja"),
    ("java.defecto", "Java por defecto", "Java", "igual", "alta"),
    ("java.instalados", "Java instalados", "Java", "igual", "media"),
    ("jboss.version", "Versión de JBoss", "JBoss", "igual", "alta"),
    ("jboss.parches", "Parches de JBoss", "JBoss", "igual", "alta"),
    ("jboss.home", "Ruta de instalación", "JBoss", "igual", "baja"),
    ("jboss.estado", "Estado", "JBoss", "igual", "alta"),
    ("jboss.usuario", "Usuario de servicio", "JBoss", "igual", "media"),
    ("jboss.java", "Java que usa JBoss", "JBoss", "igual", "alta"),
    ("jboss.modo", "Modo", "JBoss", "igual", "alta"),
    ("jboss.perfil", "Perfil de configuración", "JBoss", "igual", "alta"),
    ("jboss.jvm.xms", "Heap inicial (-Xms)", "JVM", "tamaño", "baja"),
    ("jboss.jvm.xmx", "Heap máximo (-Xmx)", "JVM", "tamaño", "media"),
    ("jboss.jvm.metaspace", "Metaspace máximo", "JVM", "tamaño", "baja"),
    ("jboss.jvm.gc", "Recolector de basura (GC)", "JVM", "igual", "media"),
    ("jboss.jvm.opciones", "Opciones de la JVM", "JVM", "igual", "media"),
    ("jboss.limite_archivos", "Límite de archivos abiertos", "JVM", "igual", "media"),
    ("jboss.limite_procesos", "Límite de procesos", "JVM", "igual", "baja"),
    ("jboss.modulos", "Módulos propios", "JBoss", "igual", "alta"),
    ("jboss.drivers", "Drivers JDBC", "JBoss", "igual", "alta"),
    ("jboss.datasources", "Datasources (JNDI)", "JBoss", "igual", "alta"),
    ("jboss.subsistemas", "Subsistemas", "JBoss", "igual", "media"),
    ("jboss.puertos", "Puertos de JBoss", "JBoss", "igual", "media"),
    ("jboss.deployments", "Aplicaciones desplegadas", "JBoss", "igual", "alta"),
    ("jboss.huella_config", "Configuración (sin contraseñas ni direcciones)", "JBoss", "igual", "alta"),
]
PESO = {"alta": 3, "media": 2, "baja": 1}


def _amb(e):
    a = (e.get("ambiente") or "").lower()
    return a if a in ORDEN_AMB else (a or "sin ambiente")


def _num(v):
    """Tamaños comparables: 4096m, 4g, 8 → MB."""
    m = re.match(r"^-?X(?:ms|mx)?(\d+)([kmgKMG]?)$|^-XX:MaxMetaspaceSize=(\d+)([kmgKMG]?)$|^(\d+)$", (v or "").strip())
    if not m:
        return None
    n, u = (m.group(1), m.group(2)) if m.group(1) else ((m.group(3), m.group(4)) if m.group(3) else (m.group(5), ""))
    return int(n) * {"k": 1 / 1024, "m": 1, "g": 1024, "": 1}[(u or "").lower()]


def grupos(eqs, segs):
    """Grupos homologables: máquinas de un mismo segmento y plataforma con huella relevada o en más de un ambiente."""
    from .inventario import segmentos_de
    out = {}
    for e in eqs:
        if e.get("estado") == "retirado" or (e.get("categoria") or "servidor") != "servidor":
            continue
        plat = sorted(s for s in skills_de(e) if s not in SOPORTE)
        if not plat:
            continue
        for sg in (segmentos_de(e, segs) or ["(sin segmento)"]):
            k = f"{sg} · {' + '.join(plat)}"
            out.setdefault(k, {"clave": k, "segmento": sg, "plataforma": " + ".join(plat), "equipos": []})["equipos"].append(e)
    res = []
    for g in out.values():
        ambs = {_amb(e) for e in g["equipos"]}
        if len(g["equipos"]) < 2 and not any(j(e.get("datos"), {}).get("entorno") for e in g["equipos"]):
            continue
        g["id"] = hashlib.sha1(g["clave"].encode()).hexdigest()[:10]
        g["equipos"].sort(key=lambda e: (ORDEN_AMB.get(_amb(e), 9), e["nombre"]))
        g["ambientes"] = sorted(ambs, key=lambda a: ORDEN_AMB.get(a, 9))
        g["faltan"] = [ETIQ_AMB[a] for a in ("desarrollo", "qa", "produccion") if a not in ambs]
        g.update(comparar(g["equipos"]))
        res.append(g)
    return sorted(res, key=lambda g: (g["indice"] is None, g["indice"] if g["indice"] is not None else 0, g["clave"]))


def comparar(eqs):
    """Matriz de homologación. Referencia: la máquina de producción (la primera si hay varias), o la última del orden."""
    hs = {e["id"]: (j(e.get("datos"), {}).get("entorno") or {}) for e in eqs}
    con = [e for e in eqs if hs[e["id"]]]
    if not con:
        return {"filas": [], "indice": None, "desviaciones": [], "sin_huella": [e["nombre"] for e in eqs], "ref": None}
    ref = next((e for e in con if _amb(e) == "produccion"), con[-1])
    filas, desv, puntos, total = [], [], 0, 0
    for clave, etiq, cat, pol, sev in DIMENSIONES:
        vals = {e["id"]: (hs[e["id"]].get(clave) or "").strip() for e in con}
        if not any(vals.values()):
            continue
        rv = vals[ref["id"]]
        celdas = []
        for e in eqs:
            if e["id"] not in vals:
                celdas.append({"v": None, "estado": "sin huella"})
                continue
            v = vals[e["id"]]
            if v == rv:
                est = "igual"
            elif pol == "tamaño":
                a, b = _num(v), _num(rv)
                est = "mayor que producción" if (a is not None and b is not None and a > b and _amb(e) != "produccion" and _amb(ref) == "produccion") else "tamaño distinto"
            else:
                est = "distinto" if v and rv else "falta"
            celdas.append({"v": v, "estado": est})
            if e["id"] != ref["id"] and est not in ("igual", "tamaño distinto"):
                s_ = sev if est != "mayor que producción" else "media"
                desv.append({"equipo": e["nombre"], "ambiente": ETIQ_AMB.get(_amb(e), _amb(e)), "dimension": etiq, "categoria": cat, "severidad": s_,
                             "valor": v or "—", "ref_valor": rv or "—",
                             "recomendacion": (f"Alinear {etiq.lower()} de {e['nombre']} con {ref['nombre']}: «{(v or '—')[:60]}» → «{(rv or '—')[:60]}»"
                                               if est != "mayor que producción" else f"{etiq} de {e['nombre']} supera a producción: revisar el dimensionamiento")})
        comparables = [c for c, e in zip(celdas, eqs) if c["v"] is not None and e["id"] != ref["id"]]
        if pol == "igual" and comparables:
            total += PESO[sev] * len(comparables)
            puntos += PESO[sev] * sum(1 for c in comparables if c["estado"] == "igual")
        filas.append({"clave": clave, "etiqueta": etiq, "categoria": cat, "politica": pol, "severidad": sev, "celdas": celdas,
                      "estado": "igual" if all(c["estado"] in ("igual", "sin huella") for c in celdas) else ("tamaño" if pol == "tamaño" and all(c["estado"] in ("igual", "tamaño distinto", "sin huella") for c in celdas) else "difiere")})
    desv.sort(key=lambda d: (-PESO[d["severidad"]], d["categoria"], d["equipo"]))
    return {"filas": filas, "indice": round(100 * puntos / total) if total else (100 if len(con) > 1 else None), "desviaciones": desv,
            "sin_huella": [e["nombre"] for e in eqs if not hs[e["id"]]], "ref": ref["nombre"],
            "medido": max((hs[e["id"]].get("medido") or "") for e in con)}
