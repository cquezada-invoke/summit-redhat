"""Gobierno › Arquitectura › Licencias: todas las licencias y suscripciones del cliente, de todos los fabricantes, en un solo
lugar; dónde está registrado cada equipo (Satellite, Red Hat, activación de Windows…) y el cruce caso a caso entre lo
contratado y lo que cada equipo realmente consume.

Cada contrato tiene una «regla» que dice qué equipos consume y cuántas unidades usa cada uno (métricas de cada fabricante):
  rhel             RHEL Server: 1 suscripción cubre 2 sockets (físico) o 2 VM (virtual)
  openshift        OpenShift Container Platform: 1 suscripción por cada 2 cores (4 vCPU) de los nodos
  aap              Ansible Automation Platform: 1 por nodo gestionado (cada equipo que automatiza FaceOps)
  satellite        Red Hat Satellite: 1 por servidor Satellite
  windows-server   Windows Server Standard: por core; mínimo 8 por VM y 16 por servidor físico
  windows-cliente  Windows Enterprise: 1 por estación de trabajo
  sqlserver        SQL Server Standard: por core; mínimo 4 por VM
  vsphere          VMware vSphere: por core de host; mínimo 16 por CPU
"""
import json
import math
import re

from .bd import ahora, ej, j, q, q1
from .inventario import skills_de

ESQUEMA = """
CREATE TABLE IF NOT EXISTS licencias (
  id INTEGER PRIMARY KEY, cliente_id INTEGER, fabricante TEXT, producto TEXT, regla TEXT, sku TEXT, cantidad INTEGER,
  unidad TEXT, desde TEXT, hasta TEXT, registro TEXT, contrato TEXT, nota TEXT, origen TEXT, creado TEXT
);
"""

REGLAS = {
    "rhel": {"fabricante": "Red Hat", "unidad": "suscripción (2 sockets o 2 VM)"},
    "openshift": {"fabricante": "Red Hat", "unidad": "suscripción (2 cores / 4 vCPU)"},
    "aap": {"fabricante": "Red Hat", "unidad": "nodo gestionado"},
    "satellite": {"fabricante": "Red Hat", "unidad": "servidor"},
    "windows-server": {"fabricante": "Microsoft", "unidad": "core"},
    "windows-cliente": {"fabricante": "Microsoft", "unidad": "dispositivo"},
    "sqlserver": {"fabricante": "Microsoft", "unidad": "core"},
    "vsphere": {"fabricante": "Broadcom (VMware)", "unidad": "core"},
}

# Contratos del laboratorio (datos de demostración, marcados con origen «demo»: se reemplazan por los reales del cliente)
DEMO = [
    ("Red Hat", "Red Hat Enterprise Linux Server, Standard (Physical or Virtual Nodes)", "rhel", "RH00004", 6, "2026-01-01", "2026-12-31",
     "Red Hat Customer Portal · cuenta del cliente · manifiesto cargado en Satellite dc1-sat01", "Contrato 16xxxxx (demo)"),
    ("Red Hat", "Red Hat OpenShift Container Platform, Standard (2 Core or 4 vCPU)", "openshift", "MW00281", 4, "2026-01-01", "2026-12-31",
     "Red Hat Customer Portal · OpenShift Cluster Manager", "Contrato 16xxxxx (demo)"),
    ("Red Hat", "Red Hat Ansible Automation Platform, Standard (100 Managed Nodes)", "aap", "MCT3691", 100, "2026-01-01", "2026-12-31",
     "Red Hat Customer Portal · manifiesto cargado en AAP", "Contrato 16xxxxx (demo)"),
    ("Red Hat", "Red Hat Satellite Server", "satellite", "MCT3718", 1, "2026-01-01", "2026-12-31",
     "Red Hat Customer Portal · manifiesto de suscripciones del Satellite", "Contrato 16xxxxx (demo)"),
    ("Microsoft", "Windows Server 2022 Standard (core)", "windows-server", "9EM-00652", 16, "2025-07-01", "2026-06-30",
     "Microsoft 365 Admin Center / VLSC · activación por clave KMS", "Acuerdo EA (demo)"),
    ("Microsoft", "Windows 11 Enterprise E3", "windows-cliente", "AAA-04203", 5, "2025-07-01", "2026-06-30",
     "Microsoft 365 Admin Center · activación por suscripción", "Acuerdo EA (demo)"),
    ("Broadcom (VMware)", "VMware vSphere Foundation (core)", "vsphere", "VCF-VSP-FND", 32, "2026-03-01", "2027-02-28",
     "Broadcom Support Portal · llave de licencia en vCenter", "Contrato Broadcom (demo)"),
]


