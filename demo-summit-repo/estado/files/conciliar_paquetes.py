#!/usr/bin/python3
# Deja los paquetes del servidor iguales a una lista de Git (NEVRA exacto: nombre-versión-release.arquitectura).
# Uso: conciliar_paquetes.py <lista_deseada> <revisar|corregir> [solo_agregar]
#   revisar      → solo informa diferencias
#   corregir     → desinstala lo que sobra, instala lo que falta y deja cada versión igual a la lista
#   solo_agregar → no desinstala nada (propagar paquetes aprobados a otro servidor)
# Devuelve JSON: antes / acciones / no_disponibles / errores / despues.
import json, re, subprocess, sys

OMITIR = {"gpg-pubkey", "kernel", "kernel-core", "kernel-modules", "kernel-modules-core", "kernel-modules-extra",
          "kernel-devel", "kernel-headers"}  # llaves y kernels (varias versiones a la vez): no se concilian
RX = re.compile(r"^(?P<n>.+)-(?P<v>[^-]+)-(?P<r>[^-]+)\.(?P<a>[^.]+)$")


def leer(lineas):
    d = {}
    for l in lineas:
        l = l.strip()
        m = RX.match(l)
        if not m or l.startswith("#") or m["n"] in OMITIR:
            continue
        d[f'{m["n"]}.{m["a"]}'] = l
    return d


def actuales():
    out = subprocess.run(["rpm", "-qa", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n"],
                         capture_output=True, text=True).stdout.split()
    return leer(out)


def diferencias(deseado, actual, solo_agregar):
    sobran = [] if solo_agregar else sorted(actual[k] for k in actual if k not in deseado)
    faltan = sorted(deseado[k] for k in deseado if k not in actual)
    distintas = [] if solo_agregar else sorted(f"{actual[k]} → {deseado[k]}" for k in deseado if k in actual and actual[k] != deseado[k])
    return {"sobran": sobran, "faltan": faltan, "distintas": distintas}


def dnf(*args):
    r = subprocess.run(["dnf", "-y", "-q", *args], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def main():
    ruta, modo = sys.argv[1], sys.argv[2]
    solo_agregar = len(sys.argv) > 3 and sys.argv[3] == "solo_agregar"
    deseado = leer(open(ruta).read().splitlines())
    actual = actuales()
    antes = diferencias(deseado, actual, solo_agregar)
    res = {"antes": antes, "acciones": [], "no_disponibles": [], "errores": []}
    if modo == "corregir":
        if antes["sobran"]:
            nombres = [re.sub(r"-[^-]+-[^-]+\.([^.]+)$", r".\1", p) for p in antes["sobran"]]
            rc, txt = dnf("remove", "--noautoremove", *nombres)
            if rc == 0:
                res["acciones"] += [f"desinstalado {p}" for p in antes["sobran"]]
            else:
                res["errores"].append("no se pudo desinstalar: " + txt[-300:])
        objetivo = antes["faltan"] + [d.split(" → ")[1] for d in antes["distintas"]]
        for p in objetivo:
            rc, txt = dnf("install", "--allowerasing", p)
            if rc != 0 and ("already installed" in txt or "higher version" in txt or "ya está instalado" in txt):
                rc, txt = dnf("downgrade", p)
            if rc == 0:
                res["acciones"].append(f"instalado {p}")
            elif re.search(r"No match for argument|No package|No hay coincidencia|Unable to find a match", txt):
                res["no_disponibles"].append(p)
            else:
                res["errores"].append(f"{p}: {txt[-200:]}")
    res["despues"] = diferencias(deseado, actuales(), solo_agregar)
    res["cambiado"] = bool(res["acciones"])
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
