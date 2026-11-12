"""Configuración de un equipo de red, en el nodo de ejecución (la usan red-respaldo.yml y red-drift.yml).
  normalizar: stdin {plataforma, texto} → {plataforma, config, sha256, lineas, secretos}. Los secretos (claves, PSK,
              comunidades SNMP, llaves) quedan como «<oculto:huella>»: un cambio de clave se detecta sin guardarla.
  comparar:   stdin {actual, base} → {cambios, agregadas, quitadas, total_agregadas, total_quitadas}."""
import difflib
import hashlib
import json
import re
import sys

SECRETO = re.compile(r"(?i)\b(encrypted-password|plaintext-password|password|passwd|secret|pre-shared-secret|pre-shared-key|"
                     r"psk|key-string|community|auth-key|authentication-key|private-key|stats auth|userlist-password)\b"
                     r"(\s+|\s*=\s*)('[^']*'|\"[^\"]*\"|\S+)")
VARIABLE = re.compile(r"^(Building configuration|Current configuration|! Last configuration change|! NVRAM config)", re.I)


def _oculta(m):
    h = hashlib.sha256(m.group(3).encode()).hexdigest()[:10]
    return f"{m.group(1)}{m.group(2)}<oculto:{h}>"


def normalizar(d):
    n = 0
    out = []
    for ln in (d.get("texto") or "").replace("\r", "").split("\n"):
        ln = ln.rstrip()
        if not ln.strip() or VARIABLE.match(ln.strip()):
            continue
        ln2, k = SECRETO.subn(_oculta, ln)
        n += k
        out.append(ln2)
    cfg = "\n".join(out) + ("\n" if out else "")
    return {"plataforma": d.get("plataforma") or "", "config": cfg, "sha256": hashlib.sha256(cfg.encode()).hexdigest(),
            "lineas": len(out), "secretos": n}


def comparar(d):
    a = [x for x in (d.get("actual") or "").split("\n") if x.strip()]
    b = [x for x in (d.get("base") or "").split("\n") if x.strip()]
    agr, qui = [], []
    for ln in difflib.unified_diff(b, a, lineterm="", n=0):
        if ln.startswith(("+++", "---", "@@")):
            continue
        (agr if ln.startswith("+") else qui).append(ln[1:])
    return {"cambios": bool(agr or qui), "agregadas": agr[:40], "quitadas": qui[:40],
            "total_agregadas": len(agr), "total_quitadas": len(qui)}


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "normalizar"
    print(json.dumps((comparar if modo == "comparar" else normalizar)(json.load(sys.stdin)), ensure_ascii=False))