def iniciar():
    from .bd import con
    con().executescript(ESQUEMA)


def sembrar(cliente_id):
    if q1("SELECT 1 FROM licencias WHERE cliente_id=?", cliente_id):
        return
    for fab, prod, regla, sku, cant, desde, hasta, reg, contrato in DEMO:
        ej("INSERT INTO licencias(cliente_id, fabricante, producto, regla, sku, cantidad, unidad, desde, hasta, registro, contrato, nota, origen, creado) "
           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cliente_id, fab, prod, regla, sku, cant, REGLAS[regla]["unidad"], desde, hasta, reg, contrato,
           "Dato de demostración: reemplazar por el contrato real", "demo", ahora())


def agregar(cliente_id, f):
    regla = f.get("regla", "")
    if regla not in REGLAS:
        raise ValueError("Elige qué cubre la licencia")
    try:
        cant = int(f.get("cantidad") or 0)
    except ValueError:
        cant = 0
    if not (f.get("producto") or "").strip() or cant < 1:
        raise ValueError("Faltan el producto o la cantidad")
    for c in ("desde", "hasta"):
        if f.get(c) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", f[c]):
            raise ValueError("Fechas en formato AAAA-MM-DD")
    ej("INSERT INTO licencias(cliente_id, fabricante, producto, regla, sku, cantidad, unidad, desde, hasta, registro, contrato, nota, origen, creado) "
       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cliente_id, (f.get("fabricante") or REGLAS[regla]["fabricante"]).strip(), f["producto"].strip(), regla,
       (f.get("sku") or "").strip(), cant, REGLAS[regla]["unidad"], f.get("desde") or "", f.get("hasta") or "", (f.get("registro") or "").strip(),
       (f.get("contrato") or "").strip(), (f.get("nota") or "").strip(), "cliente", ahora())


def _so(e):
    return (e.get("so") or "").lower()


def _consumo(e, regla):
    """Unidades que consume el equipo bajo la regla, y dónde está registrado. None si la regla no lo alcanza."""
    so, d, sk = _so(e), j(e.get("datos"), {}), set(skills_de(e))
    virtual, cpu = (e.get("forma") or "") == "virtual", int(e.get("cpu") or 0)
    cat = (e.get("categoria") or "").lower()
    if regla == "rhel":
        if not re.match(r"(red ?hat|rhel)", so) or "coreos" in so or cat == "clúster openshift":
            return None
        sat = (d.get("satellite") or {}).get("servidor")
        reg = True if (d.get("registrado") or sat) else (False if d.get("registrado") is False else None)
        donde = f"Satellite {sat}" if sat else ("Red Hat (CDN)" if d.get("registrado") else ("sin registro" if reg is False else "sin dato"))
        return {"u": 0.5 if virtual else 1, "detalle": "VM (2 por suscripción)" if virtual else "físico (2 sockets)", "registrado": reg, "donde": donde}
    if regla == "openshift":
        if cat != "clúster openshift":
            return None
        return {"u": math.ceil(cpu / 4) if cpu else 0, "detalle": f"{cpu} vCPU", "registrado": None, "donde": "OpenShift Cluster Manager (sin dato)"}
    if regla == "aap":
        if cat in ("clúster openshift",) or e.get("estado") == "retirado" or not e.get("aap_host_id"):
            return None
        return {"u": 1, "detalle": "nodo en el inventario de AAP", "registrado": True, "donde": f"AAP · {e.get('inventario') or '—'}"}
    if regla == "satellite":
        if "satellite" not in sk:
            return None
        return {"u": 1, "detalle": "servidor Satellite", "registrado": True, "donde": "manifiesto cargado en Satellite"}
    if regla == "windows-server":
        if "windows server" not in so:
            return None
        cores = max(8, cpu) if virtual else max(16, cpu)
        if "evaluation" in so:
            return {"u": cores, "detalle": f"{cores} cores", "registrado": False, "donde": "copia de evaluación (no activada con licencia)"}
        return {"u": cores, "detalle": f"{cores} cores", "registrado": None, "donde": "activación (sin dato)"}
    if regla == "windows-cliente":
        if "windows" not in so or "server" in so:
            return None
        return {"u": 1, "detalle": "estación de trabajo", "registrado": None, "donde": "activación (sin dato)"}
    if regla == "sqlserver":
        if "sqlserver" not in sk and "sql server" not in so:
            return None
        cores = max(4, cpu)
        return {"u": cores, "detalle": f"{cores} cores", "registrado": None, "donde": "sin dato"}
    if regla == "vsphere":
        if "esxi" not in so and "esxi" not in sk:
            return None
        cores = max(16, cpu)
        return {"u": cores, "detalle": f"{cores} cores", "registrado": None, "donde": "vCenter (sin dato)"}
    return None


