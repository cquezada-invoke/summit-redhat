"""FaceOps · módulo Nubes: máquinas en GCP, AWS, Azure y OCI. Vive en su propio repositorio (faceops-invoke/mod-nubes); usa el núcleo (vistas, permisos, inventario)."""

from . import requiere_login
from .vistas import bp, inventario


@bp.route("/nubes", methods=["GET", "POST"])
@requiere_login
def nubes():
    return inventario(seccion="nubes")
