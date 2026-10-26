#!/usr/bin/python3
# Demo 5 · Windows: AAP administra nodo-windows (WinRM) y remedia IIS caído. Idempotente.
# 1) Vault: lectura de secret/windows para AAP.  2) Credencial de máquina desde Vault.
# 3) Inventario "Servidores Windows" (grupo windows, host nodo-windows).  4) Plantillas:
#    "Windows · Preparar IIS", "Remediación · IIS en Windows" (la lanza EDA por la alerta SitioWindowsCaido)
#    y "Windows · Provocar caída de IIS (demo)".  5) Gitea/EDA: reutiliza configurar_monitoreo.py.
import base64, glob, json, os, re, ssl, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context(); BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
VT = json.load(open(os.path.expanduser("~/demo-drift/vault-init.json")))["root_token"]
paso = lambda t: print(f"   · {t}", flush=True)


def http(url, m, d=None, cab=None, tolerar=False):
    r = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers=dict({"Content-Type": "application/json"}, **(cab or {})))
    try:
        with urllib.request.urlopen(r, context=CTX, timeout=60) as x:
            b = x.read(); return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {url} -> {e.code}: {e.read()[:400].decode(errors='replace')}")


aap = lambda m, p, d=None, t=False: http(f"https://localhost/api/controller/v2/{p}", m, d, {"Authorization": BASIC}, t)
vault = lambda m, p, d=None: http(f"https://127.0.0.1:8200/v1/{p}", m, d, {"X-Vault-Token": VT})


def uno(rec, **q):
    r = aap("GET", f"{rec}/?" + urllib.parse.urlencode(q))["results"]
    return r[0] if r else None


def asegurar(rec, nombre, datos):
    o = uno(rec, name=nombre)
    return aap("PATCH", f"{rec}/{o['id']}/", datos) if o else aap("POST", f"{rec}/", dict(datos, name=nombre))


# 1. Vault
pol = vault("GET", "sys/policies/acl/aap-lectura")["data"]["policy"]
if "secret/data/windows" not in pol:
    vault("PUT", "sys/policies/acl/aap-lectura", {"policy": pol.rstrip() + '\npath "secret/data/windows" { capabilities = ["read"] }\n'})
paso("Vault: AAP puede leer secret/windows")

# 2. Credencial de máquina (usuario y clave desde Vault)
org = uno("organizations", name="Operación")["id"]
maquina = uno("credential_types", name="Machine")["id"]
cred = asegurar("credentials", "Windows · ansible (WinRM)", {
    "organization": org, "credential_type": maquina, "inputs": {},
    "description": "Cuenta de servicio ansible de nodo-windows, desde Vault (secret/windows)"})["id"]
vcred = uno("credentials", name="HashiCorp Vault · secretos")["id"]
for s in aap("GET", f"credentials/{cred}/input_sources/")["results"]:
    aap("DELETE", f"credential_input_sources/{s['id']}/")
for campo, clave in (("username", "usuario"), ("password", "clave")):
    aap("POST", "credential_input_sources/", {"target_credential": cred, "input_field_name": campo, "source_credential": vcred,
                                              "metadata": {"secret_backend": "secret", "secret_path": "windows", "secret_key": clave}})
paso("AAP: credencial 'Windows · ansible (WinRM)' enlazada a Vault")

# 3. Inventario
inv = asegurar("inventories", "Servidores Windows", {"organization": org, "description": "Servidores Windows del laboratorio (WinRM)"})["id"]
g = uno("groups", name="windows", inventory=inv) or aap("POST", f"inventories/{inv}/groups/", {"name": "windows"})
hv = ("ansible_host: 192.168.122.17\nansible_connection: winrm\nansible_port: 5985\nansible_winrm_transport: ntlm\n"
      "ansible_winrm_scheme: http\nansible_winrm_server_cert_validation: ignore\ndescripcion: Windows Server 2022 (evaluación) con IIS")
h = uno("hosts", name="nodo-windows", inventory=inv)
hid = aap("PATCH", f"hosts/{h['id']}/", {"variables": hv})["id"] if h else aap("POST", f"inventories/{inv}/hosts/", {"name": "nodo-windows", "variables": hv})["id"]
aap("POST", f"groups/{g['id']}/hosts/", {"id": hid}, True)
paso("AAP: inventario 'Servidores Windows' con nodo-windows (WinRM 5985, NTLM)")

# 4. Plantillas (Gitea y proyecto los sincroniza configurar_monitoreo.py)
subprocess.run([sys.executable, os.path.join(os.path.dirname(AQUI), "monitoreo", "configurar_monitoreo.py")], check=True)
proy = uno("projects", name="Operación · remediación")["id"]
sn = uno("credentials", name="ServiceNow · ansible.automatizacion")["id"]
nt = uno("notification_templates", name="Correo · aprobaciones de red")
for nombre, pb, desc, correo in (
        ("Windows · Preparar IIS", "playbooks/preparar-windows.yml", "Instala IIS y la página de la app en nodo-windows (también reinicia el caso)", False),
        ("Remediación · IIS en Windows", "playbooks/reiniciar-iis.yml", "Monitoreo: el sitio de nodo-windows no responde → levanta IIS y verifica", True),
        ("Windows · Provocar caída de IIS (demo)", "playbooks/provocar-iis.yml", "Solo para la demo: detiene IIS en nodo-windows", False)):
    jt = asegurar("job_templates", nombre, {"organization": org, "project": proy, "inventory": inv, "playbook": pb,
                                            "description": desc, "ask_variables_on_launch": True})["id"]
    ya = {c["id"] for c in aap("GET", f"job_templates/{jt}/credentials/")["results"]}
    for c in (cred, sn):
        if c not in ya:
            aap("POST", f"job_templates/{jt}/credentials/", {"id": c})
    if correo and nt:
        for ev in ("success", "error"):
            aap("POST", f"job_templates/{jt}/notification_templates_{ev}/", {"id": nt["id"]}, True)
    paso(f"AAP: plantilla '{nombre}' (id {jt})")
print("   LISTO")
