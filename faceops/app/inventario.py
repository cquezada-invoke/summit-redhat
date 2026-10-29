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


def segmentos_de(e, segmentos):
    return [s["nombre"] for s in segmentos if cumple_regla(e, j(s["regla"], {}))]


# ------------------------------------------------------------------ resumen de facts
def resumir(facts, host_vars):
    f = facts or {}
    fam = f.get("ansible_os_family", "")
    so = (f.get("ansible_distribution", "") + " " + f.get("ansible_distribution_version", "")).strip()
    discos = []
    for m in f.get("ansible_mounts", []) or []:
        if m.get("size_total"):
            usado = 100 - round(100 * m.get("size_available", 0) / m["size_total"])
            if m.get("mount") in ("/", "/var", "/home", "/opt", "/boot") or usado >= 75:
                discos.append({"punto": m.get("mount"), "gb": round(m["size_total"] / 2**30, 1), "uso": usado})
    for d in (f.get("ansible_disks") or []) if isinstance(f.get("ansible_disks"), list) else []:
        pass
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
            "ram_libre_mb": f.get("ansible_memfree_mb"), "virtual": f.get("ansible_virtualization_type", ""),
            "registrado": f.get("faceops_registrado"), "parches_seguridad": f.get("faceops_parches_seguridad"),
            "recolectado": f.get("faceops_recolectado", ""), "hostname": f.get("ansible_fqdn", ""),
        },
    }


# ------------------------------------------------------------------ sincronización desde AAP
def sincronizar(cliente, usuario="sistema"):
    """Lee inventarios y hosts de AAP (con sus facts) y actualiza el inventario de FaceOps del cliente.
    Un mismo equipo puede estar en varios inventarios de AAP: en FaceOps es uno solo. Su clasificación
    (proveedor, ubicación, ambiente) sale del primer inventario según "_prioridad", de "_equipos" o de sus variables."""
    aap = AAP.de_cliente(cliente)
    clasif = j(cliente["clasificacion"], {})
    prioridad = clasif.get("_prioridad", [])
    invs = [i for i in aap.inventarios() if i["name"] not in clasif.get("_ignorar", []) and i.get("kind") != "constructed"]
    invs.sort(key=lambda i: prioridad.index(i["name"]) if i["name"] in prioridad else len(prioridad))
    acum = {}
    for inv in invs:
        base = clasif.get(inv["name"], {})
        for h in aap.hosts(inv["id"]):
            try:
                hv = json.loads(h.get("variables") or "{}")
            except ValueError:
                import yaml
                hv = yaml.safe_load(h.get("variables") or "") or {}
            grupos = [x["name"] for x in h.get("summary_fields", {}).get("groups", {}).get("results", [])]
            a = acum.get(h["name"])
            if not a:
                over = clasif.get("_equipos", {}).get(h["name"], {})
                a = acum[h["name"]] = {
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
        fila = {"aap_host_id": a["host_id"], "inventario": a["inventario"], "inventarios": json.dumps(a["inventarios"]),
                "grupos": json.dumps(sorted(a["grupos"])), "so": r["so"], "so_familia": r["so_familia"],
                "so_version": r["so_version"], "ip": r["ip"], "cpu": r["cpu"], "ram_mb": r["ram_mb"],
                "discos": json.dumps(r["discos"]), "servicios": json.dumps(r["servicios"]), "datos": json.dumps(r["datos"]),
                "proveedor": a["proveedor"], "ubicacion": a["ubicacion"], "ambiente": a["ambiente"],
                "estado": estado, "ultima_recoleccion": a["facts_fecha"], "visto": ahora()}
        existe = q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], nombre)
        if existe:
            ej("UPDATE equipos SET " + ", ".join(f"{k}=?" for k in fila) + " WHERE id=?", *fila.values(), existe["id"])
            vistos.add(existe["id"])
        else:
            vistos.add(ej("INSERT INTO equipos(cliente_id, nombre, " + ", ".join(fila) + ") VALUES (?,?," +
                          ",".join("?" * len(fila)) + ")", cliente["id"], nombre, *fila.values()))
    for e in q("SELECT id FROM equipos WHERE cliente_id=?", cliente["id"]):
        if e["id"] not in vistos:
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
    ej("DELETE FROM hallazgos WHERE cliente_id=?", cid)
    segmentos = q("SELECT * FROM segmentos WHERE cliente_id=?", cid)
    hoy = datetime.now(timezone.utc)
    for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cid):
        if e["estado"] == "inventariado":
            continue
        if e["estado"] != "con datos":
            nuevo(cid, e, "recoleccion", "media", "Sin datos del recolector",
                  "El equipo no entregó datos: revisar conexión o credencial", sugerencia="recolectar")
        else:
            try:
                dias = (hoy - datetime.fromisoformat(e["ultima_recoleccion"].replace("Z", "+00:00"))).days
                if dias >= 2:
                    nuevo(cid, e, "recoleccion", "baja", f"Datos de hace {dias} días", "El recolector diario no lo alcanzó", sugerencia="recolectar")
            except (TypeError, ValueError):
                pass
        for p in politicas_de(e, cid, segmentos):
            for c in j(p["controles"], []):
                ok, det = evaluar_control(e, c)
                if ok is False:
                    nuevo(cid, e, "politica", p["severidad"], c["nombre"], det, politica=p["nombre"],
                          sugerencia=c.get("remediacion", ""))


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
