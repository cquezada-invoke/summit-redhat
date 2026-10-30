#!/usr/bin/python3
"""CA interna ↔ AAP: identidad de agente propia para FaceOps y su credencial cifrada en AAP.
  · usuario «faceops-renovador» en la CA (grupo Certificate Manager Agents) con su certificado de cliente
    (la llave se genera aquí y va directo a AAP; no queda en disco ni se imprime)
  · tipo de credencial y credencial «FaceOps · CA interna» (URL, CA, certificado y llave del agente como archivos)
  · la credencial queda asociada a las plantillas «Certificados · …»
Idempotente (si el agente ya tiene certificado y la credencial existe, no emite otro salvo --rotar). Corre en la VM del stand."""
import base64, glob, json, os, re, ssl, subprocess, sys, tempfile, urllib.parse, urllib.request

P = os.path.expanduser("~/pki")
CLAVES = dict(l.strip().split("=", 1) for l in open(f"{P}/claves") if "=" in l)
CA_URL_LOCAL, CA_URL_AAP = "https://localhost:18443", "https://192.168.122.1:18443"
USUARIO = "faceops-renovador"
paso = lambda t: print(f"   · {t}", flush=True)


def pki(*a, ok=False):
    r = subprocess.run(["pki", "-d", f"{P}/nssdb", "-c", CLAVES["nss"], "-U", CA_URL_LOCAL, "-n", "caadmin",
                        "--ignore-cert-status", "BAD_CERT_DOMAIN", "--ignore-banner", *a], capture_output=True, text=True)
    if r.returncode and not ok:
        sys.exit(f"pki {a[0]}: {r.stderr.strip()[:300]}")
    return r


inv = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
AUT = "Basic " + base64.b64encode(("admin:" + re.search(r"^gateway_admin_password=(.*)$", open(inv).read(), re.M).group(1).strip()).encode()).decode()
CTX = ssl._create_unverified_context()


def aap(m, p, d=None):
    q = urllib.request.Request(f"https://127.0.0.1/api/controller/v2/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Authorization": AUT, "Content-Type": "application/json"})
    with urllib.request.urlopen(q, timeout=30, context=CTX) as r:
        b = r.read()
        return json.loads(b) if b else {}


def uno(rec, **f):
    r = aap("GET", f"{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


# 1. Usuario agente en la CA
if pki("ca-user-show", USUARIO, ok=True).returncode:
    pki("ca-user-add", USUARIO, "--fullName", "FaceOps · renovación de certificados (AAP)", "--type", "agentType")
    paso(f"Usuario «{USUARIO}» creado en la CA")
if USUARIO not in pki("ca-group-member-find", "Certificate Manager Agents").stdout:
    pki("ca-group-member-add", "Certificate Manager Agents", USUARIO)
paso(f"«{USUARIO}» es agente de la CA (Certificate Manager Agents)")

ct = uno("credential_types", name="FaceOps · CA interna") or aap("POST", "credential_types/", {
    "name": "FaceOps · CA interna", "kind": "cloud",
    "description": "Agente de la CA interna (Red Hat Certificate System): pide y aprueba certificados de servicios",
    "inputs": {"fields": [{"id": "url", "label": "URL de la CA"}, {"id": "ca", "label": "Certificado de la CA (PEM)", "multiline": True},
                          {"id": "cert", "label": "Certificado del agente (PEM)", "multiline": True},
                          {"id": "llave", "label": "Llave del agente (PEM)", "multiline": True, "secret": True}],
               "required": ["url", "ca", "cert", "llave"]},
    "injectors": {"file": {"template.ca": "{{ ca }}", "template.cert": "{{ cert }}", "template.llave": "{{ llave }}"},
                  "extra_vars": {"faceops_ca_url": "{{ url }}", "faceops_ca_ca": "{{ tower.filename.ca }}",
                                 "faceops_ca_cert": "{{ tower.filename.cert }}", "faceops_ca_llave": "{{ tower.filename.llave }}"}}})
org = uno("organizations", name="FaceOps")
cr = uno("credentials", name="FaceOps · CA interna")

# 2. Certificado de cliente del agente (solo si no hay credencial o se pide rotar)
if not cr or "--rotar" in sys.argv:
    with tempfile.TemporaryDirectory() as t:
        subprocess.run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{t}/a.key", "-out", f"{t}/a.csr",
                        "-subj", f"/UID={USUARIO}/CN=FaceOps renovador/O=INVOKE"], check=True, capture_output=True)
        pki("ca-cert-issue", "--profile", "caUserCert", "--csr-file", f"{t}/a.csr", "--subject", f"UID={USUARIO},CN=FaceOps renovador,O=INVOKE",
            "--output-file", f"{t}/a.crt")
        serie = subprocess.run(["openssl", "x509", "-in", f"{t}/a.crt", "-noout", "-serial"], capture_output=True, text=True).stdout.split("=")[1].strip()
        pki("ca-user-cert-add", USUARIO, "--serial", "0x" + serie)
        datos = {"name": "FaceOps · CA interna", "organization": org["id"], "credential_type": ct["id"],
                 "description": f"Agente {USUARIO} de la CA interna",
                 "inputs": {"url": CA_URL_AAP, "ca": open(f"{P}/ca.crt").read(), "cert": open(f"{t}/a.crt").read(), "llave": open(f"{t}/a.key").read()}}
        cr = aap("PATCH", f"credentials/{cr['id']}/", datos) if cr else aap("POST", "credentials/", datos)
    paso(f"Certificado de cliente del agente emitido (serie 0x{serie}) y guardado cifrado en AAP")
else:
    paso("Credencial «FaceOps · CA interna» ya existe en AAP (usa --rotar para emitir otro certificado al agente)")

for jt in ("Certificados · Emitir para servicio", "Certificados · Renovar por vencer"):
    t_ = uno("job_templates", name=jt)
    if t_ and cr["id"] not in {c["id"] for c in aap("GET", f"job_templates/{t_['id']}/credentials/")["results"]}:
        aap("POST", f"job_templates/{t_['id']}/credentials/", {"id": cr["id"]})
    paso(f"«{jt}»: {'credencial asociada' if t_ else 'NO existe la plantilla (correr faceops/desplegar.sh)'}")
print("LISTO")
