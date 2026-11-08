"""Inventario completo de una máquina: AAP trae su información (datos del sistema a la caché de facts y paquetes por
set_stats); FaceOps la lee desde AAP —no se conecta a la máquina—, sincroniza su inventario y la contrasta con su propia
configuración y metadata: qué cambió desde el inventario anterior y qué no calza (observaciones)."""
import json
from datetime import datetime, timezone

from .bd import ahora, ej, j, q, q1

AUTOMATIZACION = "Sistema · Inventariar máquina"
VIGENTE_DIAS, VIEJO_DIAS = 1, 7


def _instante(e):
    d = j(e.get("datos"), {})
    return {"so": e.get("so") or "", "kernel": d.get("kernel") or "", "ip": e.get("ip") or "", "cpu": e.get("cpu"), "ram_mb": e.get("ram_mb"),
            "servicios": set(j(e.get("servicios"), [])), "paquetes": {p["n"]: p["v"] for p in ((d.get("paquetes") or {}).get("lista") or [])}}


def guardar_paquetes(cliente_id, ticket, art):
    """Lista de paquetes (con su fuente) de cada máquina, desde el set_stats del trabajo."""
    tot_sub, n = 0, 0
    for host_, d_ in (art or {}).items():
        e_ = q1("SELECT id, datos FROM equipos WHERE cliente_id=? AND nombre=?", cliente_id, host_)
        if not e_:
            continue
        dat_ = j(e_["datos"], {})
        dat_["paquetes"] = {"fecha": ahora(), "ticket": ticket["numero"], "gestor": d_.get("gestor", ""), "lista": d_.get("paquetes") or [],
                            "subidos": d_.get("subidos", 0), "errores": d_.get("errores", 0), "pendientes": d_.get("pendientes", 0)}
        ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(dat_, ensure_ascii=False), e_["id"])
        tot_sub += int(d_.get("subidos") or 0)
        n += len(d_.get("paquetes") or [])
    try:
        from .paquetes import limpiar_cache
        limpiar_cache()
    except Exception:  # noqa: BLE001
        pass
    return n, tot_sub


def cobertura(lineas, g_=None):
    """Lo que la instantánea no guarda en Git y con qué mecanismo queda cubierto (líneas COB_/ART_ del inventario)."""
    c = {"auto": [], "rutas": [], "sin_paquete": [], "secretos": [], "volumenes": [], "artefactos": {}, "errores": [], "omitidos": 0}
    for ln in lineas:
        k, _, v = ln.partition("=")
        p = v.split("|")
        if k == "COB_AUTO":
            c["auto"].append({"ruta": p[0], "mb": int(p[1] or 0) if len(p) > 1 and p[1].isdigit() else 0})
        elif k in ("COB_RUTA", "COB_SINPAQ"):
            c["rutas" if k == "COB_RUTA" else "sin_paquete"].append({"ruta": p[0], "mb": int(p[1]) if len(p) > 1 and p[1].isdigit() else 0})
        elif k == "COB_SECRETO":
            c["secretos"].append(v)
        elif k == "COB_VOL":
            c["volumenes"].append({"montaje": p[0], "fs": p[1] if len(p) > 1 else "", "mb": int(p[2]) if len(p) > 2 and p[2].isdigit() else 0})
        elif k in ("ART_OK", "ART_SUBIDO"):
            c["artefactos"][p[0]] = p[1] if len(p) > 1 else ""
        elif k == "ART_ERROR":
            c["errores"].append(p[0])
        elif k == "OMITIDOS" and v.isdigit():
            c["omitidos"] = int(v)
    c["boveda"] = (g_ or {}).get("SECRETOS_BOVEDA") or ""
    c["n_secretos"] = int((g_ or {}).get("SECRETOS") or 0)
    return c