def cruce(cliente_id, eqs):
    """Por contrato: contratado, en uso, disponible y vigencia. Por equipo: qué licencia consume, dónde está registrado y si
    está efectivamente cubierto (registrado + cupo en un contrato vigente). Producción se cubre primero."""
    hoy = ahora()[:10]
    lics = [dict(x) for x in q("SELECT * FROM licencias WHERE cliente_id=? ORDER BY fabricante, producto", cliente_id)]
    eqs = sorted([e for e in eqs if e.get("estado") != "retirado"], key=lambda e: ((e.get("ambiente") or "") != "produccion", e["nombre"]))
    casos = []
    for regla in REGLAS:
        pool = [l_ for l_ in lics if l_["regla"] == regla]
        for l_ in pool:
            l_.setdefault("usado", 0.0)
            l_["vigente"] = (not l_["hasta"] or l_["hasta"] >= hoy) and (not l_["desde"] or l_["desde"] <= hoy)
        for e in eqs:
            c = _consumo(e, regla)
            if not c:
                continue
            lic = next((l_ for l_ in pool if l_["vigente"] and l_["cantidad"] - l_["usado"] >= c["u"]), None)
            if lic:
                lic["usado"] += c["u"]
            if not pool:
                est, t = "sin licencia", "No hay ningún contrato registrado para este producto"
            elif not lic:
                est, t = "sin cupo", ("El contrato está vencido" if not any(l_["vigente"] for l_ in pool) else "Los contratos vigentes no alcanzan")
            elif c["registrado"] is False:
                est, t = "no registrado", "Hay cupo, pero el equipo no está registrado: no recibe soporte ni parches"
            elif c["registrado"] is None:
                est, t = "sin dato", "Hay cupo; falta confirmar el registro del equipo"
            else:
                est, t = "cubierto", "Registrado y con cupo en un contrato vigente"
            casos.append({"equipo": e, "regla": regla, "fabricante": REGLAS[regla]["fabricante"], "producto": (lic or (pool[0] if pool else {})).get("producto") or regla,
                          "unidades": c["u"], "detalle": c["detalle"], "donde": c["donde"], "estado": est, "t": t, "lic_id": (lic or {}).get("id")})
    for l_ in lics:
        l_["usado"] = l_.get("usado", 0)
        l_["usado_txt"] = (f"{l_['usado']:g}")
        l_["libre"] = l_["cantidad"] - l_["usado"]
        l_["faltan"] = sum(c["unidades"] for c in casos if c["regla"] == l_["regla"] and c["estado"] == "sin cupo") if l_ is next((x for x in lics if x["regla"] == l_["regla"]), None) else 0
        l_["dias"] = None
        if l_.get("hasta"):
            from datetime import date
            try:
                l_["dias"] = (date.fromisoformat(l_["hasta"]) - date.fromisoformat(hoy)).days
            except ValueError:
                pass
    fabs = {}
    for c in casos:
        f_ = fabs.setdefault(c["fabricante"], {"n": 0, "cubierto": 0})
        f_["n"] += 1
        f_["cubierto"] += c["estado"] == "cubierto"
    for f_ in fabs.values():
        f_["pct"] = round(100 * f_["cubierto"] / f_["n"]) if f_["n"] else None
    return {"licencias": lics, "casos": casos, "fabricantes": fabs,
            "resumen": {k: sum(1 for c in casos if c["estado"] == k) for k in ("cubierto", "sin dato", "no registrado", "sin cupo", "sin licencia")}}


