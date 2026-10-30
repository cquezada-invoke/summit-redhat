"""Estructura como código: cada equipo debe tener su configuración en Git (lo más posible, por capas). FaceOps busca el
último commit de cada capa (datos/codigo.yml) en Gitea y lo guarda en el equipo; un equipo sin ninguna capa en Git es un
hallazgo."""
import fnmatch
import json
import os
from datetime import datetime, timezone

import requests
import yaml

from .bd import ej, j, q

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "codigo.yml")


def conf():
    try:
        return yaml.safe_load(open(RUTA, encoding="utf-8")) or {}
    except OSError:
        return {}


def _aplica(si, e):
    from .inventario import skills_de, sistema_eq
    for k, vals in (si or {}).items():
        if k == "nombre" and not any(fnmatch.fnmatch(e["nombre"], v) for v in vals):
            return False
        if k == "skill" and not set(vals) & set(skills_de(e)):
            return False
        if k == "categoria" and (e.get("categoria") or "servidor") not in vals:
            return False
        if k == "sistema" and sistema_eq(e) not in vals:
            return False
    return True


def _ultimo_commit(base, repo, ruta, rama, cache):
    k = (repo, ruta, rama)
    if k not in cache:
        try:
            params = {"path": ruta, "limit": 1, **({"sha": rama} if rama else {})}
            tok = os.environ.get("FACEOPS_RESPALDO_TOKEN" if repo.startswith("repositorio/") else "FACEOPS_GITEA_TOKEN")  # repositorios privados
            r = requests.get(f"{base}/api/v1/repos/{repo}/commits", params=params, timeout=8,
                             headers={"Authorization": f"token {tok}"} if tok else {})
            d = r.json() if r.ok else []
            c = d[0] if isinstance(d, list) and d else None
            cache[k] = {"sha": c["sha"], "fecha": c["commit"]["author"]["date"], "autor": c["commit"]["author"]["name"],
                        "mensaje": c["commit"]["message"].strip().splitlines()[0][:120], "url": c["html_url"],
                        "ver": c["html_url"].split("/commit/")[0] + f"/src/commit/{c['sha']}/{ruta}"} if c else None
        except (requests.RequestException, ValueError, KeyError):
            cache[k] = "error"
    return cache[k]


def refrescar(cliente_id=None):
    """Último commit de cada capa de cada equipo → equipos.codigo (lista). Si Gitea no responde, no toca lo guardado."""
    c = conf()
    base, capas = c.get("gitea", "").rstrip("/"), c.get("capas", [])
    if not base:
        return 0
    filtro, args = ("AND cliente_id=?", [cliente_id]) if cliente_id else ("", [])
    cache, n = {}, 0
    for e in q(f"SELECT * FROM equipos WHERE estado!='retirado' {filtro}", *args):
        out, error = [], False
        for cp in capas:
            if not _aplica(cp.get("si"), e):
                continue
            ruta = cp["ruta"].format(nombre=e["nombre"], ambiente=e.get("ambiente") or "produccion")
            rama = cp["rama"].format(nombre=e["nombre"]) if cp.get("rama") else None
            r = _ultimo_commit(base, cp["repo"], ruta, rama, cache)
            if r == "error":
                error = True
            elif r:
                out.append(dict(r, capa=cp["capa"], repo=cp["repo"], ruta=ruta))
        if not error:
            ej("UPDATE equipos SET codigo=?, codigo_visto=? WHERE id=?", json.dumps(out, ensure_ascii=False),
               datetime.now(timezone.utc).isoformat(timespec="seconds"), e["id"])
            n += 1
    return n


def de(e):
    return j(e.get("codigo"), [])
