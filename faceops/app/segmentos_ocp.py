"""Segmentos de negocio desplegados en OpenShift: lo que dejó «OpenShift · Desplegar segmento» (namespace, rutas,
repositorio GitOps, pipelines). FaceOps lo lee del resultado del trabajo en AAP; el estado vivo de los pods lo trae
«OpenShift · Recolectar clúster»."""
import json

from .bd import ahora, ej, j, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS segmentos_ocp (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, segmento TEXT NOT NULL, ambiente TEXT NOT NULL, version INTEGER,
  namespace TEXT, rutas TEXT DEFAULT '{}', gitops TEXT DEFAULT '', commit_git TEXT DEFAULT '', componentes TEXT DEFAULT '[]',
  ticket TEXT DEFAULT '', actualizado TEXT, UNIQUE(cliente_id, segmento, ambiente));
"""


def iniciar():
    from .bd import con
    con().executescript(ESQUEMA)


def registrar(aap, cliente, t):
    jobs = [t["aap_job_id"]] if t["aap_job_tipo"] == "job" else \
        [n["id"] for n in aap.nodos_trabajo_workflow(t["aap_job_id"]) if n.get("type") == "job" and n.get("id")]
    d = {}
    for jid in jobs:
        d.update((aap.get(f"jobs/{jid}/").get("artifacts") or {}).get("faceops_ocp_segmento") or {})
    if not d.get("namespace"):
        return "No se recibieron datos del despliegue"
    ej("INSERT INTO segmentos_ocp(cliente_id, segmento, ambiente, version, namespace, rutas, gitops, commit_git, componentes, ticket, actualizado) "
       "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cliente_id, segmento, ambiente) DO UPDATE SET version=excluded.version, "
       "namespace=excluded.namespace, rutas=excluded.rutas, gitops=excluded.gitops, commit_git=excluded.commit_git, "
       "componentes=excluded.componentes, ticket=excluded.ticket, actualizado=excluded.actualizado",
       cliente["id"], d["segmento"], d["ambiente"], int(d.get("version") or 1), d["namespace"], json.dumps(d.get("rutas") or {}),
       d.get("gitops", ""), d.get("commit", ""), json.dumps(d.get("componentes") or []), t["numero"], ahora())
    rutas = " · ".join(f"{k}: {v}" for k, v in (d.get("rutas") or {}).items())
    return f"OpenShift: namespace {d['namespace']} · GitOps {d.get('gitops', '')}" + (f" · {rutas}" if rutas else "")


def de_segmento(cliente_id, segmento):
    out = q("SELECT * FROM segmentos_ocp WHERE cliente_id=? AND segmento=? ORDER BY ambiente", cliente_id, segmento)
    for x in out:
        x["rutas_d"], x["componentes_l"] = j(x["rutas"], {}), j(x["componentes"], [])
    return out
