"""API para el agente de la Fábrica de código (corre en OpenShift): lee las políticas de desarrollo vigentes y pide a FaceOps ejecutar
el pipeline de Tekton sobre el repositorio y la rama que creó. FaceOps crea el ticket con su aprobación en AAP (nada se despliega sin
aprobar). Autenticación: cabecera X-Fabrica-Token = FACEOPS_FABRICA_TOKEN (sin token configurado, la API no existe)."""
import hmac
import os
import re

from flask import abort, g, jsonify, request

from .bd import auditar, q1
from .vistas import bp

PIPELINE = os.environ.get("FACEOPS_FABRICA_PIPELINE", "app-fabrica")
RE_REPO = r"http://[A-Za-z0-9.\-]+:3000/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+\.git"
RE_RAMA = r"feature/[a-z0-9][a-z0-9\-]{1,60}"


def _autorizado():
    tok = os.environ.get("FACEOPS_FABRICA_TOKEN", "")
    if not tok:
        abort(404)
    if not hmac.compare_digest(request.headers.get("X-Fabrica-Token", ""), tok):
        abort(401)
    cod = os.environ.get("FACEOPS_FABRICA_CLIENTE", "")
    cli = q1("SELECT * FROM clientes WHERE codigo=?", cod) if cod else q1("SELECT * FROM clientes WHERE codigo NOT LIKE 'demo-%' ORDER BY id LIMIT 1")
    usr = q1("SELECT * FROM usuarios WHERE usuario=? AND activo=1", os.environ.get("FACEOPS_FABRICA_USUARIO", "")) or \
        q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 ORDER BY id LIMIT 1")
    if not cli or not usr:
        abort(503)
    return cli, usr


@bp.route("/api/fabrica/politicas")
def api_fabrica_politicas():
    cli, _ = _autorizado()
    from .politicas_dev import contrato
    return jsonify(contrato(cli["id"]))


@bp.route("/api/fabrica/ejecutar", methods=["POST"])
def api_fabrica_ejecutar():
    """{repo, rama, nombre, solicitante, requerimiento} → ticket del pipeline con aprobación."""
    cli, usr = _autorizado()
    d = request.get_json(silent=True) or {}
    repo, rama = str(d.get("repo", "")), str(d.get("rama", ""))
    nombre = re.sub(r"[^a-z0-9-]", "", str(d.get("nombre", "")).lower())[:40]
    if not re.fullmatch(RE_REPO, repo) or not re.fullmatch(RE_RAMA, rama) or not nombre:
        return jsonify({"error": "repo (http://…:3000/org/repo.git), rama (feature/…) o nombre no válidos"}), 400
    from . import devsecops as dso
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cli["id"], dso.AUTOMATIZACION)
    if not a:
        return jsonify({"error": f"Falta «{dso.AUTOMATIZACION}» en el catálogo"}), 503
    from .operacion import solicitar
    g.usuario, g.cliente = usr, cli
    params = {"dp_accion": "ejecutar", "dp_pipeline": PIPELINE, "dp_url": dso.url_raw(PIPELINE),
              "dp_params": {"repo-url": repo, "revision": rama, "app": nombre, "ambiente": "desarrollo"}}
    motivo = f"Fábrica de código · {nombre} · {str(d.get('requerimiento', ''))[:300]} (pedido por {str(d.get('solicitante', 'agente'))[:60]})"
    try:
        t = solicitar(usr, cli, a, "*", params, motivo)
    except Exception as x:  # noqa: BLE001
        return jsonify({"error": str(x)[:300]}), 409
    auditar("fabrica-codigo", cli["id"], "fábrica de código · pipeline pedido", nombre, {"repo": repo, "rama": rama, "ticket": t["numero"]})
    return jsonify({"ticket": t["numero"], "id": t["id"], "estado": t.get("estado", "pendiente_aprobacion")})


@bp.route("/api/fabrica/ticket/<numero>")
def api_fabrica_ticket(numero):
    cli, _ = _autorizado()
    t = q1("SELECT numero, estado, resultado, creado, fin FROM tickets WHERE cliente_id=? AND numero=?", cli["id"], numero[:40])
    if not t:
        abort(404)
    return jsonify({"ticket": t["numero"], "estado": t["estado"], "resultado": (t["resultado"] or "")[:1500], "creado": t["creado"], "fin": t["fin"]})
