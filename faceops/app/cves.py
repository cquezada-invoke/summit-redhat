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
    from .operacion import artefactos_ticket   # también si corrió dentro del flujo de aprobación (workflow)
    art = artefactos_ticket(aap, t)
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
    pend, cub = {}, {}
    ids_ = {e["id"] for e in eqs}
    for r in q("SELECT equipo_id, cve, severidad, paquete, estado FROM cves_equipo WHERE cliente_id=?", cliente_id):
        if r["equipo_id"] not in ids_:
            continue
        (pend if r["estado"] == "pendiente" else cub).setdefault(r["equipo_id"], {}).setdefault(r["cve"], r)
    out = {}
    for e in eqs:
        r = rev.get(e["id"])
        ps = sorted(pend.get(e["id"], {}).values(), key=lambda x: (ORDEN.get(x["severidad"], 9), x["cve"]))
        out[e["id"]] = {"revisado": bool(r), "repos": bool(r and r["repos"]), "pendientes": len(ps), "cubiertos": r["cubiertos"] if r else 0,
                        "criticos": len([x for x in ps if x["severidad"] == "crítica"]), "importantes": len([x for x in ps if x["severidad"] == "importante"]),
                        "cuando": r["cuando"] if r else None, "top": ps[:8],
                        # todos los CVE reportados para su sistema: pendientes primero (por severidad) y los resueltos marcados ok
                        "todos": [dict(x, ok=False) for x in ps] + [dict(x, ok=True) for x in sorted(
                            (v for k, v in cub.get(e["id"], {}).items() if k not in pend.get(e["id"], {})),
                            key=lambda x: (ORDEN.get(x["severidad"], 9), x["cve"]))]}
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
