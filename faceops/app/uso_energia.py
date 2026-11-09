"""Horas encendida y recursos consumidos por cada máquina (virtual o física), a partir del historial de energía de FaceOps.
El historial se llena solo (disparador en equipos.energia); para lo anterior al registro se usa el tiempo encendido que informa
el propio sistema (uptime del último inventario). En máquinas de nube se estima el costo con la tarifa del proveedor."""
from datetime import datetime, timedelta, timezone

from .bd import ej, q, q1

# Tarifas por hora observadas en la facturación de GCP (CLP, región Santiago); se usan solo para máquinas en GCP
TARIFA_GCP = {"n2": {"core": 44.3, "gib": 5.94}, "e2": {"core": 30.6, "gib": 4.1}, "rhel": 10.6}
NUBES = ("gcp", "google", "aws", "amazon", "azure", "oci", "oracle cloud")


def _t(s):
    try:
        d = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _sembrar(e, d):
    """Primer tramo de una máquina sin historial: desde que FaceOps la vio cambiar de estado o, si está encendida, desde que
    arrancó según su uptime (lo que sea más antiguo)."""
    if q1("SELECT 1 FROM energia_historial WHERE equipo_id=?", e["id"]) or not e.get("energia"):
        return
    ahora = datetime.now(timezone.utc)
    desde = _t(e.get("energia_cambio")) or ahora
    if e["energia"] == "encendido" and d.get("uptime_dias"):
        desde = min(desde, ahora - timedelta(days=float(d["uptime_dias"])))
    ej("INSERT INTO energia_historial(equipo_id, estado, desde) VALUES (?,?,?)", e["id"], e["energia"], desde.isoformat(timespec="seconds"))


def uso(e, d=None):
    """{horas, horas_mes, desde, cores, ram_gib, core_h, gib_h, fisica, nube, costo_clp, costo_mes_clp, tarifa} o None."""
    d = d or {}
    _sembrar(e, d)
    tramos = q("SELECT estado, desde, hasta FROM energia_historial WHERE equipo_id=? ORDER BY desde", e["id"])
    if not tramos:
        return None
    ahora = datetime.now(timezone.utc)
    ini_mes = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    horas = horas_mes = 0.0
    for t in tramos:
        if t["estado"] != "encendido":
            continue
        a, b = _t(t["desde"]), _t(t["hasta"]) or ahora
        if not a or b <= a:
            continue
        horas += (b - a).total_seconds() / 3600
        if b > ini_mes:
            horas_mes += (b - max(a, ini_mes)).total_seconds() / 3600
    fisica = (e.get("forma") or "") == "física" or (e.get("forma") or "") == "fisica"
    cores = (d.get("hardware") or {}).get("cores") if fisica else None
    cores = cores or e.get("cpu") or 0
    ram = round((e.get("ram_mb") or 0) / 1024, 1)
    ubic = f"{e.get('ubicacion') or ''} {d.get('proveedor') or ''} {d.get('nube') or ''}".lower()
    nube = next((n for n in NUBES if n in ubic), None)
    r = {"horas": round(horas, 1), "horas_mes": round(horas_mes, 1), "desde": tramos[0]["desde"][:10], "cores": cores, "ram_gib": ram,
         "core_h": round(horas * (cores or 0)), "gib_h": round(horas * ram), "fisica": fisica, "nube": nube,
         "encendida": e.get("energia") == "encendido", "costo_clp": None, "costo_mes_clp": None, "tarifa": ""}
    if nube in ("gcp", "google") and (cores or ram):
        tipo = (d.get("tipo_maquina") or d.get("machine_type") or "n2").split("/")[-1][:2]
        tf = TARIFA_GCP.get(tipo, TARIFA_GCP["n2"])
        por_h = (cores or 0) * tf["core"] + ram * tf["gib"] + (TARIFA_GCP["rhel"] if "red hat" in (e.get("so") or "").lower() else 0)
        r.update(costo_clp=round(horas * por_h), costo_mes_clp=round(horas_mes * por_h), tarifa=f"{round(por_h)} CLP/h ({tipo.upper()}, Santiago)")
    return r