def _cambios(antes, despues):
    out = []
    for k, t in (("so", "Sistema operativo"), ("kernel", "Kernel"), ("ip", "IP"), ("cpu", "vCPU"), ("ram_mb", "Memoria (MB)")):
        if antes[k] not in ("", None) and despues[k] not in ("", None) and antes[k] != despues[k]:
            out.append(f"{t}: {antes[k]} → {despues[k]}")
    nuevos, quitados = despues["servicios"] - antes["servicios"], antes["servicios"] - despues["servicios"]
    if antes["servicios"] and (nuevos or quitados):
        out.append("Servicios" + (f" · nuevos: {', '.join(sorted(nuevos)[:8])}" if nuevos else "") + (f" · ya no corren: {', '.join(sorted(quitados)[:8])}" if quitados else ""))
    pa, pd = antes["paquetes"], despues["paquetes"]
    if pa and pd:
        inst, quit_ = sorted(set(pd) - set(pa)), sorted(set(pa) - set(pd))
        act = sorted(n for n in set(pa) & set(pd) if pa[n] != pd[n])
        if inst:
            out.append(f"Paquetes instalados ({len(inst)}): {', '.join(inst[:10])}{'…' if len(inst) > 10 else ''}")
        if quit_:
            out.append(f"Paquetes desinstalados ({len(quit_)}): {', '.join(quit_[:10])}{'…' if len(quit_) > 10 else ''}")
        if act:
            out.append(f"Paquetes actualizados ({len(act)}): " + ", ".join(f"{n} {pa[n]}→{pd[n]}" for n in act[:6]) + ("…" if len(act) > 6 else ""))
    return out


def observaciones(e, segmentos=None):
    """Lo que no calza entre la máquina real y lo que FaceOps sabe o espera de ella."""
    from .identidad import NOMBRES_PLAT
    obs = []
    d = j(e.get("datos"), {})
    serv = set(j(e.get("servicios"), []))
    try:
        from .vistas import SERVICIOS_PLATAFORMA, familia
        from .inventario import skills_de
        for sk in skills_de(e):
            fam = familia(sk)
            uni = SERVICIOS_PLATAFORMA.get(fam)
            if uni and serv and not any(any(u in s for u in uni) for s in serv):
                obs.append({"nivel": "alto", "t": f"{NOMBRES_PLAT.get(sk, sk)} figura como plataforma pero su servicio no está en ejecución"})
    except Exception:  # noqa: BLE001
        pass
    p = d.get("paquetes")
    if p and p.get("lista"):
        try:
            from .paquetes import de_equipo
            pe = de_equipo(d)
            falta = sum(1 for x in pe["lista"] if x["respaldo"] is False and not x["local"])
            if falta:
                obs.append({"nivel": "medio", "t": f"{falta} paquete(s) aún no están respaldados en el registro propio"})
            loc = sum(1 for x in pe["lista"] if x["local"])
            if loc:
                obs.append({"nivel": "bajo", "t": f"{loc} paquete(s) instalados a mano, sin repositorio de origen"})
        except Exception:  # noqa: BLE001
            pass
    if d.get("registrado") is False:
        obs.append({"nivel": "alto", "t": "Sin suscripción registrada: no recibe parches de Red Hat"})
    if d.get("parches_seguridad"):
        obs.append({"nivel": "medio", "t": f"{d['parches_seguridad']} parche(s) de seguridad pendientes"})
    try:
        from .boveda import disponible, maquinas_con_credencial
        if disponible() and e["nombre"] not in maquinas_con_credencial([e["nombre"]]):
            obs.append({"nivel": "medio", "t": "No está vinculada a la bóveda"})
    except Exception:  # noqa: BLE001
        pass
    try:
        from .ciclo_vida import evaluar
        ev = evaluar(dict(e, _datos=d))
        if ev and ev.get("riesgo"):
            obs.append({"nivel": "alto" if ev["fase"] in ("fuera", "extendido") else "medio",
                        "t": f"{ev['nombre']} {ev['version']}: {'fuera de soporte' if ev['fase'] == 'fuera' else 'cerca del fin de su fase de soporte'}"})
    except Exception:  # noqa: BLE001
        pass
    if segmentos is not None and not segmentos and (e.get("categoria") or "servidor") == "servidor":
        obs.append({"nivel": "bajo", "t": "No pertenece a ningún segmento de negocio"})
    return obs


