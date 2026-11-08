#!/usr/bin/env python3
"""Genera faceadmin/fabrica.json: FaceOps como ensamblado de módulos (faceops/datos/modulos.yml), cada uno con su repositorio,
sus piezas (código, vistas, playbooks), su actividad en git (commits, última actualización, autores) y su historial.
Lo corre version.sh antes de construir FaceAdmin (necesita el repositorio con su historia) y se versiona el resultado por si
la máquina no tiene PyYAML. Uso: python3 herramientas/faceadmin/fabrica.py"""
import fnmatch
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

try:
    import yaml
except ImportError:   # sin PyYAML se queda el fabrica.json versionado
    sys.exit(0)

R = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
F = os.path.join(R, "faceops")


def git(*a):
    try:
        return subprocess.run(["git", "-C", R, *a], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def piezas(m):
    """Archivos del monorepo que son de este módulo (pasarán a su repositorio)."""
    out = []
    for carpeta, pats, ext in (("app", m.get("app") or [], ""), ("app/templates", m.get("templates") or [], ""), ("aap", m.get("aap") or [], ".yml")):
        base = os.path.join(F, carpeta)
        try:
            nombres = sorted(os.listdir(base))
        except OSError:
            continue
        for p in pats:
            pat = p if p.endswith((".py", ".html", ".yml")) else p + ext
            out += [f"faceops/{carpeta}/{n}" for n in nombres if fnmatch.fnmatch(n, pat) and os.path.isfile(os.path.join(base, n))]
    return sorted(set(out))


def lineas(rutas):
    n = 0
    for r in rutas:
        try:
            with open(os.path.join(R, r), encoding="utf-8", errors="ignore") as f:
                n += sum(1 for _ in f)
        except OSError:
            pass
    return n


def actividad(rutas):
    if not rutas:
        return {"commits": 0, "ultimos": [], "autores": [], "mensual": {}}
    log = git("log", "--format=%H\x1f%cI\x1f%an\x1f%s", "--", *rutas)
    filas = [x.split("\x1f") for x in log.splitlines() if x.count("\x1f") == 3]
    mensual = {}
    for _, f, _, _ in filas:
        mensual[f[:7]] = mensual.get(f[:7], 0) + 1
    autores = sorted({a for _, _, a, _ in filas})
    return {"commits": len(filas), "ultimos": [{"sha": h[:10], "fecha": f[:16].replace("T", " "), "autor": a, "mensaje": s[:140]} for h, f, a, s in filas[:8]],
            "autores": autores, "mensual": dict(sorted(mensual.items())[-12:]), "primero": filas[-1][1][:10] if filas else ""}


def linea():
    import re
    m = re.search(r'^VERSION = "(\d+\.\d+)', open(os.path.join(F, "app", "__init__.py"), encoding="utf-8").read(), re.M)
    return m.group(1) if m else "1.0"


def main():
    d = yaml.safe_load(open(os.path.join(F, "datos", "modulos.yml"), encoding="utf-8"))
    mods = [dict(d["nucleo"], clave="nucleo", menu=["(base de todo)"])] + list(d.get("modulos") or [])
    out = []
    asignados = set()
    for m in mods:
        p = piezas(m)
        asignados |= set(p)
        out.append({"clave": m["clave"], "nombre": m["nombre"], "repo": m.get("repo", ""), "dueno": m.get("dueno", ""), "que": m.get("que", ""),
                    "menu": m.get("menu") or [], "piezas": {"codigo": [x for x in p if x.endswith(".py")], "vistas": [x for x in p if x.endswith(".html")],
                                                            "playbooks": [x for x in p if x.endswith(".yml")]},
                    "lineas": lineas(p), "actividad": actividad(p)})
        out[-1]["version"] = f"v1.{out[-1]['actividad']['commits']}"   # cada módulo versiona aparte: sus propios cambios
    todos = [f"faceops/{c}/{n}" for c, ext in (("app", ".py"), ("app/templates", ".html"), ("aap", ".yml"))
             for n in sorted(os.listdir(os.path.join(F, c))) if n.endswith(ext)]
    sueltas = [x for x in todos if x not in asignados]
    head = git("log", "-1", "--format=%H\x1f%cI\x1f%s").strip().split("\x1f")
    res = {"generado": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "monorepo": "github.com/cquezada-invoke/summit-redhat",
           "faceops": {"linea": linea(), "build": int(git("rev-list", "--count", "HEAD").strip() or 0), "commit": head[0][:10] if head[0] else "",
                       "fecha": head[1][:16].replace("T", " ") if len(head) > 1 else "", "mensaje": head[2][:140] if len(head) > 2 else "",
                       "actividad": actividad(["faceops"])},
           "modulos": out, "sin_modulo": {"n": len(sueltas), "lineas": lineas(sueltas), "ejemplos": sueltas[:40]}}
    json.dump(res, open(os.path.join(R, "faceadmin", "fabrica.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(len(out), "módulos ·", sum(len(m["piezas"]["codigo"]) + len(m["piezas"]["vistas"]) + len(m["piezas"]["playbooks"]) for m in out), "piezas ·",
          len(sueltas), "sin módulo")


if __name__ == "__main__":
    main()
