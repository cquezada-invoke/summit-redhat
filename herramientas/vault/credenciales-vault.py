#!/usr/bin/python3
"""Bóveda: guarda en la bóveda central (faceops/web/<máquina>/vault) el token de administración de cada servidor Vault del
laboratorio, incluido el propio Vault central, para que FaceOps abra su consola sin pedir credenciales.
Uso (en la VM del stand): tokens-vault.py <nombre>=<ip> [...]. Idempotente; nunca imprime secretos."""
import json, os, subprocess, sys, tempfile, urllib.request

VAULT = "http://192.168.122.210:8200"
llave = subprocess.run(["sudo", "podman", "exec", "faceops", "cat", "/var/lib/faceops/llaves/id_demo"], capture_output=True, text=True, check=True).stdout
fd, kf = tempfile.mkstemp(); os.write(fd, llave.encode()); os.close(fd); os.chmod(kf, 0o600)


def init_de(ip):
    return json.loads(subprocess.run(["ssh", "-i", kf, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                                      "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=8", f"ansible@{ip}", "sudo cat /root/vault-init.json"],
                                     capture_output=True, text=True, check=True).stdout)


try:
    ROOT = init_de("192.168.122.210")["root_token"]
    for par in sys.argv[1:]:
        nombre, ip = par.split("=")
        try:
            tok = ROOT if ip == "192.168.122.210" else init_de(ip)["root_token"]
        except (subprocess.CalledProcessError, KeyError, ValueError) as x:
            print(f"   · {nombre}: sin acceso a su vault-init.json ({type(x).__name__})"); continue
        req = urllib.request.Request(f"{VAULT}/v1/faceops/data/web/{nombre}/vault", method="POST", headers={"X-Vault-Token": ROOT, "Content-Type": "application/json"},
                                     data=json.dumps({"data": {"sitio": "vault", "usuario": "root", "clave": tok, "puerto": 8200, "ruta": "/ui/"}}).encode())
        urllib.request.urlopen(req, timeout=15).read()
        print(f"   · {nombre}: token de administración guardado en web/{nombre}/vault")
finally:
    os.remove(kf)
