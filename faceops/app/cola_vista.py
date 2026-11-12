"""Monitoreo › Cola de acciones: lo que espera lanzarse en AAP (pendientes, lanzándose y fallidos) con reintentar y cancelar,
y el estado de la cola en la ficha del ticket. Solo lee la base de FaceOps: nunca llama a AAP."""
from flask import flash, g, redirect, render_template, request, url_for

from . import cola, requiere_login
from .vistas import _escritura, bp


@bp.app_template_global("cola_ticket")
def cola_ticket(tid):
    try:
        return cola.info_ticket(tid)
    except Exception:  # noqa: BLE001
        return None


@bp.route("/cola")
@requiere_login
def cola_acciones():
    filas = cola.listar(g.cliente["id"])
    grupos = {"activos": [f for f in filas if f["estado"] in cola.ACTIVOS],
              "fallidos": [f for f in filas if f["estado"] == "fallido"],
              "recientes": [f for f in filas if f["estado"] in ("lanzado", "cancelado")][:30]}
    import json
    from .bd import ajuste
    aap = json.loads(ajuste(f"cola_aap_{g.cliente['id']}") or "{}")
    return render_template("cola.html", grupos=grupos, aap=aap, latido=cola.latido(),
                           escritura=g.perfil["modo"] == "escritura")


@bp.route("/cola/<int:cid>/<accion>", methods=["POST"])
@requiere_login
def cola_accion(cid, accion):
    _escritura()
    try:
        if accion == "reintentar":
            cola.reintentar(g.cliente["id"], cid, g.usuario["usuario"])
            flash("Vuelve a la cola: se lanza en cuanto AAP esté disponible y con capacidad", "ok")
        elif accion == "cancelar":
            cola.cancelar(g.cliente["id"], cid, g.usuario["usuario"])
            flash("Cancelado: no se lanza en AAP", "ok")
    except ValueError as x:
        flash(str(x), "error")
    return redirect(request.form.get("volver") or url_for("consola.cola_acciones"))
