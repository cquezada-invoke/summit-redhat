"""Inventario inteligente: equipos desde AAP (hosts + facts del recolector diario), segmentos por reglas,
skills automáticas y manuales, políticas de seguridad aplicables y condiciones de borde (hallazgos)."""
import json
import re
import unicodedata
from datetime import datetime, timezone

from .aap import AAP, ErrorAAP
from .bd import ahora, auditar, ej, j, q, q1

CAMPOS_REGLA = ("inventario", "inventarios", "grupo", "proveedor", "ubicacion", "ambiente", "so_familia", "skill", "nombre")


def normal(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


def skills_de(e):
    return sorted(set(j(e.get("skills_auto"), []) + j(e.get("skills_manual"), []) + list(j(e.get("skills_habilitadas"), {}))))


def valores(e, campo):
    if campo == "grupo":
        return [normal(g) for g in j(e.get("grupos"), [])]
    if campo == "skill":
        return [normal(s) for s in skills_de(e)]
    if campo == "inventarios":
        return [normal(i) for i in j(e.get("inventarios"), [])]
    return [normal(e.get(campo, ""))]


def cumple_regla(e, regla):
    """Regla = {campo: [valores]} → todos los campos deben calzar (Y); dentro de un campo basta uno (O).
    Un valor que termina en * es prefijo. Regla vacía = todo el cliente."""
    for campo, permitidos in (regla or {}).items():
        if campo not in CAMPOS_REGLA:
            continue
        permitidos = permitidos if isinstance(permitidos, list) else [permitidos]
        tiene = valores(e, campo)
        ok = False
        for p in permitidos:
            p = normal(p)
            if any((v.startswith(p[:-1]) if p.endswith("*") else v == p) for v in tiene):
                ok = True
                break
        if not ok:
            return False
    return True


SOPORTE = {"respaldo-git", "node-exporter", "node_exporter", "windows-exporter", "monitoreo", "redisinsight", "kvm", "storage-emulado"}


def con_plataforma(e):
    """¿Corre alguna plataforma (no solo agentes de soporte)? Los equipos de red, storage y clústeres cuentan por sí mismos."""
    if (e.get("categoria") or "servidor") != "servidor":   # red, storage, clústeres y estaciones de usuario cuentan por sí mismos
        return True
    return any(x not in SOPORTE for x in skills_de(e))


def segmentos_candidatos(e, segmentos):
    """Segmentos a los que la regla asigna la máquina, aunque aún no corra una plataforma (no forma parte todavía)."""
    fijos = set(j(e.get("segmentos_fijos"), []))
    return [s["nombre"] for s in segmentos if (s["nombre"] in fijos or cumple_regla(e, j(s["regla"], {})))
            and (not s.get("sistema") or s["sistema"] == sistema_eq(e))]


def segmentos_de(e, segmentos):
    """Un segmento de negocio agrupa equipos técnicamente compatibles: si define «sistema», solo entran equipos de ese sistema.
    Un servidor forma parte de un segmento a través de una plataforma: sin ninguna (solo el sistema operativo) no es parte
    de ninguno, aunque la regla lo nombre (queda como candidato hasta que se le incorpore una)."""
    fijos = set(j(e.get("segmentos_fijos"), []))
    cand = segmentos_candidatos(e, segmentos)
    return [n for n in cand if n in fijos or con_plataforma(e)]


def sistema_eq(e):
    """linux | windows | red (equipos de red sin sistema operativo de servidor)."""
    if (e.get("categoria") or "") in ("router", "switch", "firewall", "balanceador", "estación de trabajo"):
        return "red"
    return sistema_de(e)


# ------------------------------------------------------------------ resumen de facts
def _gb(txt):
    m = re.match(r"([\d.]+)\s*([KMGT])B", str(txt or ""))
    if not m:
        return 0.0
    return round(float(m.group(1)) * {"K": 1 / 2**20, "M": 1 / 1024, "G": 1, "T": 1024}[m.group(2)], 1)


def discos_fisicos(f):
    """[{dev, gb, uso, tipo, rotacional, modelo, particiones: [{nombre, gb, montajes: [{punto, fs, gb, uso}], lvm}]}] desde los facts."""
    devs = f.get("ansible_devices") or {}
    if not isinstance(devs, dict) or not devs:
        return []
    pvs = ((f.get("ansible_lvm") or {}).get("pvs") or {}) if isinstance(f.get("ansible_lvm"), dict) else {}
    mounts = [m for m in (f.get("ansible_mounts") or []) if m.get("size_total") and str(m.get("device", "")).startswith("/dev/")]
    out = []
    for nombre, d in sorted(devs.items()):
        if not re.match(r"^(vd|sd|xvd|nvme|hd)[a-z0-9]*$", nombre) or str(d.get("removable")) == "1" or d.get("model") == "QEMU DVD-ROM":
            continue
        parts = []
        for pn, p in sorted((d.get("partitions") or {}).items()):
            vg = (pvs.get(f"/dev/{pn}") or {}).get("vg", "")
            ms = []
            for m in mounts:
                dev = m["device"]
                if dev == f"/dev/{pn}" or (vg and (dev.startswith(f"/dev/mapper/{vg}-") or dev.startswith(f"/dev/{vg}/"))):
                    ms.append({"punto": m["mount"], "fs": m.get("fstype", ""), "dev": dev, "gb": round(m["size_total"] / 2**30, 1),
                               "uso": 100 - round(100 * m.get("size_available", 0) / m["size_total"])})
            parts.append({"nombre": pn, "gb": _gb(p.get("size")), "lvm": vg, "montajes": ms})
        tot = sum(m["gb"] for pt in parts for m in pt["montajes"])
        usado = sum(m["gb"] * m["uso"] / 100 for pt in parts for m in pt["montajes"])
        if not parts and not d.get("size"):
            continue
        out.append({"dev": nombre, "punto": "/dev/" + nombre, "gb": _gb(d.get("size")), "uso": round(100 * usado / tot) if tot else 0,
                    "tipo": {"vd": "virtio (disco virtual)", "sd": "SCSI / SATA", "xv": "Xen", "nv": "NVMe", "hd": "IDE"}.get(nombre[:2], ""),
                    "rotacional": str(d.get("rotational")) == "1", "modelo": (d.get("model") or d.get("vendor") or "").strip(),
                    "sectores": d.get("sectors"), "tam_sector": d.get("sectorsize"), "particiones": parts})
    return [x for x in out if x["particiones"] or x["gb"]]


def _num(v, defecto=0):
    """Número desde un fact (los equipos legados los entregan como texto)."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return defecto


def resumir(facts, host_vars):
    f = dict(facts or {})
    for k_ in ("ansible_uptime_seconds", "ansible_memtotal_mb", "ansible_memfree_mb", "ansible_processor_vcpus", "ansible_processor_count"):
        if isinstance(f.get(k_), str):
            f[k_] = _num(f[k_], None)
    for k_ in ("services", "ansible_default_ipv4", "ansible_selinux"):
        if k_ in f and not isinstance(f[k_], dict):
            f[k_] = {}
    if f.get("ansible_mounts"):
        f["ansible_mounts"] = [dict(m, size_total=_num(m.get("size_total")), size_available=_num(m.get("size_available")))
                               for m in (f["ansible_mounts"] if isinstance(f["ansible_mounts"], list) else []) if isinstance(m, dict)]
    nodo = (host_vars or {}).get("faceops_nodo_ocp")
    if isinstance(nodo, dict) and not f.get("ansible_os_family"):   # nodo de OpenShift: lo que informa el clúster por su API
        return {"so": nodo.get("sistema") or "Red Hat Enterprise Linux CoreOS", "so_familia": "RedHat", "so_version": "9",
                "ip": nodo.get("ip") or host_vars.get("ansible_host", ""), "cpu": int(float(nodo.get("cpu") or 0)) or None,
                "ram_mb": int(float(nodo.get("memoria_gb") or 0) * 1024) or None, "discos": [], "servicios": [],
                "datos": {"kernel": nodo.get("kernel", ""), "nodo_ocp": nodo, "recolectado": nodo.get("recolectado", ""),
                          "carga_1m": None, "virtual": "kvm"}}
    ocp = f.get("faceops_ocp")
    if isinstance(ocp, dict) and ocp.get("version"):  # clúster OpenShift: plataforma de contenedores, no un sistema operativo
        rec = ocp.get("recursos") or {}
        return {"so": f"OpenShift {ocp['version']}", "so_familia": "Plataforma de contenedores", "so_version": ocp["version"].split(".")[0],
                "ip": (ocp.get("nodos") or [{}])[0].get("ip") or host_vars.get("ansible_host", ""),
                "cpu": int(float(rec.get("cpu_total") or 0)) or None, "ram_mb": int(float(rec.get("memoria_total_gb") or 0) * 1024) or None,
                "discos": [], "servicios": [],
                "datos": {"ocp": ocp, "recolectado": ocp.get("recolectado", ""),
                          **({"openshift": host_vars["openshift"]} if isinstance((host_vars or {}).get("openshift"), dict) else {})}}
    hv = host_vars or {}
    red = f.get("faceops_red")
    if isinstance(red, dict) and red.get("plataforma") and (not f.get("ansible_os_family") or str(red.get("recolectado") or "") >= str(f.get("faceops_recolectado") or "")
                                                            or hv.get("faceops_categoria") in ("router", "switch", "firewall", "balanceador")):
        # equipo de red (lo trae «Red · Inventariar equipo de red»): su sistema es la plataforma de red, no un SO de servidor
        ver = str(red.get("version") or "")
        return {"so": f"{red['plataforma']} {ver}".strip(), "so_familia": "Red", "so_version": ver.split(".")[0],
                "ip": hv.get("ansible_host") or (red.get("gestion") or {}).get("ip", ""), "cpu": None, "ram_mb": None,
                "discos": [], "servicios": [],
                "datos": {"red": red, "recolectado": red.get("recolectado", ""), "hostname": red.get("hostname", ""),
                          "uptime_dias": round((red.get("uptime_s") or 0) / 86400, 1),
                          "hardware": {k: v for k, v in {"fabricante": red.get("fabricante"), "modelo": red.get("modelo"),
                                                         "serie": red.get("serie")}.items() if v}}}
    if not f.get("ansible_os_family") and hv.get("config.guestId"):  # VM de vSphere sin facts: lo que dice el vCenter
        return _vsphere(hv)
    fam = f.get("ansible_os_family", "")
    so = (f.get("ansible_distribution", "") + " " + f.get("ansible_distribution_version", "")).strip()
    discos = []
    for m in f.get("ansible_mounts", []) or []:
        if m.get("size_total"):
            usado = 100 - round(100 * m.get("size_available", 0) / m["size_total"])
            if m.get("mount") in ("/", "/var", "/home", "/opt", "/boot") or usado >= 75:
                discos.append({"punto": m.get("mount"), "gb": round(m["size_total"] / 2**30, 1), "uso": usado})
    fisicos = discos_fisicos(f)
    if fisicos:   # un disco por dispositivo físico, con sus particiones, LVM y puntos de montaje
        discos = fisicos
    servicios = sorted(k.replace(".service", "") for k, v in (f.get("services") or {}).items()
                       if isinstance(v, dict) and v.get("state") == "running") or sorted(f.get("faceops_servicios_win") or [])
    ipv4 = [x for x in (f.get("ansible_ip_addresses") or []) if x.count(".") == 3 and not x.startswith("169.254")]
    ip = (f.get("ansible_default_ipv4") or {}).get("address") or (ipv4 or [""])[0] \
        or host_vars.get("ansible_host", "")
    return {
        "so": so or host_vars.get("so", ""), "so_familia": fam or ("Windows" if "windows" in json.dumps(host_vars).lower() else ""),
        "so_version": f.get("ansible_distribution_major_version", ""), "ip": ip,
        "cpu": f.get("ansible_processor_vcpus") or f.get("ansible_processor_count"),
        "ram_mb": f.get("ansible_memtotal_mb"), "discos": discos, "servicios": servicios,
        "datos": {
            "kernel": f.get("ansible_kernel", ""), "uptime_dias": round((f.get("ansible_uptime_seconds") or 0) / 86400, 1),
            "selinux": (f.get("ansible_selinux") or {}).get("mode") if isinstance(f.get("ansible_selinux"), dict) else "",
            "ram_libre_mb": f.get("ansible_memfree_mb"), "carga_1m": (f.get("ansible_loadavg") or {}).get("1m"),
            "ram_disponible_mb": ((f.get("ansible_memory_mb") or {}).get("nocache") or {}).get("free"), "virtual": f.get("ansible_virtualization_type", ""),
            "registrado": f.get("faceops_registrado"), "parches_seguridad": f.get("faceops_parches_seguridad"),
            "recolectado": f.get("faceops_recolectado", ""), "hostname": f.get("ansible_fqdn", ""),
            "virtual_rol": f.get("ansible_virtualization_role", ""),
            "hardware": {k: v for k, v in {"fabricante": f.get("ansible_system_vendor"), "modelo": f.get("ansible_product_name"),
                                           "serie": f.get("ansible_product_serial"), "bios": f.get("ansible_bios_version")}.items()
                         if v and v not in ("NA", "QEMU", "Standard PC (Q35 + ICH9, 2009)", "Standard PC (i440FX + PIIX, 1996)")},
            "montajes_red": [{"punto": m.get("mount"), "origen": m.get("device"), "fs": m.get("fstype"), "gb": round((m.get("size_total") or 0) / 2**30, 1),
                              "uso": (100 - round(100 * m.get("size_available", 0) / m["size_total"])) if m.get("size_total") else None}
                             for m in (f.get("ansible_mounts") or []) if str(m.get("fstype", "")).startswith(("nfs", "cifs", "smb", "glusterfs", "ceph", "fuse.s3"))],
            "usuarios": _usuarios(f.get("faceops_usuarios") or []),
            "servicios_det": _servicios_det(f.get("faceops_servicios_det") or []), "logs": _logs(f.get("faceops_logs") or []), "jboss": [dict(zip(("home", "familia", "version", "apps"), (l.split("|") + ["", "", "", ""])[:4]), apps=[x for x in (l.split("|") + ["", "", "", ""])[3].split(",") if x])
                      for l in (f.get("faceops_jboss") or []) if l.count("|") >= 3],
            **({"openshift": host_vars["openshift"]} if isinstance((host_vars or {}).get("openshift"), dict) else {}),
            **_red_facts(f),
        },
    }


def _red_facts(f):
    """Puerta de enlace e interfaces IPv4 (con su prefijo) de los facts: base del diagrama Redes › Arquitectura."""
    gw = (f.get("ansible_default_ipv4") or {}).get("gateway") if isinstance(f.get("ansible_default_ipv4"), dict) else None
    itfs = []
    for n in (f.get("ansible_interfaces") or [])[:16]:
        d = f.get("ansible_" + str(n).replace("-", "_").replace(".", "_")) or {}
        v4 = d.get("ipv4") if isinstance(d, dict) else None
        if n != "lo" and isinstance(v4, dict) and v4.get("address") and not str(v4["address"]).startswith("127."):
            itfs.append({"nombre": n, "ip": v4["address"] + (f"/{v4['prefix']}" if v4.get("prefix") else "")})
    return {"red": {"gateway": gw or "", "interfaces": itfs}} if gw or itfs else {}


def _usuarios(lineas):
    """{usuarios: [{n, uid, origen: local|central, grupos, privilegiado}], dominios: [{n, proveedor}], sudoers: [...]}"""
    us, doms, priv, sudo = [], [], {}, []
    for l in lineas:
        p = (l.strip().split("|") + [""] * 6)
        if p[0] == "g":
            for m in filter(None, p[2].split(",")):
                priv.setdefault(m, []).append(p[1])
        elif p[0] == "d" and p[1]:
            doms.append({"n": p[1], "proveedor": {"ipa": "IdM (FreeIPA)", "ad": "Active Directory", "ldap": "LDAP"}.get(p[2], p[2])})
        elif p[0] == "s" and p[1]:
            sudo.append(p[1])
    for l in lineas:
        p = (l.strip().split("|") + [""] * 6)
        if p[0] == "u" and p[1]:
            grupos = [g for g in p[4].split(",") if g]
            rol = "administrador" if (p[2] == "0" or {"wheel", "sudo", "admins", "Administrators"} & set(grupos) or p[1] in priv) else "usuario"
            us.append({"n": p[1], "uid": p[2], "origen": p[3] or "local", "grupos": grupos, "rol": rol})
    return {"usuarios": us, "dominios": doms, "sudoers": sudo} if lineas else None


def _servicios_det(lineas):
    """[{n, tipo, activo, sub, arranque, resultado, estado}] · estado: arriba | detenido (habilitado y abajo) | error."""
    out = []
    for l in lineas:
        kv = dict(x.split("=", 1) for x in l.split(";") if "=" in x)
        if not kv.get("Id"):
            continue
        n, tipo, act, sub, uf, res = (kv.get(k, "") for k in ("Id", "Type", "ActiveState", "SubState", "UnitFileState", "Result"))
        if act == "failed" or res not in ("", "success"):
            est = "error"
        elif act == "active":
            est = "arriba"
        elif uf == "enabled" and tipo not in ("oneshot", "dbus") and kv.get("ConditionResult", "yes") != "no":
            est = "detenido"   # arranque automático, sin condición que lo impida y abajo: debería estar arriba
        else:
            est = "inactivo"   # corre una vez y termina, se activa a demanda (dbus) o su condición no se cumple: normal
        out.append({"n": n.replace(".service", ""), "tipo": tipo, "activo": act, "sub": sub, "arranque": uf, "resultado": res, "estado": est})
    return out


def _logs(lineas):
    out = []
    for l in lineas:
        p = (l.split("|") + ["", ""])[:3]
        if p[0].startswith("/"):
            out.append({"ruta": p[0], "kb": round(int(p[1] or 0) / 1024, 1) if (p[1] or "").isdigit() else None, "fecha": p[2]})
    return out


def so_de_guest_id(gid, completo=""):
    """guestId de vSphere (rhel9_64Guest, windows2019srv_64Guest, centos6_64Guest…) → (sistema, familia, versión)."""
    import re
    g = (gid or "").lower()
    if completo and completo != gid and "guest" not in completo.lower():
        fam = "Windows" if "windows" in completo.lower() else "RedHat"
        return completo, fam, (re.findall(r"\d+", completo) or [""])[0]
    for pre, nom, fam in (("rhel", "Red Hat Enterprise Linux", "RedHat"), ("centos", "CentOS", "RedHat"), ("oracle", "Oracle Linux", "RedHat"),
                          ("sles", "SUSE Linux Enterprise", "Suse"), ("ubuntu", "Ubuntu", "Debian"), ("debian", "Debian", "Debian")):
        if g.startswith(pre):
            v = (re.findall(r"\d+", g[len(pre):]) or [""])[0]
            return f"{nom} {v}".strip(), fam, v
    m = re.match(r"windows(\d{4})srv", g) or re.match(r"windows(\d+)server", g)
    if m:
        v = {"9": "2016", "8": "2012", "7": "2008 R2"}.get(m.group(1), m.group(1))
        return f"Windows Server {v}", "Windows", v
    return gid or "", "", ""


def _vsphere(hv):
    so, fam, ver = so_de_guest_id(hv.get("config.guestId"), hv.get("config.guestFullName") or "")
    return {"so": so, "so_familia": fam, "so_version": ver, "ip": hv.get("guest.ipAddress") or hv.get("ansible_host", ""),
            "cpu": hv.get("config.hardware.numCPU"), "ram_mb": hv.get("config.hardware.memoryMB") or None, "discos": [], "servicios": [],
            "datos": {"virtual": "VMware", "virtual_rol": "guest", "hostname": hv.get("guest.hostName", ""),
                      "energia": {"poweredOn": "encendida", "poweredOff": "apagada", "suspended": "suspendida"}.get(hv.get("summary.runtime.powerState"), ""),
                      "vsphere": {"uuid": hv.get("config.uuid"), "estado": hv.get("summary.runtime.powerState"), "guest_id": hv.get("config.guestId")},
                      "recolectado": ""}}


CATEGORIAS_GRUPO = {"router": "router", "routers": "router", "sucursales": "router", "switch": "switch", "switches": "switch",
                    "firewall": "firewall", "firewalls": "firewall", "fortigate": "firewall", "balanceador": "balanceador",
                    "lb": "balanceador", "storage": "almacenamiento", "almacenamiento": "almacenamiento"}


def categoria_y_forma(nombre, a, r):
    """Categoría (servidor, router, switch, firewall, balanceador, almacenamiento, estación) y forma (virtual | físico).
    Orden: variables del host (faceops_categoria / faceops_forma) → «_equipos» del cliente → grupos de AAP → datos recolectados."""
    over, hv = a["over"], a["hv"]
    cat = hv.get("faceops_categoria") or over.get("categoria")
    if not cat:
        cat = next((CATEGORIAS_GRUPO[normal(g)] for g in sorted(a["grupos"]) if normal(g) in CATEGORIAS_GRUPO), "")
    if not cat:
        cat = "servidor"   # sin datos aún (p. ej. máquina nueva sin vincular): por defecto es un servidor; red y storage llegan por sus grupos
    forma = hv.get("faceops_forma") or over.get("forma") or a["base"].get("forma")
    if not forma:
        rol, tipo = r["datos"].get("virtual_rol"), r["datos"].get("virtual")
        forma = "virtual" if rol == "guest" or tipo in ("kvm", "VMware", "virtualbox", "openstack", "xen", "hyperv", "container", "podman", "docker") \
            else ("físico" if r["so"] else "")
    return cat, forma


# ------------------------------------------------------------------ sincronización desde AAP
INV_NODOS_OCP = "OpenShift · nodos"


def alta_nodos_ocp(aap, cliente):
    """Cada nodo de un clúster OpenShift es también una máquina del inventario (con su sistema, CPU, memoria e IP),
    en el inventario de AAP «OpenShift · nodos». Se actualiza con lo que informa el clúster por su API."""
    clusters = [e for e in q("SELECT * FROM equipos WHERE cliente_id=? AND categoria='clúster openshift' AND estado!='retirado'", cliente["id"])]
    nodos = [(e, n) for e in clusters for n in ((j(e["datos"], {}).get("ocp") or {}).get("nodos") or [])]
    if not nodos:
        return
    org = (aap.get("organizations/", name="FaceOps").get("results") or [{}])[0].get("id")
    inv = (aap.get("inventories/", name=INV_NODOS_OCP).get("results") or [None])[0] or aap.post("inventories/", {
        "name": INV_NODOS_OCP, "organization": org, "description": "Nodos de los clústeres OpenShift (máquinas de cada clúster)"})
    for e, n in nodos:
        hv = {"ansible_host": n.get("ip", ""), "faceops_categoria": "servidor", "faceops_forma": "virtual",
              "faceops_so": n.get("sistema", ""), "faceops_ubicacion": e.get("ubicacion") or "", "faceops_ambiente": e.get("ambiente") or "",
              "faceops_nodo_ocp": dict(n, cluster=e["nombre"], cluster_id=e["id"], recolectado=(j(e["datos"], {}).get("ocp") or {}).get("recolectado", ""))}
        ya = (aap.get("hosts/", name=n["nombre"], inventory=inv["id"]).get("results") or [None])[0]
        if ya:
            aap.patch(f"hosts/{ya['id']}/", {"variables": json.dumps(hv, ensure_ascii=False)})
        else:
            aap.post("hosts/", {"name": n["nombre"], "inventory": inv["id"], "variables": json.dumps(hv, ensure_ascii=False)})
    cl = j(q1("SELECT clasificacion FROM clientes WHERE id=?", cliente["id"])["clasificacion"], {})
    if INV_NODOS_OCP not in cl:
        cl[INV_NODOS_OCP] = {"proveedor": "kvm", "ubicacion": clusters[0].get("ubicacion") or "", "ambiente": clusters[0].get("ambiente") or "produccion",
                             "sin_recoleccion": True, "forma": "virtual"}
        ej("UPDATE clientes SET clasificacion=? WHERE id=?", json.dumps(cl, ensure_ascii=False), cliente["id"])


def _fecha(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _ajuste_posterior(cliente_id, nombre, facts_fecha):
    """(vCPU, MB) del último «Infra · Ajustar recursos de máquinas» exitoso sobre la máquina si es posterior a sus facts.
    Sin esto, la sincronización volvía a mostrar los recursos de facts recolectados antes del cambio (el hipervisor ya tenía lo nuevo)."""
    t = q1("SELECT t.parametros, t.fin FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
           "WHERE t.cliente_id=? AND a.nombre_aap='Infra · Ajustar recursos de máquinas' AND t.estado='exitoso' "
           "AND t.parametros LIKE ? ORDER BY t.fin DESC LIMIT 1", cliente_id, f"%{nombre}:%")
    if not t:
        return None
    ff, ft = _fecha(facts_fecha), _fecha(t["fin"])
    if not ft or (ff and ff >= ft):
        return None
    for x in (j(t["parametros"], {}).get("ir_maquinas") or "").split(","):
        p_ = x.strip().split(":")
        if len(p_) == 3 and p_[0] == nombre and p_[1].isdigit() and p_[2].isdigit():
            return int(p_[1]), int(p_[2])
    return None


def sincronizar(cliente, usuario="sistema"):
    """Lee inventarios y hosts de AAP (con sus facts) y actualiza el inventario de FaceOps del cliente.
    Un mismo equipo puede estar en varios inventarios de AAP: en FaceOps es uno solo. Su clasificación
    (proveedor, ubicación, ambiente) sale del primer inventario según "_prioridad", de "_equipos" o de sus variables."""
    aap = AAP.de_cliente(cliente)
    try:
        alta_nodos_ocp(aap, cliente)
    except Exception as x:  # noqa: BLE001
        print("Nodos OpenShift:", str(x)[:200])
    clasif = j((q1("SELECT clasificacion FROM clientes WHERE id=?", cliente["id"]) or cliente)["clasificacion"], {})
    prioridad = clasif.get("_prioridad", [])
    invs = [i for i in aap.inventarios() if i["name"] not in clasif.get("_ignorar", []) and i.get("kind") != "constructed"]
    invs.sort(key=lambda i: prioridad.index(i["name"]) if i["name"] in prioridad else len(prioridad))
    acum = {}
    for inv in invs:
        base = clasif.get(inv["name"], {})
        for h in aap.hosts(inv["id"]):
            if h.get("enabled") is False:   # deshabilitado en AAP (p. ej. reemplazado): sale del inventario de FaceOps
                continue
            try:
                hv = json.loads(h.get("variables") or "{}")
            except ValueError:
                import yaml
                hv = yaml.safe_load(h.get("variables") or "") or {}
            grupos = [x["name"] for x in h.get("summary_fields", {}).get("groups", {}).get("results", [])]
            a = acum.get(h["name"])
            if not a:
                over = clasif.get("_equipos", {}).get(h["name"], {})
                a = acum[h["name"]] = {"over": over, "base": base,
                    "inventario": inv["name"], "inventarios": [], "grupos": set(), "facts": {}, "hv": hv, "host_id": h["id"],
                    "facts_fecha": None, "recolectable": not base.get("sin_recoleccion"),
                    "proveedor": hv.get("faceops_proveedor") or over.get("proveedor") or base.get("proveedor", ""),
                    "ubicacion": hv.get("faceops_ubicacion") or over.get("ubicacion") or base.get("ubicacion", ""),
                    "ambiente": hv.get("ambiente") or hv.get("faceops_ambiente") or over.get("ambiente") or base.get("ambiente", ""),
                }
            a["inventarios"].append(inv["name"])
            a["grupos"].update(grupos)
            if h.get("ansible_facts_modified") and (not a["facts_fecha"] or h["ansible_facts_modified"] > a["facts_fecha"]):
                a["facts"], a["facts_fecha"], a["host_id"] = aap.facts(h["id"]), h["ansible_facts_modified"], h["id"]
                a["recolectable"] = True
    vistos = set()
    for nombre, a in acum.items():
        r = resumir(a["facts"], a["hv"])
        estado = "con datos" if a["facts"] else ("sin datos" if a["recolectable"] else "inventariado")
        if not r["so"]:  # equipos de red: el sistema sale de su ansible_network_os o de la clasificación del cliente
            nos = str(a["hv"].get("ansible_network_os") or "").split(".")[-1].lower()
            r["so"] = a["hv"].get("faceops_so") or a["over"].get("so") or \
                {"vyos": "VyOS", "fortios": "FortiOS", "frr": "FRRouting", "eos": "Arista EOS", "ios": "Cisco IOS",
                 "iosxr": "Cisco IOS XR", "nxos": "Cisco NX-OS", "junos": "Juniper Junos", "panos": "PAN-OS"}.get(nos, "")
            r["so_familia"] = r["so_familia"] or ("Red" if r["so"] else "")
        categoria, forma = categoria_y_forma(nombre, a, r)
        fila = {"aap_host_id": a["host_id"], "inventario": a["inventario"], "inventarios": json.dumps(a["inventarios"]),
                "grupos": json.dumps(sorted(a["grupos"])), "so": r["so"], "so_familia": r["so_familia"],
                "so_version": r["so_version"], "ip": r["ip"], "cpu": r["cpu"], "ram_mb": r["ram_mb"],
                "discos": json.dumps(r["discos"]), "servicios": json.dumps(r["servicios"]), "datos": json.dumps(r["datos"]),
                "proveedor": a["proveedor"], "ubicacion": a["ubicacion"], "ambiente": a["ambiente"],
                "estado": estado, "ultima_recoleccion": a["facts_fecha"], "visto": ahora(),
                "categoria": categoria, "forma": forma,
                "segmentos_fijos": json.dumps([x for x in [a["hv"].get("faceops_segmento")] if x], ensure_ascii=False)}
        if isinstance(a["hv"].get("faceops_skills"), list):  # productos declarados en AAP (servicios que no se recolectan)
            fila["skills_manual"] = json.dumps(sorted(set(a["hv"]["faceops_skills"])))
        elif isinstance(a["over"].get("skills"), list):     # o en la clasificación del cliente (p. ej. el software de un router)
            fila["skills_manual"] = json.dumps(sorted(set(a["over"]["skills"])))
        aj = _ajuste_posterior(cliente["id"], nombre, a["facts_fecha"])
        if aj:   # facts anteriores al último «Ajustar recursos» exitoso: manda lo que quedó en el hipervisor
            fila["cpu"], fila["ram_mb"] = aj
        existe = q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], nombre)
        if existe:
            ej("UPDATE equipos SET " + ", ".join(f"{k}=?" for k in fila) + " WHERE id=?", *fila.values(), existe["id"])
            vistos.add(existe["id"])
        else:
            vistos.add(ej("INSERT INTO equipos(cliente_id, nombre, " + ", ".join(fila) + ") VALUES (?,?," +
                          ",".join("?" * len(fila)) + ")", cliente["id"], nombre, *fila.values()))
    for e in q("SELECT id, nombre FROM equipos WHERE cliente_id=?", cliente["id"]):
        if e["id"] not in vistos and not (e["nombre"] or "").startswith("demo-"):   # las máquinas demo no están en AAP: no se retiran
            ej("UPDATE equipos SET estado='retirado' WHERE id=?", e["id"])
    calcular(cliente)
    auditar(usuario, cliente["id"], "sincronizar inventario", "inventario", {"equipos": len(acum)})
    return len(acum)


# ------------------------------------------------------------------ skills, políticas y hallazgos
def sistema_de(e):
    """Familia del sistema operativo en minúsculas: linux | windows | otro (la usan políticas y buscador)."""
    f = normal(e.get("so_familia") or "")
    if f == "windows" or "windows" in normal(e.get("so") or ""):
        return "windows"
    if f in ("redhat", "debian", "suse", "linux") or any(x in normal(e.get("so") or "") for x in ("red hat", "rhel", "linux", "ubuntu")):
        return "linux"
    return "otro"


# Productos que traen su propia base de datos, caché o servidor web: esos servicios son parte del producto (componentes
# internos), no plataformas del equipo; no cuentan en el stack ni en los segmentos.
COMPONENTES_INTERNOS = {
    "satellite": ["postgres", "redis", "httpd", "tomcat"],
    "aap": ["postgres", "redis", "nginx"],
    "ansible-automation-platform": ["postgres", "redis", "nginx"],
    "red-hat-idm": ["httpd", "389-directory-server"],
    "keycloak": ["postgres"],
    "midpoint": ["postgres", "tomcat"],
}


def calcular(cliente):
    cid = cliente["id"]
    reglas = q("SELECT * FROM skills WHERE cliente_id=?", cid)
    for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cid):
        auto = []
        for s in reglas:
            r = j(s["regla"], {})
            ok = False
            for campo, vals in r.items():
                vals = [normal(v) for v in (vals if isinstance(vals, list) else [vals])]
                if campo == "servicio":
                    tiene = [normal(x) for x in j(e["servicios"], [])]
                    ok = ok or any(t == v or (v.endswith("*") and t.startswith(v[:-1])) for t in tiene for v in vals)
                elif campo == "grupo":
                    ok = ok or any(normal(g) in vals for g in j(e["grupos"], []))
                elif campo == "so_familia":
                    ok = ok or normal(e["so_familia"]) in vals
                elif campo == "nombre":
                    ok = ok or any(normal(e["nombre"]).startswith(v.rstrip("*")) for v in vals)
            if ok:
                auto.append(s["nombre"])
        todas = set(auto) | set(j(e.get("skills_manual"), [])) | set(j(e.get("skills_habilitadas"), {}))
        internos = sorted({x for prod, comps in COMPONENTES_INTERNOS.items() if prod in todas for x in comps if x in auto})
        auto = [x for x in auto if x not in internos]   # la base de datos, caché o web que trae un producto no es una plataforma aparte
        d_ = j(e.get("datos"), {})
        if d_.get("componentes_internos") != internos:
            d_["componentes_internos"] = internos
            ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d_, ensure_ascii=False), e["id"])
        ej("UPDATE equipos SET skills_auto=? WHERE id=?", json.dumps(sorted(set(auto))), e["id"])
    evaluar_hallazgos(cliente)


def politicas_de(e, cliente_id, segmentos=None):
    segmentos = segmentos if segmentos is not None else q("SELECT * FROM segmentos WHERE cliente_id=?", cliente_id)
    segs, sk = set(segmentos_de(e, segmentos)), set(skills_de(e))
    res = []
    for p in q("SELECT * FROM politicas WHERE cliente_id=? AND activa=1", cliente_id):
        a = j(p["aplica"], {})
        ps, pk, pa = set(a.get("segmentos", ["*"])), set(a.get("skills", ["*"])), set(a.get("ambientes", ["*"]))
        psis = set(a.get("sistemas", ["*"]))
        if ("*" in ps or ps & segs) and ("*" in pk or pk & sk) and ("*" in pa or (e.get("ambiente") or "") in pa) \
                and ("*" in psis or sistema_de(e) in psis):
            res.append(p)
    return res


def evaluar_control(e, c):
    """Devuelve (cumple: True/False/None si no hay datos, detalle)."""
    d, tipo, v = j(e["datos"], {}), c.get("tipo"), c.get("valor")
    if e["estado"] != "con datos":
        return None, "sin datos del recolector"
    if tipo == "servicio_activo":
        ok = any(normal(s) == normal(v) for s in j(e["servicios"], []))
        return ok, f"{v} {'activo' if ok else 'no está activo'}"
    if tipo == "selinux":
        if not d.get("selinux"):
            return None, "no aplica"
        return d["selinux"] == v, f"SELinux {d['selinux']}"
    if tipo == "registrado":
        if d.get("registrado") is None:
            return None, "sin dato"
        return bool(d["registrado"]), "registrado en Red Hat" if d["registrado"] else "sin registro en Red Hat"
    if tipo == "parches_max":
        n = d.get("parches_seguridad")
        if n is None:
            return None, "sin dato"
        return int(n) <= int(v), f"{n} parches de seguridad pendientes"
    if tipo == "disco_max":
        peor = max([x["uso"] for x in j(e["discos"], [])] or [0])
        return peor <= int(v), f"uso máximo de disco {peor}%"
    if tipo == "so_minimo":
        try:
            ok = int(str(e["so_version"]).split(".")[0]) >= int(v)
        except ValueError:
            return None, "versión desconocida"
        return ok, f"{e['so']}"
    if tipo == "uptime_max":
        return float(d.get("uptime_dias") or 0) <= float(v), f"{d.get('uptime_dias')} días encendido"
    return None, "control desconocido"


def evaluar_hallazgos(cliente):
    cid = cliente["id"]
    ej("DELETE FROM hallazgos WHERE cliente_id=? AND COALESCE(titulo,'') NOT LIKE '[demo]%'", cid)   # los demo se quitan desde Administración
    segmentos = q("SELECT * FROM segmentos WHERE cliente_id=?", cid)
    hoy = datetime.now(timezone.utc)
    for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cid):
        if e["estado"] == "inventariado" or (e["nombre"] or "").startswith("demo-"):
            continue
        propia = bool(j(e.get("datos"), {}).get("propia_faceops"))
        if propia and not e.get("ultima_recoleccion"):
            continue   # la máquina de FaceOps recién instalada parte limpia: se evalúa desde su primera recolección
        if propia:
            if e["estado"] == "con datos":
                from .riesgo import hallazgos as hallazgos_riesgo
                hallazgos_riesgo(cid, dict(e, skills=skills_de(e)), nuevo)
            for p in politicas_de(e, cid, segmentos):
                for c in j(p["controles"], []):
                    ok, det = evaluar_control(e, c)
                    if ok is False:
                        nuevo(cid, e, "politica", p["severidad"], c["nombre"], det, politica=p["nombre"], sugerencia=c.get("remediacion", ""))
            continue
        if e["estado"] != "con datos":
            nuevo(cid, e, "recoleccion", "media", "Sin datos del recolector",
                  "El equipo no entregó datos: revisar conexión o credencial", sugerencia="recolectar")
        else:
            try:
                dias = (hoy - datetime.fromisoformat((e["ultima_recoleccion"] or "").replace("Z", "+00:00"))).days
                if dias >= 2:
                    nuevo(cid, e, "recoleccion", "baja", f"Datos de hace {dias} días", "El recolector diario no lo alcanzó", sugerencia="recolectar")
            except (TypeError, ValueError):
                pass
        if e["estado"] == "con datos" and (e.get("categoria") or "servidor") == "servidor" and not skills_de(e):
            nuevo(cid, e, "sin_uso", "media", "Equipo vacío: ninguna skill andando",
                  "Encendido sin uso: habilita una skill o apágalo", sugerencia="apagar")
        if e["estado"] == "con datos" and e.get("codigo") not in (None, "") and not j(e.get("codigo"), []):
            nuevo(cid, e, "codigo", "media", "Sin estructura como código",
                  "Ninguna capa de su configuración está en Git: no se puede auditar ni reconstruir", sugerencia="registrar servidor")
        if e["estado"] == "con datos":   # errores conocidos con su condición activa (vuelven a alertar si reaparecen)
            from .riesgo import hallazgos as hallazgos_riesgo
            ee = dict(e, skills=skills_de(e))
            hallazgos_riesgo(cid, ee, nuevo)
        for p in politicas_de(e, cid, segmentos):
            for c in j(p["controles"], []):
                ok, det = evaluar_control(e, c)
                if ok is False:
                    nuevo(cid, e, "politica", p["severidad"], c["nombre"], det, politica=p["nombre"],
                          sugerencia=c.get("remediacion", ""))
    try:  # desviaciones contra la arquitectura aprobada de cada segmento
        from .arquitectura import hallazgos as hallazgos_arquitectura
        hallazgos_arquitectura(cliente, q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cid))
    except Exception:  # noqa: BLE001
        pass


def nuevo(cid, e, tipo, sev, titulo, detalle, politica="", sugerencia=""):
    ej("INSERT INTO hallazgos(cliente_id, equipo_id, tipo, severidad, titulo, detalle, politica, sugerencia, creado) "
       "VALUES (?,?,?,?,?,?,?,?,?)", cid, e["id"], tipo, sev, titulo, detalle, politica, sugerencia, ahora())


def cumplimiento(e, cliente_id, segmentos=None):
    """Lista de (politica, control, cumple, detalle) para la ficha del equipo."""
    out = []
    for p in politicas_de(e, cliente_id, segmentos):
        for c in j(p["controles"], []):
            ok, det = evaluar_control(e, c)
            out.append({"politica": p["nombre"], "severidad": p["severidad"], "control": c["nombre"], "cumple": ok,
                        "detalle": det, "remediacion": c.get("remediacion", "")})
    return out


# ------------------------------------------------------------------ recolección diaria (lanza el recolector en AAP)
def recolectar(cliente, usuario="sistema"):
    aap = AAP.de_cliente(cliente)
    plan = j(cliente["recoleccion"], {})
    plantilla = plan.get("_plantilla", "FaceOps · Recolectar inventario")
    r = aap.get("job_templates/", name=plantilla).get("results", [])
    if not r:
        raise ErrorAAP(f"No existe la plantilla '{plantilla}' en AAP")
    jt = r[0]["id"]
    inventarios = {i["name"]: i["id"] for i in aap.inventarios()}
    lanzados = []
    for inv, pasos in plan.items():
        if inv.startswith("_") or inv not in inventarios:
            continue
        for p in pasos:
            cred = aap.credencial(p["credencial"]) if p.get("credencial") else None
            job = aap.lanzar("job", jt, limite=p.get("limite") or None, inventario=inventarios[inv],
                             credenciales=[cred] if cred else None)
            lanzados.append({"inventario": inv, "limite": p.get("limite", ""), "job": job})
    auditar(usuario, cliente["id"], "recolectar inventario", "inventario", {"trabajos": lanzados})
    return lanzados


# ------------------------------------------------------------------ estado de energía (encendido / apagado)
PUERTOS_SONDEO = (22, 3389, 5985, 443, 80, 830)


def _responde(ip, timeout=1.5):
    """Encendido si algún puerto típico contesta (aceptando o rechazando la conexión); apagado si nadie responde."""
    import socket
    for p in PUERTOS_SONDEO:
        try:
            with socket.create_connection((ip, p), timeout=timeout):
                return True
        except ConnectionRefusedError:
            return True   # el equipo contestó: está encendido aunque ese puerto esté cerrado
        except OSError:
            continue
    return False


def sondear_energia(cliente_id=None):
    """Prueba de conexión liviana a cada equipo con IP; guarda energia = encendido | apagado."""
    from concurrent.futures import ThreadPoolExecutor
    filtro, args = ("AND cliente_id=?", [cliente_id]) if cliente_id else ("", [])
    todos_ = q(f"SELECT id, ip, datos FROM equipos WHERE estado!='retirado' {filtro}", *args)
    # sin IP (p. ej. VM recién creada en vSphere): el estado de energía lo informa el hipervisor en el inventario de AAP
    ahora_ = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for e in todos_:
        if not e["ip"]:
            try:
                en = (json.loads(e["datos"] or "{}") or {}).get("energia", "")
            except (ValueError, TypeError):
                en = ""
            est = {"encendida": "encendido", "apagada": "apagado", "suspendida": "apagado"}.get(en)
            if est:
                ej("UPDATE equipos SET energia_cambio=CASE WHEN COALESCE(energia,'')!=? THEN ? ELSE energia_cambio END, energia=?, energia_visto=?, energia_origen='hipervisor' WHERE id=?", est, ahora_, est, ahora_, e["id"])
    eqs = [e for e in todos_ if e["ip"]]
    if not eqs:
        return {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        res = dict(zip([e["id"] for e in eqs], ex.map(lambda e: _responde(e["ip"]), eqs)))
    cuando = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # recién encendido por un ticket puede no contestar mientras arranca: se respeta el ticket solo 10 min; pasado eso,
    # si no responde se muestra apagado (antes eran 12 h y quedaban «encendidos» equipos apagados a los que no se entraba)
    from datetime import timedelta
    tope = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
    por_ticket = {r["id"] for r in q(f"SELECT id FROM equipos WHERE energia_origen='ticket' AND energia_visto>? {filtro}", tope, *args)}
    for eid, ok in res.items():
        if not ok and eid in por_ticket:
            continue
        est = "encendido" if ok else "apagado"
        ej("UPDATE equipos SET energia_cambio=CASE WHEN COALESCE(energia,'')!=? THEN ? ELSE energia_cambio END, energia=?, energia_visto=?, energia_origen='sondeo' WHERE id=?",
           est, cuando, est, cuando, eid)
    return res
