"""Paquetes del sistema de cada máquina (los lista AAP con «Sistema · Paquetes · inventario y respaldo») y qué está ya
respaldado en el registro de paquetes propio (Gitea · repositorio/rpm). FaceOps solo lee el registro (caché de 5 min)."""
import time

import requests

_cache = {}
REPO_LOCAL = ("@commandline", "commandline", "<unknown>", "")
# paquete → plataforma (skill) a la que pertenece; el resto es del sistema operativo
PAQ_PLAT = [("jboss", r"^eap[78]-"), ("postgres", r"^postgresql"), ("redis", r"^(redis|valkey)"), ("nginx", r"^nginx"),
            ("httpd", r"^(httpd|mod_)"), ("kafka", r"^(kafka|amq-streams)"), ("keycloak", r"^(rhbk|keycloak|rh-sso)"),
            ("activemq", r"^(amq-broker|activemq|artemis)"), ("389-directory-server", r"^(389-ds|cockpit-389|python3-lib389)"),
            ("red-hat-idm", r"^(ipa-|freeipa|bind|krb5-server|certmonger|pki-|python3-ipa|sssd-ipa|slapi-nis)"), ("vault", r"^vault"),
            ("tomcat", r"^(tomcat|jws)"), ("mysql", r"^(mysql|mariadb)"), ("podman", r"^(podman|containers-common|crun|conmon)")]


def sin_epoca(v):
    """Gitea guarda la época como prefijo («1-1.12.20-8.el9» o «1:…»): se compara sin ella."""
    import re
    v = v.split(":", 1)[-1]
    return re.sub(r"^\d+-(?=[^-]+-[^-]+$)", "", v)


def limpiar_cache():
    """Marca la caché como vencida: la próxima consulta la renueva en segundo plano sin hacer esperar a nadie."""
    if "rpm" in _cache:
        _cache["rpm"] = (0, _cache["rpm"][1])


def _leer_registro():
    out = set()
    try:
        import os
        from .readme import _gitea
        base, h = _gitea()
        tok = os.environ.get("FACEOPS_RESPALDO_TOKEN", "")   # el usuario de servicio del registro ve sus paquetes privados
        if tok:
            h = {"Authorization": f"token {tok}"}
        with requests.Session() as ses:   # una sola conexión para todas las páginas
            for page in range(1, 200):
                r = ses.get(f"{base}/api/v1/packages/repositorio", params={"type": "rpm", "limit": 50, "page": page}, headers=h, timeout=10)
                lote = r.json() if r.ok else []
                if not lote:
                    break
                out.update(f"{p['name']}|{sin_epoca(p['version'])}" for p in lote)
    except Exception:  # noqa: BLE001 - sin registro se muestra la lista igual
        return None
    return out


def _archivo():
    import os
    return os.path.join(os.path.dirname(os.environ.get("FACEOPS_BD", "/var/lib/faceops/faceops.db")), "registro-rpm.json")


def _guardar_disco(t, out):
    """La última lectura queda en disco: tras un reinicio o despliegue la primera ficha ya la tiene (no espera los ~25 s)."""
    import json
    import os
    try:
        tmp = _archivo() + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"t": t, "rpm": sorted(out)}, f)
        os.replace(tmp, _archivo())
    except (OSError, TypeError):
        pass


