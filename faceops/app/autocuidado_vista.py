"""Monitoreo › Autocuidado de la plataforma (reglas, estado, acciones y su prueba, últimas ejecuciones y el vigía externo),
la campana global de alertas de la plataforma y el webhook que recibe los eventos de EDA. Las páginas solo leen la caché del
ciclo de fondo y la base de FaceOps: nunca esperan a AAP, Prometheus ni Alertmanager."""
from flask import abort, current_app, flash, g, redirect, render_template, request, url_for

from . import autocuidado as ac
from . import requiere_login
from .vistas import _escritura, bp

_inicio = {"hecho": False}


@bp.before_app_request
def _arrancar_ciclo():
    """El ciclo de fondo arranca con la primera petición (necesita la app); no corre en la maqueta ni con el vigía apagado."""
    if _inicio["hecho"]:
        return
    _inicio["hecho"] = True
    import os
    if os.environ.get("FACEOPS_VIGIA", "1") == "1" and os.environ.get("FACEOPS_MAQUETA") != "1":
        ac.iniciar(current_app._get_current_object())


@bp.app_template_global("autocuidado_campana")
def autocuidado_campana():
    try:
        return ac.campana()
    except Exception:  # noqa: BLE001
        return None


@bp.route("/api/autocuidado/evento", methods=["POST"])
def api_autocuidado_evento():
    """Evento de autocuidado que entrega «Autocuidado · Atender alerta» (EDA). Token: sha256 del token del anfitrión
    (/var/lib/faceops/autocuidado/token); sin él, 401. FaceOps no ejecuta nada fuera de AAP: crea el ticket y aplica la política."""
    if not ac.token_valido(request.headers.get("Authorization", "")):
        return {"error": "no autorizado"}, 401
    ev = request.get_json(silent=True) or {}
    ev = {k: str(ev.get(k) or "")[:400] for k in ("regla", "accion", "severidad", "resumen", "objeto", "estado", "job")}
    if not ev["regla"]:
        return {"error": "evento sin regla"}, 400
    return {"ok": True, **ac.recibir(ev)}


@bp.route("/autocuidado")
@requiere_login
def autocuidado():
    cid = g.cliente["id"]
    mon = ac.monitoreo()
    pol = ac.politica()
    reglas = []
    activas = {(a["regla"]) for a in mon["alertas"]}
    for r in pol.get("reglas", []):
        st = mon["reglas"].get(r["id"], {})
        reglas.append(dict(r, estado=("disparando" if r["id"] in activas else st.get("estado") or ("sin cargar" if mon["reglas"] else "sin datos")),
                           salud=st.get("salud", ""), error=st.get("error", ""), accion_texto=pol["acciones"].get(r["accion"], {}).get("texto", r["accion"]),
                           riesgo=pol["acciones"].get(r["accion"], {}).get("riesgo", "")))
    grupos = []
    for r in reglas:
        if not grupos or grupos[-1][0] != r["grupo"]:
            grupos.append((r["grupo"], []))
        grupos[-1][1].append(r)
    return render_template("autocuidado.html", mon=mon, grupos=grupos, acciones=ac.acciones_tabla(cid), ejec=ac.ejecuciones(cid),
                           vigia=ac.vigia_estado(), registro=ac.registro_reciente(), kpi=ac.resumen_metricas(),
                           es_lab=(ac.cliente() or {}).get("id") == cid, escritura=g.perfil["modo"] == "escritura",
                           edad_mon=int(__import__("time").time() - mon["t"]) if mon.get("t") else None)


@bp.route("/autocuidado/pedir", methods=["POST"])
@requiere_login
def autocuidado_pedir():
    _escritura()
    if (ac.cliente() or {}).get("id") != g.cliente["id"]:
        abort(404)
    try:
        t = ac.pedir_manual(g.usuario, g.cliente, request.form.get("accion", ""), request.form.get("modo", "aplicar"))
        flash(f"Pedido {t['numero']}: queda con su aprobación normal", "ok")
        return redirect(url_for("consola.ticket", tid=t["id"]))
    except (ValueError, PermissionError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.autocuidado"))
