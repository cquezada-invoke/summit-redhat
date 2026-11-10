"""FaceOps · módulo Redes: equipos de red (routers, switches, firewalls, balanceadores). Vive en su propio repositorio (faceops-invoke/mod-redes); usa el núcleo (vistas, permisos, inventario)."""

from . import requiere_login
from .vistas import bp, inventario


@bp.route("/redes", methods=["GET", "POST"])
@requiere_login
def redes():
    """Equipos de red (routers, switches, firewalls, balanceadores): su propia sección, fuera del inventario de máquinas."""
    return inventario(seccion="redes")


def _topologia(completo):
    """Diagrama de red del cliente (app/topologia_red.py) con los equipos que ve el usuario. Nunca rompe la página."""
    from flask import current_app, g
    from .topologia_red import diagrama, es_red
    from .vistas import equipos_visibles
    try:
        eqs = equipos_visibles()
        return diagrama(g.cliente, eqs, [e for e in eqs if es_red(e)], completo=completo)
    except Exception:  # noqa: BLE001
        current_app.logger.exception("topología de red")
        return None


@bp.route("/redes/arquitectura")
@requiere_login
def redes_arquitectura():
    """Arquitectura de red: todos los equipos de red y todas las máquinas, con la dependencia real de cada una."""
    from flask import render_template
    return render_template("redes_arquitectura.html", topo=_topologia(True))
