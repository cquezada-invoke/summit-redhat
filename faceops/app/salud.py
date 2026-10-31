"""Salud de cada máquina, componente por componente (sistema operativo y cada plataforma instalada: nginx, PostgreSQL,
Redis…). Cada componente parte en 100 y descuenta por lo que se ve ahora (errores conocidos activos, servicio no visto,
disco, parches, suscripción) y, en menor medida, por los errores conocidos que le aplican sin prevención. La salud global
de la máquina combina sus componentes: el promedio, arrastrado hacia el peor (una plataforma caída enferma a la máquina)."""
from datetime import datetime, timezone

from .bd import j

ACTIVO = {"critica": 45, "alta": 30, "media": 18, "baja": 8}     # error conocido con su condición activa ahora
LATENTE_MAX = 12                                                # tope de lo que restan los errores aplicables sin prevención


def nivel(p):
    if p is None:
        return "sin datos"
    return "saludable" if p >= 85 else ("atención" if p >= 65 else "crítico")


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


def _latente(errs):
    """Errores que le aplican, sin activarse, y sin su prevención ejecutada: restan poco y con tope."""
    s = 0.0
    for f in errs:
        if f["activa"]:
            continue
        cub = (f.get("cobertura") or {}).get("estado") == "cubierto"
        s += float(f.get("prob_ocurrencia") or 0) / 100 * {"critica": 10, "alta": 7, "media": 4, "baja": 2}.get(f["severidad"], 4) * (0.25 if cub else 1)
    return min(LATENTE_MAX, round(s))


def componentes(e, riesgo, productos):
    """Una entrada por componente: puntaje 0-100, nivel y los motivos (lo que le restó)."""
    datos = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    discos = j(e.get("discos"), []) if isinstance(e.get("discos"), str) else (e.get("discos") or [])
    serv = j(e.get("servicios"), []) if isinstance(e.get("servicios"), str) else (e.get("servicios") or [])
    serv_txt = " ".join(serv).lower()
    info = {p["codigo"]: p for p in productos}
    out = []
    for cod in (riesgo or {}).get("productos", []):
        p = info.get(cod, {"nombre": cod})
        errs = [f for f in riesgo["errores"] if f["producto"] == p.get("nombre")]
        motivos, pts = [], 100
        for f in errs:
            if f["activa"]:
                d = ACTIVO.get(f["severidad"], 18)
                pts -= d
                motivos.append({"t": f"{f['numero']} activo: {f['activa']}", "d": d, "nivel": "rojo"})
        es_so = cod == "rhel"
        if es_so:
            mx = max(discos, key=lambda d: d.get("uso") or 0) if discos else None
            if mx and (mx.get("uso") or 0) >= 90:
                pts -= 20; motivos.append({"t": f"disco {mx.get('punto')} al {mx['uso']} %", "d": 20, "nivel": "rojo"})
            elif mx and (mx.get("uso") or 0) >= 80:
                pts -= 8; motivos.append({"t": f"disco {mx.get('punto')} al {mx['uso']} %", "d": 8, "nivel": "amarillo"})
            n = datos.get("parches_seguridad")
            if n:
                d = min(15, 3 + int(n) // 5)
                pts -= d; motivos.append({"t": f"{n} parches de seguridad pendientes", "d": d, "nivel": "amarillo"})
            if datos.get("registrado") is False:
                pts -= 8; motivos.append({"t": "sin suscripción registrada (no recibe parches)", "d": 8, "nivel": "amarillo"})
            if (datos.get("selinux") or "").lower() == "disabled":
                pts -= 4; motivos.append({"t": "SELinux desactivado", "d": 4, "nivel": "amarillo"})
        else:
            alias = [a.lower() for a in (p.get("detecta") or [])] + ([p["skill"].lower()] if p.get("skill") else [])
            if serv and not any(a and a in serv_txt for a in alias):
                pts -= 35; motivos.append({"t": "su servicio no aparece en ejecución", "d": 35, "nivel": "rojo"})
        lat = _latente(errs)
        if lat:
            pts -= lat
            sinp = sum(1 for f in errs if not f["activa"] and (f.get("cobertura") or {}).get("estado") != "cubierto")
            motivos.append({"t": f"{sinp} error{'es' if sinp != 1 else ''} conocido{'s' if sinp != 1 else ''} sin prevención aplicada", "d": lat, "nivel": "info"})
        pts = max(0, pts)
        out.append({"codigo": cod, "nombre": p.get("nombre", cod), "so": es_so, "salud": pts, "nivel": nivel(pts),
                    "motivos": sorted(motivos, key=lambda m: -m["d"])})
    out.sort(key=lambda c: (not c["so"], c["nombre"]))
    return out


def de_equipo(e, riesgo, productos):
    """Salud global: promedio de los componentes arrastrado hacia el peor. Apagado o sin datos recientes: se informa aparte."""
    estado = encendido(e)
    comps = componentes(e, riesgo, productos) if riesgo else []
    if not comps:
        return {"estado": estado, "salud": None, "nivel": "sin datos", "componentes": [], "antiguedad_h": _horas(e.get("ultima_recoleccion"))}
    peor = min(c["salud"] for c in comps)
    prom = sum(c["salud"] for c in comps) / len(comps)
    glob = round(0.5 * peor + 0.5 * prom)
    ant = _horas(e.get("ultima_recoleccion"))
    return {"estado": estado, "salud": glob, "nivel": nivel(glob), "componentes": comps, "peor": min(comps, key=lambda c: c["salud"]),
            "antiguedad_h": ant, "desactualizado": ant is not None and ant > 48}
