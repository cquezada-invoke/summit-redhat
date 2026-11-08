"""FaceOps · módulo Contenedores: clústeres OpenShift y Kubernetes. Vive en su propio repositorio (faceops-invoke/mod-contenedores); usa el núcleo (vistas, permisos, inventario)."""

from . import requiere_login
from .vistas import bp, inventario


@bp.route("/contenedores", methods=["GET", "POST"])
@requiere_login
def contenedores():
    """Clústeres de contenedores (OpenShift, Kubernetes): su propia sección."""
    return inventario(seccion="contenedores")
