#!/usr/bin/env python3
"""Sube la instantánea del inventario de una máquina a su repositorio propio (Gitea · <dueño>/maquina-<nombre>, rama master).

Uso: inventario-a-git.py <instantánea .tgz|.zip> <nombre> <motivo>   (entorno: G = URL de Gitea, T = token)
Solo hace commit si algo cambió; los archivos que cambiaron van en un solo commit (o en partes si son muchos MB).
Imprime CLAVE=valor: REPO, RAMA, COMMIT (si hubo), CAMBIOS, ARCHIVOS, URL.
"""
import base64
import datetime
import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile

G, T = os.environ["G"].rstrip("/"), os.environ["T"]
RAMA = "master"
LOTE = 15 * 1024 * 1024   # base64 por pedido


def api(metodo, ruta, datos=None, ok404=False):
    req = urllib.request.Request(G + "/api/v1" + ruta, method=metodo, data=json.dumps(datos).encode() if datos is not None else None,
                                 headers={"Authorization": f"token {T}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if ok404 and e.code == 404:
            return None
        raise SystemExit(f"ERROR=Gitea {metodo} {ruta}: {e.code} {e.read()[:300]!r}")


def leer(ruta):
    if not os.path.exists(ruta) and os.path.exists(ruta + ".b64"):
        with open(ruta + ".b64", "rb") as f:
            datos = base64.b64decode(b"".join(f.read().split()))
        os.remove(ruta + ".b64")
    else:
        with open(ruta, "rb") as f:
            datos = f.read()
    out = {}
    if ruta.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            for n in z.namelist():
                if not n.endswith("/"):
                    out[n.replace("\\", "/")] = z.read(n)
    else:
        with tarfile.open(fileobj=io.BytesIO(datos), mode="r:gz") as t:
            for m in t.getmembers():
                if m.isfile():
                    n = m.name.lstrip("./")
                    out[n if n.startswith(("meta/", "secretos/")) else "archivos/" + n] = t.extractfile(m).read()
    return out


def a_boveda(nombre, secretos):
    """Secretos de la máquina (llaves, keystores, shadow) → bóveda faceops/data/maquinas/<nombre>/archivos, nunca a Git.
    Solo escribe si cambió algo (no crea versiones nuevas cada día)."""
    addr, tok = os.environ.get("FACEOPS_VAULT_ADDR", "").rstrip("/"), os.environ.get("FACEOPS_VAULT_TOKEN", "")
    if not addr or not tok:
        return "sin_credencial"
    url = f"{addr}/v1/faceops/data/maquinas/{nombre}/archivos"
    datos = {"/" + k: base64.b64encode(v).decode() for k, v in secretos.items()}
    h = {"X-Vault-Token": tok, "Content-Type": "application/json"}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r:
            if (json.load(r).get("data") or {}).get("data") == datos:
                return "igual"
    except urllib.error.HTTPError as e:
        if e.code not in (404,):
            return f"error_{e.code}"
    except OSError:
        return "sin_conexion"
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="POST", headers=h, data=json.dumps({"data": datos}).encode()), timeout=20)
        return "guardado"
    except urllib.error.HTTPError as e:
        return f"error_{e.code}"
    except OSError:
        return "sin_conexion"