def tras_inventariar(aap, cliente, t, art):
    """Al terminar «Inventariar máquina»: sincroniza desde AAP, guarda los paquetes y anota cambios y observaciones."""
    from .inventario import segmentos_de, sincronizar
    nombres = [x for x in (t.get("limite") or "").split(":") if x]
    antes = {e["nombre"]: _instante(e) for e in q("SELECT * FROM equipos WHERE cliente_id=?", cliente["id"]) if e["nombre"] in nombres}
    try:
        sincronizar(cliente, "FaceOps")
    except Exception as x:  # noqa: BLE001
        print("Inventariar · sincronizar:", str(x)[:200])
    n_paq, n_sub = guardar_paquetes(cliente["id"], t, art.get("faceops_paquetes") or {})
    git_ = art.get("faceops_inventario_git") or {}
    segs = q("SELECT * FROM segmentos WHERE cliente_id=?", cliente["id"])
    for e in q("SELECT * FROM equipos WHERE cliente_id=?", cliente["id"]):
        if e["nombre"] not in nombres:
            continue
        d = j(e["datos"], {})
        ahora_ = _instante(e)
        g_ = git_.get(e["nombre"]) or git_.get(next((k for k in git_ if k.split(".")[0] == e["nombre"]), ""), {})
        if g_.get("REPO"):   # instantánea de archivos en el repositorio propio de la máquina (rama master)
            prev_ = d.get("git") or {}
            d["git"] = {"repo": g_["REPO"], "rama": g_.get("RAMA") or "master", "url": g_.get("URL", ""), "archivos": int(g_.get("ARCHIVOS") or 0),
                        "commit": g_.get("COMMIT") or prev_.get("commit", ""), "cambios": int(g_.get("CAMBIOS") or 0), "fecha": ahora(), "ticket": t["numero"],
                        "cobertura": cobertura(g_.get("archivo") or [], g_)}
        d["inventario"] = {"fecha": ahora(), "ticket": t["numero"], "cambios": _cambios(antes.get(e["nombre"]) or ahora_, ahora_),
                           "observaciones": observaciones(dict(e, datos=json.dumps(d)), segmentos_de(e, segs)),
                           "partes": {"sistema": e.get("ultima_recoleccion") or "", "paquetes": (d.get("paquetes") or {}).get("fecha", "")}}
        ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d, ensure_ascii=False), e["id"])
        try:   # punto de restauración automático si el estado completo cambió
            from .puntos import crear
            crear(cliente, dict(e, datos=json.dumps(d)), origen=f"inventario {t['numero']}", usuario=t.get("autor") or "", si_cambio=True)
        except Exception as x:  # noqa: BLE001
            print("Punto de restauración:", str(x)[:200])
    return n_paq, n_sub


def antiguedad(e):
    """{'dias', 'nivel' (verde|amarillo|rojo|gris), 'fecha', 'completo'} del último inventario de la máquina."""
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    inv = d.get("inventario") or {}
    f = inv.get("fecha") or e.get("ultima_recoleccion") or ""
    if not f:
        return {"dias": None, "nivel": "gris", "fecha": "", "completo": False, "obs": 0}
    try:
        dias = (datetime.now(timezone.utc) - datetime.fromisoformat(str(f).replace("Z", "+00:00"))).days
    except ValueError:
        return {"dias": None, "nivel": "gris", "fecha": f, "completo": False, "obs": 0}
    nivel = "verde" if dias <= VIGENTE_DIAS else ("amarillo" if dias <= VIEJO_DIAS else "rojo")
    return {"dias": dias, "nivel": nivel if inv else ("amarillo" if nivel == "verde" else nivel), "fecha": f, "completo": bool(inv),
            "obs": len(inv.get("observaciones") or []), "ticket": inv.get("ticket")}
