"""Salud de cada máquina, plataforma por plataforma (sistema operativo, nginx, PostgreSQL, Redis…) y, dentro de cada una,
sección por sección: todo lo que la pone en riesgo, no solo vulnerabilidades. Cada sección parte en 100 % y descuenta por
lo que se ve ahora; la plataforma combina sus secciones (promedio arrastrado hacia la más débil) y se compara con su umbral
mínimo aceptable. Secciones:
  sistema operativo · Configuración (suscripción, SELinux, parches), Recursos (disco, memoria), Remediaciones preventivas
  (errores conocidos), Actualización (soporte de la versión), CVEs, Políticas de seguridad
  cada plataforma  · Servicio (en ejecución), Remediaciones preventivas (sus errores conocidos)"""
from datetime import datetime, timezone

from .bd import j

UMBRAL = {"rhel": 80, "postgresql": 85, "redis": 80, "nginx": 80, "jboss": 80, "openshift": 85}
UMBRAL_DEFECTO = 80
ACTIVO = {"critica": 50, "alta": 35, "media": 20, "baja": 10}


def nivel(p, umbral=UMBRAL_DEFECTO):
    if p is None:
        return "sin datos"
    return "saludable" if p >= umbral else ("atención" if p >= umbral - 25 else "crítico")


def encendido(e):
    v = (e.get("energia") or "").lower()
    if v.startswith("encendid"):
        return "encendido"
    if v.startswith("apagad"):
        return "apagado"
    if v.startswith("suspend"):
        return "suspendido"
    return "con datos" if e.get("estado") == "con datos" else "desconocido"


def _horas(desde):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(desde)).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def _seccion(clave, nombre, motivos, base=100):
    pts = max(0, base - sum(m["d"] for m in motivos))
    return {"clave": clave, "nombre": nombre, "salud": pts, "motivos": sorted(motivos, key=lambda m: -m["d"])}


def _seccion_cves(cv):
    """CVE proporcional por severidad (criterio en cves.CRITERIO): con 1 decimal para que cada parche se note."""
    from .cves import descuentos
    ms = [{"t": t, "d": d, "nivel": n} for t, d, n in descuentos(cv)]
    s = _seccion("cves", "CVEs", ms)
    s["salud"] = round(max(0.0, 100 - sum(m["d"] for m in ms)), 1)
    return s


# Políticas: proporcional a las que cumple de las que le aplican (8 de 10 al día → 80 %). Una desactualizada (aplicada en una
# versión anterior) vale la mitad; pendiente, fallida o que no cumple no suma.
CREDITO_POL = {"al_dia": 1.0, "desactualizada": 0.5}
CRITERIO_POL = ("Puntaje de políticas: proporcional a las políticas al día sobre las que le aplican a la máquina "
                "(cada una pesa 100 ÷ aplicables; una desactualizada vale la mitad; pendiente, fallida o que no cumple no suma).")


def _seccion_politicas(pols):
    n = len(pols)
    peso = 100.0 / n
    ms, suma = [], 0.0
    for x in pols:
        c = CREDITO_POL.get(x.get("estado"), 0.0)
        suma += c
        if c < 1:
            est = (x.get("estado") or "pendiente").replace("_", " ")
            ms.append({"t": f"{x.get('codigo')} {est}", "d": round(peso * (1 - c), 1),
                       "nivel": "rojo" if x.get("estado") in ("no_cumple", "fallida") else "amarillo"})
    s = _seccion("politicas", "Políticas de seguridad", ms)
    s["salud"] = round(100.0 * suma / n, 1)
    s["al_dia"], s["aplicables"] = sum(1 for x in pols if x.get("estado") == "al_dia"), n
    return s


def _m(t, d, nivel_="amarillo"):
    return {"t": t, "d": int(round(d)), "nivel": nivel_}


