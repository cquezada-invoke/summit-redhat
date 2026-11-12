"""FaceOps · módulo Contenedores: clústeres OpenShift y Kubernetes. Vive en su propio repositorio (faceops-invoke/mod-contenedores); usa el núcleo (vistas, permisos, inventario)."""

from . import requiere_login
from .vistas import bp, inventario
from . import plataformas_contenedores  # noqa: F401,E402  («Crear plataforma de contenedores»: conectar existente o crear en GCP)


@bp.route("/contenedores", methods=["GET", "POST"])
@requiere_login
def contenedores():
    """Clústeres de contenedores (OpenShift, Kubernetes): su propia sección."""
    return inventario(seccion="contenedores")
