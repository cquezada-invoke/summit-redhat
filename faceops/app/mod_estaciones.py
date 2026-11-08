"""FaceOps · módulo Estaciones de trabajo: escritorios y acceso remoto. Vive en su propio repositorio (faceops-invoke/mod-estaciones); usa el núcleo (vistas, permisos, inventario)."""

from . import requiere_login
from .vistas import bp, inventario


@bp.route("/estaciones", methods=["GET", "POST"])
@requiere_login
def estaciones():
    """Estaciones de trabajo: su propia sección."""
    return inventario(seccion="estaciones")
