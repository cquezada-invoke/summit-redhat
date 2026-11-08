"""FaceOps · módulo Storage: equipos de almacenamiento. Vive en su propio repositorio (faceops-invoke/mod-storage); usa el núcleo (vistas, permisos, inventario)."""

from . import requiere_login
from .vistas import bp, inventario


@bp.route("/storage", methods=["GET", "POST"])
@requiere_login
def storage():
    """Equipos de almacenamiento: su propia sección, fuera del inventario de máquinas."""
    return inventario(seccion="storage")
