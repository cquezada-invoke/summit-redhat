"""Políticas de desarrollo › Cumplimiento de aplicaciones: cruza la evidencia que levanta la acción «Políticas de desarrollo ·
Evaluar aplicaciones» (IIS, JBoss EAP, Spring Boot y Quarkus ya instalados) con las políticas activas que aplican a cada
plataforma. Cada política queda como cumple, no cumple o revisión (no se puede comprobar desde la máquina)."""
import json
import re

from .bd import ahora, ej, q, q1

AUTO_EVALUAR = "Políticas de desarrollo · Evaluar aplicaciones"
ESTADOS = {"cumple": "Cumple", "no_cumple": "No cumple", "revision": "Revisión"}


def _tabla():
    ej("CREATE TABLE IF NOT EXISTS politicas_eval (id INTEGER PRIMARY KEY, cliente_id INTEGER, ticket_id INTEGER, fecha TEXT, equipo TEXT, "
       "plataforma TEXT, app TEXT, instalacion TEXT, producto TEXT, en_ejecucion INTEGER, codigo TEXT, estado TEXT, evidencia TEXT)")


def _ver(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def _java(a):
    j = _ver(a.get("java"))
    if not j:
        return None, ""
    return j[0] >= 17, f"Java {a.get('java')}"


def _stack(a):
    p = a["plataforma"]
    jok, jtxt = _java(a)
    if p == "jboss":
        prod = a.get("producto") or ""
        m = re.search(r"Version (\d+\.\d+)", prod)
        if "Enterprise Application Platform" not in prod or not m:
            return "no_cumple", f"{prod or 'JBoss sin versión de EAP'}: el manifiesto acepta JBoss EAP 7.4 u 8"
        if _ver(m.group(1)) < (7, 4):
            return "no_cumple", f"JBoss EAP {m.group(1)}: fuera de soporte (mínimo 7.4)"
        if jok is False:
            return "no_cumple", f"JBoss EAP {m.group(1)} sobre {jtxt}: el manifiesto pide Java 17 o superior"
        return "cumple", f"JBoss EAP {m.group(1)}" + (f" sobre {jtxt}" if jtxt else " (Java sin leer: el servidor está detenido)")
    if p == "quarkus":
        if _ver(a.get("version")) and _ver(a.get("version"))[0] < 3:
            return "no_cumple", f"Quarkus {a.get('version')}: el manifiesto pide Quarkus 3"
        if jok is False:
            return "no_cumple", f"Quarkus sobre {jtxt}: el manifiesto pide Java 17 o superior"
        return "cumple", f"{a.get('producto') or 'Quarkus'} {('sobre ' + jtxt) if jtxt else ''}".strip()
    if p == "springboot":
        return "no_cumple", f"{a.get('producto') or 'Spring Boot'}{(' sobre ' + jtxt) if jtxt else ''}: el manifiesto acepta Java con Quarkus o JBoss EAP"
    if p == "iis":
        return "no_cumple", f"{a.get('producto') or '.NET'} en IIS: fuera del stack aceptado por el manifiesto (Python, Node.js, Java con Quarkus o JBoss EAP)"
    return None


def _marca(campo, si, no, detalle=""):
    def f(a):
        if a.get(campo) is None:
            return None
        return ("cumple", si) if a.get(campo) else ("no_cumple", no + (f": {a[detalle]}" if detalle and a.get(detalle) else ""))
    return f


def _negada(campo, ok, malo, detalle=""):
    def f(a):
        if a.get(campo) is None:
            return None
        return ("no_cumple", malo + (f": {a[detalle]}" if detalle and a.get(detalle) else "")) if a.get(campo) else ("cumple", ok)
    return f


def _libs(a):
    v = a.get("libs_vulnerables") or []
    if v:
        return "no_cumple", "Librerías con vulnerabilidades conocidas: " + ", ".join(v[:8])
    return "cumple", f"{a.get('librerias') or 0} librerías revisadas, sin versiones vulnerables conocidas"


def _tls(a):
    if a["plataforma"] == "iis":
        if a.get("tls"):
            return "cumple", "Sitio con enlace HTTPS y cookies con requireSSL"
        falta = [x for x, ok in (("sin enlace HTTPS", a.get("https")), ("cookies sin requireSSL", a.get("cookies_seguras"))) if not ok]
        return "no_cumple", ", ".join(falta)
    return ("cumple", "Atiende por HTTPS o detrás de un proxy TLS declarado") if a.get("tls") else ("no_cumple", "Sin HTTPS ni proxy TLS declarado en la configuración")


COMPROBACION = {
    "PD-05": _negada("secreto_plano", "Sin claves ni contraseñas en texto plano en la configuración", "Secretos en texto plano", "secreto_detalle"),
    "PD-06": _marca("health", "Sondas de salud habilitadas", "Sin sondas de salud (health)"),
    "PD-10": _marca("log_json", "Logs en formato JSON", "Logs sin formato JSON"),
    "PD-11": _marca("metricas", "Métricas expuestas para Prometheus", "Sin métricas para Prometheus"),
    "PD-13": _libs,
    "PD-19": _stack,
    "PD-20": _negada("depuracion", "Sin modo depuración ni errores detallados", "Modo depuración o errores detallados", "depuracion_detalle"),
    "PD-21": _negada("consola_expuesta", "Consolas y endpoints de administración no expuestos", "Consola o endpoints de administración expuestos"),
    "PD-22": _tls,
}


def aplicaciones(artefactos):
    """Artefactos del job → [app con equipo], y los errores por máquina."""
    apps, errores = [], {}
    for host, d in ((artefactos or {}).get("faceops_politicas_apps") or {}).items():
        try:
            lst = json.loads(d.get("apps") or "[]")
        except ValueError:
            lst, errores[host] = [], "salida no legible"
        if isinstance(lst, dict):
            lst = [lst]
        for a in lst:
            a["equipo"] = host
            apps.append(a)
        if d.get("error") and not lst:
            errores[host] = d["error"][:200]
    return apps, errores


def evaluar(cliente_id, apps):
    from .politicas_dev import aplica, lista
    pols = [p for p in lista(cliente_id) if p["activa"]]
    filas = []
    for a in apps:
        for p in pols:
            if a.get("plataforma") not in aplica(p):
                continue
            r = COMPROBACION.get(p["codigo"], lambda _a: None)(a)
            estado, ev = r if r else ("revision", "Requiere revisión del código (no se comprueba desde la máquina)")
            filas.append({**a, "codigo": p["codigo"], "estado": estado, "evidencia": ev})
    return filas


def guardar(t, artefactos):
    """Post-ticket: reemplaza la evaluación vigente del cliente por la del ticket."""
    _tabla()
    apps, _ = aplicaciones(artefactos)
    filas = evaluar(t["cliente_id"], apps)
    ej("DELETE FROM politicas_eval WHERE cliente_id=?", t["cliente_id"])
    f_ = ahora()
    for r in filas:
        ej("INSERT INTO politicas_eval (cliente_id, ticket_id, fecha, equipo, plataforma, app, instalacion, producto, en_ejecucion, codigo, estado, evidencia) "
           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", t["cliente_id"], t["id"], f_, r["equipo"], r["plataforma"], r.get("app", ""), r.get("instalacion", ""),
           r.get("producto", ""), 1 if r.get("en_ejecucion") else 0, r["codigo"], r["estado"], r["evidencia"][:500])
    return len(filas)


def resumen(cliente_id, equipos=None):
    """Cumplimiento por aplicación (de la última evaluación), con sus políticas."""
    _tabla()
    filas = q("SELECT * FROM politicas_eval WHERE cliente_id=? ORDER BY equipo, app, codigo", cliente_id)
    if equipos is not None:
        filas = [f for f in filas if f["equipo"] in equipos]
    apps = {}
    for f in filas:
        k = (f["equipo"], f["app"])
        a = apps.setdefault(k, {"equipo": f["equipo"], "app": f["app"], "plataforma": f["plataforma"], "producto": f["producto"],
                                "instalacion": f["instalacion"], "en_ejecucion": f["en_ejecucion"], "politicas": [],
                                "cumple": 0, "no_cumple": 0, "revision": 0})
        a["politicas"].append(f)
        a[f["estado"]] += 1
    lst = sorted(apps.values(), key=lambda a: (-a["no_cumple"], a["equipo"], a["app"]))
    for a in lst:
        medibles = a["cumple"] + a["no_cumple"]
        a["pct"] = round(100 * a["cumple"] / medibles) if medibles else None
    ult = q1("SELECT fecha, ticket_id FROM politicas_eval WHERE cliente_id=? ORDER BY id DESC LIMIT 1", cliente_id)
    return {"apps": lst, "fecha": ult["fecha"] if ult else "", "ticket_id": ult["ticket_id"] if ult else None,
            "no_cumple": sum(a["no_cumple"] for a in lst), "con_brechas": sum(1 for a in lst if a["no_cumple"])}
