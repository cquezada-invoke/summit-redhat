#!/usr/bin/python3
# Lanza en AAP "Red · Solicitar acceso" con los valores por defecto (usuarios → srv-app tcp/8080) y muestra el resultado.
# Uso en la VM: python3 ~/summit-redhat/demo4-redes/herramientas/probar-caso1.py [solicitado_por]
import base64, glob, json, os, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
C, CTX = "https://localhost/api/controller/v2", ssl._create_unverified_context()
H = {"Authorization": "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode(), "Content-Type": "application/json"}
def api(m, p, d=None, crudo=False):
    try:
        r = urllib.request.urlopen(urllib.request.Request(f"{C}/{p}", method=m, headers=H,
                                   data=json.dumps(d).encode() if d is not None else None), context=CTX)
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR {m} {p}: {e.code} {e.read()[:300].decode(errors='replace')}")
    b = r.read()
    return b.decode() if crudo else json.loads(b)
jt = api("GET", "job_templates/?name=" + urllib.parse.quote("Red · Solicitar acceso"))["results"][0]["id"]
quien = sys.argv[1] if len(sys.argv) > 1 else "cquezada"
j = api("POST", f"job_templates/{jt}/launch/", {"extra_vars": {"acceso_nombre": "usuarios-a-srv-app", "acceso_origen": "10.40.40.0/24",
                                                    "acceso_destino": "10.10.10.10/32", "acceso_puerto": 8080,
                                                    "solicitado_por": quien}})["job"]
print(f"job {j} lanzado", flush=True)
while (st := api("GET", f"jobs/{j}/")["status"]) not in ("successful", "failed", "error", "canceled"):
    time.sleep(3)
salida = api("GET", f"jobs/{j}/stdout/?format=txt", crudo=True)
print("\n".join(l.strip() for l in salida.splitlines() if '"msg"' in l or "ok=" in l))
print(f"\njob {j}: {st}")
