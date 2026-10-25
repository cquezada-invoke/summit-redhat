#!/usr/bin/python3
# fgt-clave-api.py · Genera el token REST del usuario "aap" del FortiGate y lo guarda en Vault (secret/red/fortigate · api_token).
# El FortiGate pide la clave de admin de forma interactiva; se responde por una terminal virtual. No imprime secretos.
import json, os, pty, re, select, subprocess, sys, time, urllib.request

VAULT = "http://192.168.122.1:8202/v1/secret/data/red/fortigate"
TOK = json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))["root_token"]
req = lambda m, d=None: json.load(urllib.request.urlopen(urllib.request.Request(
    VAULT, method=m, data=json.dumps(d).encode() if d else None, headers={"X-Vault-Token": TOK})))
datos = req("GET")["data"]["data"]

pid, fd = pty.fork()
if pid == 0:
    os.execvp("ssh", ["ssh", "-q", "-tt", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                      "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=8",
                      "-i", os.path.expanduser("~/demo4/llave-lab"), "admin@172.20.20.12"])
buf = b""

def esperar(patron, seg=30):
    global buf
    fin = time.time() + seg
    while time.time() < fin:
        r, _, _ = select.select([fd], [], [], 1)
        if r:
            try:
                buf += os.read(fd, 4096)
            except OSError:
                break
            m = re.search(patron, buf)
            if m:
                return m
    return None

if not esperar(rb"# $|# \s*$", 30):
    sys.exit("No se obtuvo el prompt del FortiGate")
os.write(fd, b"execute api-user generate-key aap\n")
if esperar(rb"password:", 15):
    os.write(fd, datos["clave"].encode() + b"\n")
m = esperar(rb"New API key:\s*([A-Za-z0-9]+)", 20)
os.write(fd, b"exit\n")
if not m:
    sys.exit("No se pudo generar el token (¿licencia sin activar?)")
datos["api_token"] = m.group(1).decode()
req("POST", {"data": datos})
print("   token de API del FortiGate guardado en Vault")
