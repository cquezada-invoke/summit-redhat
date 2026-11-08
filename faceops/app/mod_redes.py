"""FaceOps · módulo Redes: equipos de red (routers, switches, firewalls, balanceadores). Vive en su propio repositorio (faceops-invoke/mod-redes); usa el núcleo (vistas, permisos, inventario)."""

from . import requiere_login
from .vistas import bp, inventario


@bp.route("/redes", methods=["GET", "POST"])
@requiere_login
def redes():
    """Equipos de red (routers, switches, firewalls, balanceadores): su propia sección, fuera del inventario de máquinas."""
    return inventario(seccion="redes")