def blob_sha(b):
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def main():
    ruta, nombre, motivo = sys.argv[1], sys.argv[2], sys.argv[3]
    local = leer(ruta)
    try:
        os.remove(ruta)
    except OSError:
        pass
    secretos = {k[len("secretos/"):]: local.pop(k) for k in [k for k in local if k.startswith("secretos/")]}
    print(f"SECRETOS={len(secretos)}")
    if secretos:
        print(f"SECRETOS_BOVEDA={a_boveda(nombre, secretos)}")
    dueno = os.environ.get("DUENO", "repositorio")   # misma organización que el registro y el respaldo continuo
    repo = f"maquina-{nombre}"
    info = api("GET", f"/repos/{dueno}/{repo}", ok404=True)
    if info is None:
        datos_ = {"name": repo, "private": True, "auto_init": True, "default_branch": RAMA,
                  "description": f"Máquina {nombre}: instantánea diaria del inventario (FaceOps). Rama master = estado vigente."}
        info = api("POST", f"/orgs/{dueno}/repos", datos_, ok404=True) or api("POST", "/user/repos", datos_)
        dueno = (info.get("owner") or {}).get("login") or dueno
    remoto, pag = {}, 1
    while True:
        tr = api("GET", f"/repos/{dueno}/{repo}/git/trees/{RAMA}?recursive=true&per_page=10000&page={pag}", ok404=True) or {}
        for x in tr.get("tree") or []:
            if x.get("type") == "blob":
                remoto[x["path"]] = x["sha"]
        if not tr.get("truncated") or not tr.get("tree"):
            break
        pag += 1
    if "README.md" not in remoto or not any(k.startswith("archivos/") for k in remoto):
        local["README.md"] = (f"# Máquina {nombre}\n\nInstantánea que FaceOps guarda en cada inventario (una vez al día y a pedido), "
                              "sin agente, por AAP.\n\n- `archivos/` configuración del sistema y de cada producto encontrado\n"
                              "- `meta/` paquetes exactos, servicios, SELinux, usuarios, permisos, red, discos y lo omitido por tamaño\n\n"
                              "Cada commit es un estado de la máquina: lo usan los puntos de restauración y «Recuperar servidor».\n").encode()
    pq_antes = set()
    if "meta/paquetes.txt" in remoto and "meta/paquetes.txt" in local:
        r = urllib.request.Request(f"{G}/api/v1/repos/{dueno}/{repo}/raw/meta/paquetes.txt?ref={RAMA}", headers={"Authorization": f"token {T}"})
        with urllib.request.urlopen(r, timeout=60) as f:
            pq_antes = set(f.read().decode(errors="replace").splitlines())
    ops = []
    for p, b in sorted(local.items()):
        s = blob_sha(b)
        if p not in remoto:
            ops.append({"operation": "create", "path": p, "content": base64.b64encode(b).decode()})
        elif remoto[p] != s:
            ops.append({"operation": "update", "path": p, "content": base64.b64encode(b).decode(), "sha": remoto[p]})
    for p, s in sorted(remoto.items()):
        if p not in local and (p.startswith("archivos/") or p.startswith("meta/")):
            ops.append({"operation": "delete", "path": p, "sha": s})
    print(f"REPO={dueno}/{repo}")
    print(f"RAMA={RAMA}")
    print(f"ARCHIVOS={sum(1 for k in local if k.startswith('archivos/'))}")
    print(f"URL={info.get('html_url', '')}")
    # solo cambian metadatos volátiles (permisos de archivos que no cambiaron, etc.) → igual se guarda: es el estado
    if not ops:
        print("CAMBIOS=0")
        return
    hoy = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    marca = {"create": "+", "update": "M", "delete": "-"}
    det = [f"· {marca[o['operation']]} {o['path']}" for o in ops if not o["path"].startswith("meta/")]
    pq_ahora = set(local.get("meta/paquetes.txt", b"").decode(errors="replace").splitlines())
    pq = ([f"· + {x.split('|')[-1]}" for x in sorted(pq_ahora - pq_antes)] + [f"· - {x.split('|')[-1]}" for x in sorted(pq_antes - pq_ahora)]) if pq_antes else []
    cuerpo = ""
    if det:
        cuerpo += "\nArchivos:\n" + "\n".join(det[:80]) + (f"\n· … y {len(det) - 80} más" if len(det) > 80 else "")
    if pq:
        cuerpo += "\nPaquetes:\n" + "\n".join(pq[:80]) + (f"\n· … y {len(pq) - 80} más" if len(pq) > 80 else "")
    titulo = f"{motivo} · {hoy} · {len(det)} archivo{'s' if len(det) != 1 else ''}" + (f", {len(pq)} paquete{'s' if len(pq) != 1 else ''}" if pq else "")
    lotes, act, tam = [], [], 0
    for o in ops:
        n = len(o.get("content", ""))
        if act and tam + n > LOTE:
            lotes.append(act)
            act, tam = [], 0
        act.append(o)
        tam += n
    lotes.append(act)
    sha = ""
    for i, lt in enumerate(lotes, 1):
        msg = titulo + (f" (parte {i} de {len(lotes)})" if len(lotes) > 1 else "") + ("\n" + cuerpo if i == len(lotes) and cuerpo else "")
        r = api("POST", f"/repos/{dueno}/{repo}/contents", {"branch": RAMA, "message": msg, "files": lt,
                                                             "author": {"name": "FaceOps inventario", "email": "inventario@faceops.invoke.cl"}})
        sha = ((r or {}).get("commit") or {}).get("sha", sha)
    print(f"CAMBIOS={len(ops)}")
    print(f"COMMIT={sha}")


if __name__ == "__main__":
    main()
