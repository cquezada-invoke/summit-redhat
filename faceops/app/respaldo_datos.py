"""Respaldo de los volúmenes de datos con restic («Recuperación · Respaldar volúmenes con restic»).

FaceOps nunca entra a la máquina: la automatización corre en AAP y deja su resultado en set_stats
(faceops_respaldo_datos[<equipo>]). Al terminar, FaceOps lo guarda en la ficha (datos.respaldo_datos: herramienta,
snapshot, fecha, rutas…), que es lo que leen los volúmenes del punto de restauración (app/puntos.py: volumenes) y el pilar
Recuperación del radar, y registra un punto de restauración nuevo con el respaldo."""
import json

from .bd import ahora, ej, j, q1

AUTOMATIZACION = "Recuperación · Respaldar volúmenes con restic"
PUERTO_S3 = 9000


def destino_s3(cliente_id):
    """Endpoint del S3 del laboratorio: el equipo donde terminó bien «Storage · Servicio S3 del data lake»."""
    t = q1("SELECT t.limite FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND "
           "a.nombre_aap='Storage · Servicio S3 del data lake' AND t.estado='exitoso' AND t.parametros NOT LIKE '%\"estado\"%' "
           "ORDER BY t.id DESC LIMIT 1", cliente_id)
    n = ((t or {}).get("limite") or "").split(":")[0]
    e = q1("SELECT ip FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cliente_id, n) if n else None
    return f"http://{e['ip']}:{PUERTO_S3}" if e and e.get("ip") else ""


def preparar(cliente_id, parametros):
    """Sin S3 indicado (p. ej. desde el flotante de salud): el del laboratorio."""
    p = dict(parametros or {})
    if not str(p.get("rr_s3") or "").strip():
        s3 = destino_s3(cliente_id)
        if s3:
            p["rr_s3"] = s3
    return p


def registrar(cliente, t, art):
    """Lo que dejó la automatización en cada equipo → ficha y punto de restauración. Devuelve el texto para el ticket."""
    out = []
    for n, r in ((art or {}).get("faceops_respaldo_datos") or {}).items():
        e = q1("SELECT * FROM equipos WHERE cliente_id=? AND (nombre=? OR nombre LIKE ?) AND estado!='retirado'", cliente["id"], n, n.split(".")[0] + ".%") \
            or q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cliente["id"], n.split(".")[0])
        if not e or not isinstance(r, dict):
            continue
        d = j(e["datos"], {})
        prev = d.get("respaldo_datos") or {}
        modo = r.get("modo") or "respaldar"
        if modo == "restaurar":
            d["respaldo_datos"] = dict(prev, ultima_restauracion={"snapshot": r.get("restaurado", ""), "directorio": r.get("directorio", ""),
                                                                  "archivos": r.get("archivos_restaurados"), "canario": r.get("canario"),
                                                                  "fecha": ahora(), "ticket": t["numero"]})
            out.append(f"{e['nombre']}: snapshot {str(r.get('restaurado', ''))[:8]} restaurado en {r.get('directorio', '')} "
                       f"({r.get('archivos_restaurados')} archivos, canario {'ok' if r.get('canario') else 'falta'})")
        elif r.get("estado") == "fallido":
            out.append(f"{e['nombre']}: el respaldo falló · {r.get('vuelta_atras', '')}")
            continue   # la ficha conserva el último respaldo bueno
        elif modo == "estado" and not r.get("configurado"):
            out.append(f"{e['nombre']}: sin respaldo restic configurado")
            continue
        else:
            nuevo = {k: r.get(k) for k in ("herramienta", "version", "snapshot", "fecha", "rutas", "repositorio", "snapshots", "verificacion",
                                           "programa", "proxima", "retencion", "boveda", "nota") if r.get(k) not in (None, "")}
            if modo == "estado":
                nuevo = dict({k: v for k, v in prev.items() if k not in ("snapshot", "fecha", "snapshots", "proxima")}, **nuevo)
            nuevo.update(ticket=t["numero"], registrado=ahora())
            if prev.get("ultima_restauracion"):
                nuevo["ultima_restauracion"] = prev["ultima_restauracion"]
            d["respaldo_datos"] = nuevo
            out.append(f"{e['nombre']}: " + (f"restic {str(nuevo.get('snapshot') or '')[:8]} · {', '.join(nuevo.get('rutas') or [])} · "
                                             f"{nuevo.get('programa', '')} · {nuevo.get('retencion', '')}" if nuevo.get("rutas") else (nuevo.get("nota") or "sin volúmenes de datos")))
        ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d, ensure_ascii=False), e["id"])
        try:   # el respaldo de datos es un eslabón del punto de restauración: punto nuevo si cambió
            from .puntos import crear
            p = crear(cliente, dict(e, datos=json.dumps(d)), origen=f"respaldo de datos {t['numero']}", usuario=t.get("autor") or "", si_cambio=True)
            if p:
                out[-1] += f" · punto {p['codigo']}"
        except Exception as x:  # noqa: BLE001
            out[-1] += f" · sin punto de restauración ({str(x)[:120]})"
    return "\n".join(out)