def json_seguro(x):
    return json.dumps(x, ensure_ascii=False, default=str)


# ------------------------------------------------------------------ licencias en uso y su caducidad, levantadas de cada plataforma
AUTO_LEVANTAR = "Licencias · Levantar desde las plataformas"


def _tabla_det():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS licencias_detectadas (
      id INTEGER PRIMARY KEY, cliente_id INTEGER, equipo TEXT, fabricante TEXT, producto TEXT, sku TEXT, contrato TEXT, vence TEXT,
      fuente TEXT, detalle TEXT, visto TEXT, ticket_id INTEGER);""")


def _fecha(txt):
    """«12/31/2026», «2026-12-31» o «31/12/2026» → 2026-12-31."""
    txt = (txt or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", txt)
    if m:
        return m.group(0)
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", txt)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        mes, dia = (a, b) if a <= 12 else (b, a)
        return f"{y}-{mes:02d}-{dia:02d}"
    return ""


def _consumidas(txt):
    out, cur = [], {}
    for linea in (txt or "").splitlines():
        if ":" not in linea:
            continue
        k, v = [x.strip() for x in linea.split(":", 1)]
        k = k.lower()
        if k == "subscription name" and cur:
            out.append(cur)
            cur = {}
        cur[k] = v
    if cur:
        out.append(cur)
    return [x for x in out if x.get("subscription name")]


def levantar_desde(artefactos):
    """Artefactos del job → [{equipo, fabricante, producto, sku, contrato, vence, fuente, detalle}]."""
    out = []
    for host, d in ((artefactos or {}).get("faceops_licencias") or {}).items():
        if d.get("familia") == "RedHat":
            subs = _consumidas(d.get("consumidas"))
            for s_ in subs:
                out.append({"equipo": host, "fabricante": "Red Hat", "producto": s_.get("subscription name", ""), "sku": s_.get("sku", ""),
                            "contrato": s_.get("contract", ""), "vence": _fecha(s_.get("ends")), "fuente": "subscription-manager", "detalle": d.get("so", "")})
            if not subs:
                so_ = re.sub(r"^RedHat\b", "Red Hat Enterprise Linux", d.get("so") or "RHEL")
                est = d.get("estado") or ""
                sca = "simple content access" in est.lower() or "content access mode" in est.lower()
                reg = "unknown" not in est.lower() and bool(est.strip())
                ident = (d.get("identidad") or "").splitlines()
                out.append({"equipo": host, "fabricante": "Red Hat", "producto": so_, "sku": "", "contrato": "",
                            "vence": "", "fuente": ("acceso simple (SCA) · " if sca else "") + ("registrado" if reg else "sin registrar"),
                            "detalle": " · ".join(x for x in ident if x)})
        elif d.get("familia") == "Windows":
            try:
                w = json.loads(d.get("windows") or "{}")
            except ValueError:
                w = {}
            out.append({"equipo": host, "fabricante": "Microsoft", "producto": w.get("producto") or d.get("so", "Windows"), "sku": "",
                        "contrato": w.get("canal", ""), "vence": _fecha(w.get("vence")), "fuente": "activación de Windows",
                        "detalle": {1: "activado", 0: "sin licencia", 2: "período de gracia", 5: "notificación"}.get(w.get("estado"), str(w.get("estado", "")))})
    return out


def guardar_levantamiento(t, artefactos):
    _tabla_det()
    filas = levantar_desde(artefactos)
    ej("DELETE FROM licencias_detectadas WHERE cliente_id=?", t["cliente_id"])
    for f in filas:
        ej("INSERT INTO licencias_detectadas (cliente_id, equipo, fabricante, producto, sku, contrato, vence, fuente, detalle, visto, ticket_id) "
           "VALUES (?,?,?,?,?,?,?,?,?,?,?)", t["cliente_id"], f["equipo"], f["fabricante"], f["producto"], f["sku"], f["contrato"], f["vence"],
           f["fuente"], f["detalle"] if isinstance(f["detalle"], str) else json.dumps(f["detalle"]), ahora(), t["id"])
    return len(filas)


_aap_lic = {}


def licencia_aap(cliente):
    """La suscripción de Ansible Automation Platform del cliente, leída de su AAP (caché 1 hora)."""
    import time
    c = _aap_lic.get(cliente["id"])
    if c and time.time() - c[0] < 3600:
        return c[1]
    r = None
    try:
        from .aap import AAP
        info = (AAP.de_cliente(cliente).get("config/") or {}).get("license_info") or {}
        if info:
            from datetime import datetime, timezone
            ts = info.get("license_date")
            r = {"producto": info.get("subscription_name") or "Ansible Automation Platform", "sku": info.get("sku") or "",
                 "vence": datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d") if ts else "",
                 "nodos": info.get("instance_count"), "usados": info.get("current_instances") or info.get("automated_instances"),
                 "vencida": bool(info.get("date_expired"))}
    except Exception:  # noqa: BLE001 - sin AAP no se muestra
        r = None
    _aap_lic[cliente["id"]] = (time.time(), r)
    return r


def caducidad(cliente, contratos):
    """Todas las licencias en uso con fecha de término: contratos registrados, lo levantado de cada máquina y la suscripción de AAP,
    agrupadas por producto, contrato y fecha, ordenadas por lo que vence primero."""
    _tabla_det()
    from datetime import date
    hoy = date.today()
    grupos = {}
    for d in q("SELECT * FROM licencias_detectadas WHERE cliente_id=?", cliente["id"]):
        k = (d["fabricante"], d["producto"], d["contrato"], d["vence"])
        x = grupos.setdefault(k, {"fabricante": d["fabricante"], "producto": d["producto"], "sku": d["sku"], "contrato": d["contrato"],
                                  "vence": d["vence"], "fuente": d["fuente"], "equipos": [], "origen": "plataforma", "visto": d["visto"]})
        x["equipos"].append(d["equipo"])
    filas = list(grupos.values())
    for l_ in contratos:
        filas.append({"fabricante": l_["fabricante"], "producto": l_["producto"], "sku": l_.get("sku") or "", "contrato": l_.get("contrato") or "",
                      "vence": l_.get("hasta") or "", "fuente": l_.get("registro") or "contrato registrado", "equipos": [], "origen": "contrato",
                      "cantidad": l_.get("cantidad"), "usado": l_.get("usado")})
    a = licencia_aap(cliente)
    if a:
        filas.append({"fabricante": "Red Hat", "producto": a["producto"], "sku": a["sku"], "contrato": "", "vence": a["vence"],
                      "fuente": "suscripción cargada en AAP", "equipos": [], "origen": "plataforma", "cantidad": a["nodos"], "usado": a["usados"]})
    for f in filas:
        try:
            dias = (date.fromisoformat(f["vence"]) - hoy).days if f["vence"] else None
        except ValueError:
            dias = None
        f["dias"] = dias
        f["estado"] = ("sin fecha" if dias is None else "vencida" if dias < 0 else "vence en 30 días" if dias <= 30
                       else "vence en 90 días" if dias <= 90 else "vigente")
    orden = {"vencida": 0, "vence en 30 días": 1, "vence en 90 días": 2, "vigente": 3, "sin fecha": 4}
    filas.sort(key=lambda f: (orden[f["estado"]], f["dias"] if f["dias"] is not None else 99999, f["fabricante"], f["producto"]))
    ult = q1("SELECT MAX(visto) v FROM licencias_detectadas WHERE cliente_id=?", cliente["id"]) or {}
    return {"filas": filas, "cuenta": {k: sum(1 for f in filas if f["estado"] == k) for k in orden}, "levantado": ult.get("v") or ""}