def _remediaciones(errs):
    ms = []
    for f in errs:
        if f["activa"]:
            ms.append(_m(f"{f['numero']} activo: {f['activa']}", ACTIVO.get(f["severidad"], 20), "rojo"))
    lat = 0.0
    sinp = 0
    for f in errs:
        if f["activa"]:
            continue
        cub = (f.get("cobertura") or {}).get("estado") == "cubierto"
        sinp += not cub
        lat += float(f.get("prob_ocurrencia") or 0) / 100 * {"critica": 25, "alta": 18, "media": 10, "baja": 5}.get(f["severidad"], 10) * (0.2 if cub else 1)
    if lat >= 1:
        ms.append(_m(f"{sinp} error{'es' if sinp != 1 else ''} conocido{'s' if sinp != 1 else ''} sin prevención aplicada", min(30, lat), "info"))
    return ms


# Servicios que un producto necesita en ejecución (además del principal que lo detecta)
SERVICIOS_PROPIOS = {"wireguard": ["wg-quick@wg0"], "satellite": ["foreman", "httpd", "postgresql", "pulpcore", "tomcat", "redis"]}


def componentes(e, riesgo, productos, extras=None):
    """Una entrada por plataforma: secciones con su %, % de la plataforma, umbral y nivel."""
    extras = extras or {}
    datos = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    discos = j(e.get("discos"), []) if isinstance(e.get("discos"), str) else (e.get("discos") or [])
    serv = j(e.get("servicios"), []) if isinstance(e.get("servicios"), str) else (e.get("servicios") or [])
    serv_txt = " ".join(serv).lower()
    info = {p["codigo"]: p for p in productos}
    out = []
    for cod in (riesgo or {}).get("productos", []):
        p = info.get(cod, {"nombre": cod})
        errs = [f for f in riesgo["errores"] if f["producto"] == p.get("nombre")]
        secs = []
        es_so = cod == "rhel"
        if es_so:
            conf = []
            n = datos.get("parches_seguridad")
            if n:
                conf.append(_m(f"{n} parches de seguridad pendientes", min(40, 8 + int(n) // 4)))
            if datos.get("registrado") is False:
                conf.append(_m("sin suscripción registrada (no recibe parches)", 25))
            se = (datos.get("selinux") or "").lower()
            if se == "disabled":
                conf.append(_m("SELinux desactivado", 25, "rojo"))
            elif se == "permissive":
                conf.append(_m("SELinux en modo permisivo", 12))
            secs.append(_seccion("configuracion", "Sistema operativo", conf))
            rec = []
            mx = max(discos, key=lambda d: d.get("uso") or 0) if discos else None
            if mx and (mx.get("uso") or 0) >= 90:
                rec.append(_m(f"disco {mx.get('punto')} al {mx['uso']} %", 50, "rojo"))
            elif mx and (mx.get("uso") or 0) >= 80:
                rec.append(_m(f"disco {mx.get('punto')} al {mx['uso']} %", 20))
            ram, libre = e.get("ram_mb") or 0, datos.get("ram_disponible_mb") if datos.get("ram_disponible_mb") is not None else datos.get("ram_libre_mb")
            if ram and libre is not None:
                u = 100 * (ram - libre) / ram
                if u >= 95:
                    rec.append(_m(f"memoria al {u:.0f} %", 40, "rojo"))
                elif u >= 90:
                    rec.append(_m(f"memoria al {u:.0f} %", 15))
            secs.append(_seccion("recursos", "Recursos", rec))
            secs.append(_seccion("remediaciones", "Remediaciones preventivas", _remediaciones(errs)))
            ev = extras.get("eval_so")
            if ev:
                sem = ((ev or {}).get("plan") or {}).get("semaforo")
                d = {"rojo": 60, "reconstruir": 70, "amarillo": 30}.get(sem, 0)
                secs.append(_seccion("actualizacion", "Actualización del sistema operativo",
                                     [_m(f"{ev['plan'].get('origen', '')}: {ev['plan'].get('soporte', '')} · semáforo {sem}", d, "rojo" if d >= 60 else "amarillo")] if d else []))
            cv = extras.get("cves")
            if cv and cv.get("revisado") and cv.get("repos"):
                secs.append(_seccion_cves(cv))
            pols = extras.get("politicas")
            if pols:
                secs.append(_seccion_politicas(pols))
        else:
            alias = [a.lower() for a in (p.get("detecta") or [])] + ([p["skill"].lower()] if p.get("skill") else [])
            ms = []
            if serv and not any(a and a in serv_txt for a in alias):
                ms.append(_m("su servicio no aparece en ejecución", 100, "rojo"))
            elif serv:   # productos con varios servicios propios: cada uno detenido resta
                for x in SERVICIOS_PROPIOS.get(cod, []):
                    if x not in serv_txt:
                        ms.append(_m(f"servicio {x} detenido", 25, "rojo"))
            secs.append(_seccion("servicio", "Servicio", ms if serv else []))
            secs.append(_seccion("remediaciones", "Remediaciones preventivas", _remediaciones(errs)))
        vals = [s["salud"] for s in secs]
        pts = round(0.5 * min(vals) + 0.5 * sum(vals) / len(vals)) if vals else None
        um = UMBRAL.get(cod, UMBRAL_DEFECTO)
        for s in secs:
            s["nivel"] = nivel(s["salud"], um)
        motivos = sorted([m for s in secs for m in s["motivos"]], key=lambda m: -m["d"])
        out.append({"codigo": cod, "nombre": p.get("nombre", cod), "so": es_so, "salud": pts, "umbral": um, "nivel": nivel(pts, um),
                    "secciones": secs, "motivos": motivos})
    out.sort(key=lambda c: (not c["so"], c["nombre"]))
    return out


def de_equipo(e, riesgo, productos, extras=None):
    """Salud global: promedio de las plataformas arrastrado hacia la más débil."""
    estado = encendido(e)
    comps = componentes(e, riesgo, productos, extras) if riesgo else []
    if not comps:
        return {"estado": estado, "salud": None, "nivel": "sin datos", "componentes": [], "antiguedad_h": _horas(e.get("ultima_recoleccion"))}
    peor = min(comps, key=lambda c: c["salud"] - c["umbral"])
    prom = sum(c["salud"] for c in comps) / len(comps)
    glob = round(0.5 * min(c["salud"] for c in comps) + 0.5 * prom)
    ant = _horas(e.get("ultima_recoleccion"))
    return {"estado": estado, "salud": glob, "nivel": nivel(glob), "componentes": comps, "peor": peor, "umbral": UMBRAL_DEFECTO,
            "antiguedad_h": ant, "desactualizado": ant is not None and ant > 48}


def componente_sistema(e, extras=None, nombre=None):
    """Sistema del equipo cuando no es RHEL (Windows, VyOS, FortiOS…): mismas secciones que apliquen (recursos, CVE,
    políticas), para que todas las fichas tengan el mismo formato."""
    extras = extras or {}
    datos = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    discos = j(e.get("discos"), []) if isinstance(e.get("discos"), str) else (e.get("discos") or [])
    secs = [_seccion("configuracion", "Sistema operativo", [])]   # mismas secciones que un RHEL, con lo que se puede medir
    rec = []
    mx = max(discos, key=lambda d: d.get("uso") or 0) if discos else None
    if mx and (mx.get("uso") or 0) >= 90:
        rec.append(_m(f"disco {mx.get('punto')} al {mx['uso']} %", 50, "rojo"))
    elif mx and (mx.get("uso") or 0) >= 80:
        rec.append(_m(f"disco {mx.get('punto')} al {mx['uso']} %", 20))
    ram, libre = e.get("ram_mb") or 0, datos.get("ram_libre_mb")
    if ram and libre is not None and 100 * (ram - libre) / ram >= 95:
        rec.append(_m(f"memoria al {100 * (ram - libre) / ram:.0f} %", 40, "rojo"))
    secs.append(_seccion("recursos", "Recursos", rec))
    secs.append(_seccion("remediaciones", "Remediaciones preventivas", []))
    cv = extras.get("cves")
    if cv and cv.get("revisado") and cv.get("repos"):
        secs.append(_seccion_cves(cv))
    pols = extras.get("politicas")
    if pols:
        secs.append(_seccion_politicas(pols))
    vals = [x["salud"] for x in secs]
    pts = round(0.5 * min(vals) + 0.5 * sum(vals) / len(vals))
    for x in secs:
        x["nivel"] = nivel(x["salud"], UMBRAL_DEFECTO)
    return {"salud": pts, "umbral": UMBRAL_DEFECTO, "nivel": nivel(pts), "secciones": secs,
            "motivos": sorted([m for x in secs for m in x["motivos"]], key=lambda m: -m["d"])}


def componente_operador(o):
    """Operador de OpenShift (OLM): mismas fichas que una plataforma. Estado del CSV, aprobación declarada y planes de
    instalación en espera."""
    est = []
    if (o.get("fase") or "") != "Succeeded":
        est.append(_m(f"versión instalada en fase {o.get('fase') or 'sin estado'}", 60, "rojo"))
    act = []
    pend = o.get("pendientes") or []
    if pend:
        act.append(_m(f"{len(pend)} plan(es) de instalación esperando aprobación", min(30, 15 * len(pend))))
    conf = []
    if not o.get("aprob"):
        conf.append(_m("aprobación de actualizaciones sin declarar", 10))
    elif o.get("aprob") == "Automatic":
        conf.append(_m("actualizaciones automáticas (sin ventana de cambio)", 8))
    secs = [_seccion("estado", "Estado del operador", est), _seccion("actualizaciones", "Actualizaciones", act),
            _seccion("configuracion", "Configuración", conf)]
    vals = [x["salud"] for x in secs]
    pts = round(0.5 * min(vals) + 0.5 * sum(vals) / len(vals))
    for x in secs:
        x["nivel"] = nivel(x["salud"], UMBRAL_DEFECTO)
    return {"salud": pts, "umbral": UMBRAL_DEFECTO, "nivel": nivel(pts), "secciones": secs,
            "motivos": sorted([m for x in secs for m in x["motivos"]], key=lambda m: -m["d"])}


def componente_nodo_ocp(n):
    """OpenShift en uno de sus nodos: estado del nodo en el clúster y su uso de recursos."""
    est = []
    if not n.get("listo"):
        est.append(_m("el nodo no está Ready en el clúster", 60, "rojo"))
    if n.get("programable") is False:
        est.append(_m("nodo cordonado (no recibe pods nuevos)", 25))
    rec = []
    for et, u, t in (("CPU", n.get("cpu_uso"), n.get("cpu_asignable")), ("memoria", n.get("memoria_uso_gb"), n.get("memoria_asignable_gb"))):
        try:
            p = 100 * float(u) / float(t)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if p >= 90:
            rec.append(_m(f"{et} al {p:.0f} % de lo asignable", 30, "rojo"))
        elif p >= 80:
            rec.append(_m(f"{et} al {p:.0f} % de lo asignable", 10))
    secs = [_seccion("estado", "Estado del nodo", est), _seccion("recursos", "Recursos", rec)]
    vals = [x["salud"] for x in secs]
    pts = round(0.5 * min(vals) + 0.5 * sum(vals) / len(vals))
    for x in secs:
        x["nivel"] = nivel(x["salud"], UMBRAL_DEFECTO)
    return {"salud": pts, "umbral": UMBRAL_DEFECTO, "nivel": nivel(pts), "secciones": secs,
            "motivos": sorted([m for x in secs for m in x["motivos"]], key=lambda m: -m["d"])}
