#!/usr/bin/python3
# Demo 4 · Aviso por correo de las aprobaciones del workflow "Red · Solicitar acceso con aprobación".
# Notificador de AAP (Gmail SMTP, remitente y destinatario cquezada@invoke.cl). La contraseña de aplicación
# NO está aquí: la ingresa Cristian en AAP (Notifiers → editar → Password). Idempotente: no pisa la contraseña.
import base64, glob, json, os, re, ssl, sys, urllib.error, urllib.parse, urllib.request

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CLAVE = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
CTX = ssl._create_unverified_context()
BASIC = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
NOMBRE, WF, CORREO = "Correo · aprobaciones de red", "Red · Solicitar acceso con aprobación", "cquezada@invoke.cl"


def aap(m, p, d=None, tolerar=False):
    req = urllib.request.Request(f"https://localhost/api/controller/v2/{p}", method=m,
                                 data=json.dumps(d).encode() if d is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": BASIC})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            b = r.read()
            return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar:
            return None
        sys.exit(f"ERROR {m} {p} -> {e.code}: {e.read()[:600].decode(errors='replace')}")


def uno(rec, **f):
    r = aap("GET", f"{rec}/?" + urllib.parse.urlencode(f))["results"]
    return r[0] if r else None


wf = uno("workflow_job_templates", name=WF)
CUERPO = ("Solicitud de acceso de red: {{ job_metadata }}\n\n"
          "Revisa los comandos en el paso 'preparar' y decide aquí:\n{{ workflow_url }}\n")
mensajes = {"workflow_approval": {
    "running": {"message": "Por aprobar: cambio en el firewall ({{ approval_node_name }})", "body": CUERPO},
    "approved": {"message": "Aprobado: cambio en el firewall", "body": "Se aprobó y se está aplicando.\n{{ workflow_url }}\n"},
    "denied": {"message": "Rechazado: cambio en el firewall", "body": "No se aplicó ningún cambio.\n{{ workflow_url }}\n"},
    "timed_out": {"message": "Vencido: cambio en el firewall sin aprobar", "body": "Nadie aprobó a tiempo; no se aplicó ningún cambio.\n{{ workflow_url }}\n"}}}
conf = {"host": "smtp.gmail.com", "port": 587, "use_tls": True, "use_ssl": False, "username": CORREO,
        "sender": CORREO, "recipients": [CORREO], "timeout": 30}
o = uno("notification_templates", name=NOMBRE)
if o:
    conf["password"] = "$encrypted$"  # conserva la contraseña que ya ingresó Cristian
    nt = aap("PATCH", f"notification_templates/{o['id']}/", {"notification_configuration": conf, "messages": mensajes})
else:
    conf["password"] = ""
    nt = aap("POST", "notification_templates/", {"name": NOMBRE, "organization": wf["organization"], "notification_type": "email",
                                                  "description": "Avisa a Cristian cuando un cambio de red espera aprobación",
                                                  "notification_configuration": conf, "messages": mensajes})
aap("POST", f"workflow_job_templates/{wf['id']}/notification_templates_approvals/", {"id": nt["id"]}, tolerar=True)
print(f"   · Notificador '{NOMBRE}' (id {nt['id']}) asociado a las aprobaciones del workflow {wf['id']}")
print(f"   NOTIFICADOR_ID {nt['id']}")
