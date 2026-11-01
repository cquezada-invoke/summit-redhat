#!/usr/bin/python3
"""Credencial «FaceOps · Bóveda (escritura)» para «FaceOps · Vincular máquina a la bóveda» (idempotente; corre en la VM del stand).
Política faceops-vincular: solo crear/actualizar/leer faceops/data/maquinas/*. Token periódico (se renueva solo mientras se use).
Deja el token en un archivo 0600 que lee el paso siguiente (AAP) y luego se borra. Nunca imprime secretos."""
import json, os, subprocess, sys, tempfile, urllib.request

VAULT = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.122.210:8200"
SALIDA = sys.argv[2] if len(sys.argv) > 2 else "/tmp/bov-escritura.json"
IP = VAULT.split("//")[1].split(":")[0]
llave = subprocess.run(["sudo", "podman", "exec", "faceops", "cat", "/var/lib/faceops/llaves/id_demo"], capture_output=True, text=True, check=True).stdout
fd, kf = tempfile.mkstemp(); os.write(fd, llave.encode()); os.close(fd); os.chmod(kf, 0o600)
try:
    out = subprocess.run(["ssh", "-i", kf, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                          "-o", "LogLevel=ERROR", f"ansible@{IP}", "sudo cat /root/vault-init.json"], capture_output=True, text=True, check=True).stdout
finally:
    os.remove(kf)
ROOT = json.loads(out)["root_token"]


def v(m, p, d=None):
    req = urllib.request.Request(f"{VAULT}/v1/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                                 headers={"X-Vault-Token": ROOT, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        b = r.read()
        return json.loads(b) if b else {}


v("PUT", "sys/policies/acl/faceops-vincular", {"policy": 'path "faceops/data/maquinas/*" { capabilities = ["create", "update", "read"] }\n'})
print("   · política faceops-vincular (solo faceops/data/maquinas/*)")
tok = v("POST", "auth/token/create-orphan", {"policies": ["faceops-vincular"], "no_default_policy": True, "period": "768h",
                                             "display_name": "aap-vincular-maquinas", "meta": {"uso": "FaceOps · Vincular máquina a la bóveda"}})
fd = os.open(SALIDA, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, json.dumps({"addr": VAULT, "token": tok["auth"]["client_token"]}).encode()); os.close(fd)
print("   · token periódico creado (768 h renovable)")
