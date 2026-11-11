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
CREATE TABLE IF NOT EXISTS cves_base (
  cliente_id INTEGER NOT NULL, equipo_id INTEGER NOT NULL, cve TEXT NOT NULL, severidad TEXT DEFAULT '', desde TEXT,
  PRIMARY KEY(cliente_id, equipo_id, cve));
CREATE TABLE IF NOT EXISTS cves_revision (
  cliente_id INTEGER NOT NULL, equipo_id INTEGER NOT NULL, repos INTEGER, pendientes INTEGER, cubiertos INTEGER, cuando TEXT, ticket_id INTEGER,
  PRIMARY KEY(cliente_id, equipo_id));
"""


def iniciar():
    from .bd import con
    con().executescript(ESQUEMA)


def registrar(aap, cliente, t):
    """Resultado de «Revisar CVEs»: reemplaza lo conocido de cada equipo revisado."""
    from .operacion import artefactos_ticket   # también si corrió dentro del flujo de aprobación (workflow)
    art = artefactos_ticket(aap, t)
    if not q1("SELECT 1 FROM cves_base WHERE cliente_id=? LIMIT 1", cliente["id"]):
        sembrar_base(aap, cliente)
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
        _a_base(cliente["id"], e["id"], r.get("pendientes") or [])
        ej("INSERT INTO cves_revision(cliente_id, equipo_id, repos, pendientes, cubiertos, cuando, ticket_id) VALUES (?,?,?,?,?,?,?) "
           "ON CONFLICT(cliente_id, equipo_id) DO UPDATE SET repos=excluded.repos, pendientes=excluded.pendientes, cubiertos=excluded.cubiertos, "
           "cuando=excluded.cuando, ticket_id=excluded.ticket_id",
           cliente["id"], e["id"], int(bool(r.get("repos"))), len({x.split("|")[0] for x in r.get("pendientes") or []}),
           len({x.split("|")[0] for x in r.get("cubiertos") or []}), ahora(), t["id"])
        lineas.append(f"{n}: " + (f"{len(r.get('pendientes') or [])} pendientes · {len(r.get('cubiertos') or [])} cubiertos" if r.get("repos")
                                  else "sin repositorios Red Hat (no se puede evaluar)"))
    return "CVE: " + " · ".join(lineas)


def _a_base(cliente_id, equipo_id, filas, cuando=None):
    """Línea base de exposición: todo CVE que alguna vez estuvo pendiente en la máquina (desde su primera revisión)."""
    for f in filas:
        cve, sev = (f.split("|") + [""])[:2]
        ej("INSERT OR IGNORE INTO cves_base(cliente_id, equipo_id, cve, severidad, desde) VALUES (?,?,?,?,?)",
           cliente_id, equipo_id, cve, SEV.get(sev, sev), cuando or ahora())


def sembrar_base(aap, cliente):
    """Primera vez: arma la línea base con los pendientes de todas las revisiones anteriores (artefactos en AAP)."""
    from .operacion import artefactos_ticket
    n = 0
    for t in q("SELECT t.* FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND a.nombre_aap=? "
               "AND t.estado='exitoso' ORDER BY t.id", cliente["id"], REVISAR):
        try:
            res = artefactos_ticket(aap, t).get("faceops_cves") or {}
        except Exception:  # noqa: BLE001 - trabajo purgado en AAP: se sigue con los demás
            continue
        for nombre, r in res.items():
            e = q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], nombre)
            if e:
                _a_base(cliente["id"], e["id"], r.get("pendientes") or [], t["fin"] or t["creado"])
                n += 1
    return n


def por_equipo(cliente_id, eqs):
    """{equipo_id: {revisado, repos, pendientes, cubiertos, criticos, importantes, cuando, top: [...]}}"""
    rev = {r["equipo_id"]: r for r in q("SELECT * FROM cves_revision WHERE cliente_id=?", cliente_id)}
    pend, cub = {}, {}
    ids_ = {e["id"] for e in eqs}
    for r in q("SELECT equipo_id, cve, severidad, paquete, estado FROM cves_equipo WHERE cliente_id=?", cliente_id):
        if r["equipo_id"] not in ids_:
            continue
        (pend if r["estado"] == "pendiente" else cub).setdefault(r["equipo_id"], {}).setdefault(r["cve"], r)
    base = {}
    try:
        for r in q("SELECT equipo_id, cve, severidad FROM cves_base WHERE cliente_id=?", cliente_id):
            if r["equipo_id"] in ids_:
                base.setdefault(r["equipo_id"], {})[r["cve"]] = r
    except Exception:  # noqa: BLE001 - tabla aún no creada
        pass
    out = {}
    for e in eqs:
        r = rev.get(e["id"])
        ps = sorted(pend.get(e["id"], {}).values(), key=lambda x: (ORDEN.get(x["severidad"], 9), x["cve"]))
        out[e["id"]] = {"revisado": bool(r), "repos": bool(r and r["repos"]), "pendientes": len(ps), "cubiertos": r["cubiertos"] if r else 0,
                        "criticos": len([x for x in ps if x["severidad"] == "crítica"]), "importantes": len([x for x in ps if x["severidad"] == "importante"]),
                        "cuando": r["cuando"] if r else None, "top": ps[:8],
                        # por severidad: CVE distintos pendientes y detectados (pendientes + corregidos), para el puntaje proporcional
                        "sev": _por_sev(pend.get(e["id"], {}), base.get(e["id"], {})), "criterio": CRITERIO,
                        "act": actualizacion(e, pend.get(e["id"], {}).values()),
                        # todos los CVE reportados para su sistema: pendientes primero (por severidad) y los resueltos marcados ok
                        "todos": [dict(x, ok=False) for x in ps] + [dict(x, ok=True) for x in sorted(
                            (v for k, v in cub.get(e["id"], {}).items() if k not in pend.get(e["id"], {})),
                            key=lambda x: (ORDEN.get(x["severidad"], 9), x["cve"]))]}
    return out


# paquetes que exigen reiniciar al actualizarse (los mismos que mira needs-restarting -r)
REINICIO = ("kernel", "kernel-core", "kernel-rt", "kernel-modules", "glibc", "linux-firmware", "systemd", "systemd-udev", "udev",
            "openssl-libs", "gnutls", "dbus", "dbus-broker", "microcode_ctl")


def actualizacion(e, filas):
    """Qué deja «Aplicar todos los parches de seguridad»: versión actual → versión menor resultante (la del flujo de los paquetes
    que corrigen los CVE pendientes: .el9_8 = 9.8; si el release estuviera fijado, los repositorios solo ofrecerían esa menor),
    cuántos paquetes cambian y si exige reiniciar. Dentro de la misma versión mayor: 9 → 10 es Modernización."""
    import re
    m = re.search(r"(\d+)(?:\.(\d+))?", str(e.get("so") or ""))
    may, men = (int(m.group(1)), int(m.group(2) or 0)) if m else (None, None)
    pkgs = {x["paquete"] for x in filas if x.get("paquete")}
    if not pkgs:
        return None
    nombres = {p.rsplit("-", 2)[0] if p.count("-") >= 2 else p for p in pkgs}
    menores = [int(b) for p in pkgs for a, b in re.findall(r"\.el(\d+)_(\d+)", p) if may is None or int(a) == may]
    dest = max([men or 0] + menores) if may is not None else None
    return {"actual": f"{may}.{men}" if may is not None else (e.get("so") or "—"), "destino": f"{may}.{dest}" if may is not None else None,
            "sube": may is not None and dest > (men or 0), "paquetes": len(nombres), "familia": "RHEL" if re.search(r"red ?hat|rhel", str(e.get("so") or ""), re.I) else (str(e.get("so") or "").split(" ")[0] or "SO"),
            "nombres": sorted(nombres)[:6],
            "reinicio": any(n in REINICIO for n in nombres)}


def _por_sev(pend, base):
    """{severidad: {p: pendientes, t: detectados desde la línea base}} (CVE distintos)."""
    out = {}
    for k, x in list(base.items()) + [(k, v) for k, v in pend.items() if k not in base]:
        sv = x["severidad"] if x["severidad"] in ("crítica", "importante", "moderada") else "baja"
        d = out.setdefault(sv, {"p": 0, "t": 0})
        d["t"] += 1
        d["p"] += k in pend
    return out


# Puntaje de la sección CVE (pilar Seguridad): % de la exposición ya corregida desde la línea base de la máquina (todo CVE que
# estuvo pendiente desde su primera revisión), ponderada por severidad. Cada CVE corregido suma su peso.
PESOS = {"crítica": 10, "importante": 5, "moderada": 2, "baja": 1}
CRITERIO = ("CVE: % de la exposición corregida desde la línea base de la máquina (su primer análisis), ponderada por severidad "
            "— crítica 10, importante 5, moderada 2, baja 1; cada parche aplicado suma.")
_PLURAL = {"crítica": "críticos", "importante": "importantes", "moderada": "moderados", "baja": "bajos"}


def descuentos(cv):
    """[(texto, descuento en puntos con 1 decimal, nivel)]: la parte de 100 que aún está expuesta, por severidad."""
    sv_ = cv.get("sev") or {}
    total = sum(PESOS[k] * (sv_.get(k) or {}).get("t", 0) for k in PESOS)
    out = []
    for sv, peso in PESOS.items():
        d = sv_.get(sv) or {}
        if not total or not d.get("p"):
            continue
        out.append((f"{d['p']} de {d['t']} CVE {_PLURAL[sv]} pendientes (peso {peso})", round(100 * peso * d["p"] / total, 1),
                    "rojo" if sv == "crítica" else ("amarillo" if sv == "importante" else "info")))
    return out


# Entorno que impacta cada CVE, según el paquete donde está la falla
_ENTORNOS = [
    ("Contenedores", ("podman", "buildah", "skopeo", "runc", "crun", "conmon", "containers-common", "container-selinux", "cri-o", "cri-tools",
                      "docker", "moby", "netavark", "aardvark-dns", "slirp4netns", "fuse-overlayfs", "toolbox")),
    ("Kernel", ("kernel", "linux-firmware", "microcode_ctl")),
    ("Lenguajes y runtimes", ("java-", "python3", "python-", "python2", "nodejs", "perl", "php", "ruby", "golang", "rust", "dotnet", "openjdk", "tomcat")),
    ("Servicios y aplicaciones", ("httpd", "nginx", "postgresql", "mariadb", "mysql", "redis", "bind", "squid", "samba", "dovecot", "postfix",
                                  "sendmail", "cups", "vsftpd", "haproxy", "keepalived", "389-ds", "ipa-", "sssd", "freeradius", "tigervnc", "xorg-")),
    ("Librerías", ("lib", "openssl", "gnutls", "nss", "glib2", "zlib", "expat", "curl", "krb5", "pcre", "sqlite", "gnupg2", "gpgme", "libxml2",
                   "libxslt", "freetype", "harfbuzz", "poppler", "ncurses", "readline", "cyrus-sasl", "json-c", "protobuf", "c-ares", "nghttp2")),
    ("Sistema operativo", ("glibc", "systemd", "openssh", "sudo", "bash", "coreutils", "util-linux", "shadow-utils", "pam", "polkit", "grub2",
                           "shim", "rpm", "dnf", "yum", "tar", "less", "vim", "emacs", "git", "procps", "dbus", "NetworkManager", "firewalld",
                           "selinux-policy", "audit", "chrony", "rsync", "sos", "tzdata", "ca-certificates", "binutils", "gcc", "e2fsprogs",
                           "xfsprogs", "lvm2", "device-mapper", "cryptsetup", "iproute", "iptables", "nftables", "dhcp", "wget", "unzip", "zip")),
]


def entorno(paquete):
    """Sistema operativo | Kernel | Librerías | Contenedores | Lenguajes y runtimes | Servicios y aplicaciones."""
    n = (paquete or "").rsplit("-", 2)[0] if (paquete or "").count("-") >= 2 else (paquete or "")
    for nombre, prefijos in _ENTORNOS:
        if any(n == x or n.startswith(x) for x in prefijos):
            return nombre
    return "Sistema operativo" if n else "—"


_DETALLE = {}


def oficial(cve):
    """Ficha oficial de Red Hat (Security Data API), con caché de 6 h: descripción, CVSS, fecha y declaración."""
    import time
    import urllib.request
    if cve in _DETALLE and time.time() - _DETALLE[cve][0] < 21600:
        return _DETALLE[cve][1]
    d = {}
    try:
        req = urllib.request.Request(f"https://access.redhat.com/hydra/rest/securitydata/cve/{cve}.json",
                                     headers={"User-Agent": "FaceOps/1.0 (+https://invoke.cl)", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:   # sin User-Agent propio, Red Hat responde 403
            x = json.loads(r.read())
        d = {"descripcion": " ".join(x.get("details") or []) or (x.get("bugzilla") or {}).get("description", ""),
             "titulo": (x.get("bugzilla") or {}).get("description", ""), "fecha": (x.get("public_date") or "")[:10],
             "cvss": (x.get("cvss3") or {}).get("cvss3_base_score", ""), "vector": (x.get("cvss3") or {}).get("cvss3_scoring_vector", ""),
             "cwe": x.get("cwe", ""), "declaracion": x.get("statement", ""), "severidad_rh": x.get("threat_severity", ""),
             "mitigacion": (x.get("mitigation") or {}).get("value", "") if isinstance(x.get("mitigation"), dict) else (x.get("mitigation") or ""),
             "erratas": sorted({a.get("advisory") for a in x.get("affected_release") or [] if a.get("advisory")})[:12]}
    except Exception:  # noqa: BLE001 - sin internet o sin ficha: la página muestra los enlaces oficiales
        d = {}
        _DETALLE[cve] = (time.time() - 21600 + 300, d)   # reintenta en 5 min
        return d
    _DETALLE[cve] = (time.time(), d)
    return d


def resumen(cliente_id, eqs, estado="pendiente"):
    """Por CVE: severidad, paquete y equipos afectados (pendiente) o protegidos (cubierto)."""
    nombres = {e["id"]: e for e in eqs}
    por = {}
    for r in q("SELECT * FROM cves_equipo WHERE cliente_id=? AND estado=?", cliente_id, estado):
        if r["equipo_id"] not in nombres:
            continue
        x = por.setdefault(r["cve"], {"cve": r["cve"], "severidad": r["severidad"], "paquetes": set(), "equipos": [], "entornos": set()})
        x["paquetes"].add(r["paquete"].rsplit("-", 2)[0] if r["paquete"] else "")
        x["entornos"].add(entorno(r["paquete"]))
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