def _leer_disco():
    import json
    try:
        with open(_archivo()) as f:
            d = json.load(f)
        return float(d["t"]), set(d["rpm"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _refrescar():
    try:
        out = _leer_registro()
        if out is not None:
            t = time.time()
            _cache["rpm"] = (t, out)
            _guardar_disco(t, out)
        else:   # registro sin respuesta: no reintentar en cada ficha (cortacircuito de 60 s)
            _cache["caido"] = time.time()
    finally:
        _cache.pop("refrescando", None)


def precargar():
    """Lee el registro en segundo plano (al arrancar FaceOps y cada 5 min) para que ninguna ficha espere."""
    import threading
    if "refrescando" not in _cache and time.time() - _cache.get("caido", 0) >= 60:
        _cache["refrescando"] = True
        threading.Thread(target=_refrescar, daemon=True, name="registro-rpm").start()


def en_registro():
    """{'nombre|versión-release'} de los RPM del registro propio, o None si todavía no se sabe. Nunca espera: responde desde la
    memoria (o la copia en disco de la última lectura) y la renueva en segundo plano cada 5 min."""
    if "rpm" not in _cache:
        d = _leer_disco()
        if d:
            _cache["rpm"] = d
    if "rpm" in _cache:
        if time.time() - _cache["rpm"][0] >= 300:
            precargar()
        return _cache["rpm"][1]
    precargar()
    try:
        from .sin_espera import _marcar_pendiente
        _marcar_pendiente()
    except Exception:  # noqa: BLE001
        pass
    return None


def de_equipo(datos, skills=None):
    """{'fecha', 'ticket', 'lista': [{n, v, a, r, s, respaldo}], 'total', 'respaldados', 'fuentes': {repo: n}} o None."""
    p = (datos or {}).get("paquetes")
    if not p:
        return None
    import re
    from .identidad import NOMBRES_PLAT
    reg = en_registro()   # None: registro aún sin leer (la columna «respaldo» queda sin dato, no en «no»)
    lista, fuentes = [], {}
    sk = set(skills or [])
    reglas = [(k, re.compile(rx)) for k, rx in PAQ_PLAT if k in sk]
    for x in p.get("lista") or []:
        rpm = x.get("r") != "apt"
        resp = (f"{x['n']}|{x['v']}" in reg) if rpm and reg is not None else None
        plat = next((k for k, rx in reglas if rx.search(x["n"])), "")
        if not x.get("r") and re.search(r"\.el\d+", x.get("v") or ""):   # vino en la imagen base de RHEL sin repositorio registrado
            x = dict(x, r="Red Hat · imagen base", b=True)
        lista.append(dict(x, respaldo=resp, local=x.get("r") in REPO_LOCAL, plat=plat))
        fuentes[x.get("r") or "—"] = fuentes.get(x.get("r") or "—", 0) + 1
    lista.sort(key=lambda x: x["n"].lower())
    return {**{k: p.get(k) for k in ("fecha", "ticket", "gestor", "subidos", "errores", "pendientes")}, "lista": lista, "total": len(lista),
            "respaldados": sum(1 for x in lista if x["respaldo"]), "registro_pendiente": reg is None, "rpm": any(x["respaldo"] is not None for x in lista),
            "fuentes": dict(sorted(fuentes.items(), key=lambda kv: -kv[1])),
            "grupos": [("", "Sistema operativo", sum(1 for x in lista if not x["plat"]))] +
                      [(k, NOMBRES_PLAT.get(k) or k.capitalize(), n) for k, n in
                       sorted({k: sum(1 for x in lista if x["plat"] == k) for k in {x["plat"] for x in lista if x["plat"]}}.items())]}


def url_repo(n, v, a, r):
    """Enlace al paquete en su repositorio de origen (el del fabricante o el propio)."""
    from urllib.parse import quote
    r = (r or "").lower()
    if not r or r in REPO_LOCAL:
        return ""
    if r == "apt" or "ubuntu" in r or "jammy" in r or "noble" in r:
        return f"https://packages.ubuntu.com/search?keywords={quote(n)}"
    if "epel" in r:
        return f"https://packages.fedoraproject.org/pkgs/{quote(n)}/"
    if any(x in r for x in ("red hat", "rhel", "codeready", "ansible-automation", "satellite", "rhocp", "jb-eap", "jws", "rhbk", "amq", "rhui")):
        return f"https://access.redhat.com/downloads/content/{quote(n)}/{quote(sin_epoca(v))}/{quote(a or 'x86_64')}/fd431d51/package"
    if "faceops" in r or "registro" in r or "gitea" in r:
        return f"/git/repositorio/-/packages?type=rpm&q={quote(n)}"
    return f"https://pkgs.org/search/?q={quote(n)}"
