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
_r = uno("job_templates", name="Red · Responder solicitud")
RESP_URL = f"https://34.176.42.56/execution/templates/job-template/{_r['id']}/details" if _r else "(plantilla Red · Responder solicitud)"
# job_metadata trae las variables del workflow como texto: se extraen ticket, origen, destino, puerto y motivo.
def campo(n, num=False):
    if num:
        return f"(job_metadata.split('{n}\\\\\": ')[1].split(',')[0] if '{n}\\\\\": ' in job_metadata else '-')"
    return f"(job_metadata.split('{n}\\\\\": \\\\\"')[1].split('\\\\\"')[0] if '{n}\\\\\": \\\\\"' in job_metadata else '-')"


SETS = ("{%- set t = " + campo("snow_ritm") + " -%}{%- set o = " + campo("acceso_origen") + " -%}"
        "{%- set d = " + campo("acceso_destino") + " -%}{%- set p = " + campo("acceso_puerto") + " -%}"
        "{%- set p = (" + campo("acceso_puerto", True) + " if p == '-' else p) -%}"
        "{%- set m = " + campo("snow_motivo") + " -%}{%- set c = " + campo("osh_cluster") + " -%}"
        "{%- set k = " + campo("solicitud_tipo") + " -%}{%- set rt = " + campo("rv_ticket") + " -%}"
        "{%- set rd = " + campo("rv_destino") + " -%}"
        "{%- set q = ('retiro de acceso de red' if k == 'revocar' else ('acceso de red' if c == '-' else 'red para clúster OpenShift')) -%}")
PEDIDO = ("Ticket: {{ t }}\n{% if k == 'revocar' %}Retirar el acceso {{ ('del ticket ' ~ rt) if rt not in ['-', ''] else ('hacia ' ~ rd) }}"
          "{% elif c == '-' %}Acceso: desde {{ o }} hacia {{ d }} por {{ p }}"
          "{% else %}Red para el clúster OpenShift: {{ c }} (segmento, router, firewall y DNS){% endif %}\nMotivo: {{ m }}\n")
mensajes = {"workflow_approval": {
    "running": {"message": SETS + "Por aprobar: {{ q }} {{ t }}",
                "body": SETS + "Hola Cristian,\n\nHay un cambio de red esperando tu aprobación.\n\n" + PEDIDO +
                        "\nComandos exactos (paso 'preparar'):\n{{ workflow_url }}\n\n"
                        "Para aprobar o rechazar con motivo, lanza 'Red · Responder solicitud' con el ticket {{ t }}:\n" + RESP_URL + "\n\n"
                        "Si nadie responde en 4 horas, la solicitud se cierra sin cambios.\n"},
    "approved": {"message": SETS + "Aprobado: {{ q }} {{ t }}",
                 "body": SETS + "Aprobaste el cambio. Se está aplicando y el ticket se cerrará solo.\n\n" + PEDIDO + "\n{{ workflow_url }}\n"},
    "denied": {"message": SETS + "Rechazado: {{ q }} {{ t }}",
               "body": SETS + "Rechazaste el cambio. No se aplicó ningún cambio y el ticket se cerró como no aprobado.\n\n" + PEDIDO + "\n{{ workflow_url }}\n"},
    "timed_out": {"message": SETS + "Vencido: {{ q }} {{ t }}",
                  "body": SETS + "Nadie aprobó a tiempo. No se aplicó ningún cambio y el ticket se cerró como no aprobado.\n\n" + PEDIDO + "\n{{ workflow_url }}\n"}}}
conf = {"host": "smtp.gmail.com", "port": 587, "use_tls": True, "use_ssl": False, "username": CORREO,
        "sender": CORREO, "recipients": [CORREO], "timeout": 30}
o = uno("notification_templates", name=NOMBRE)
if o:
    conf["password"] = "$encrypted$"  # conserva la contraseña que ya ingresó Cristian
    actuales = o.get("messages") or {}  # conserva los mensajes de otros usos (p. ej. bloqueo de IP)
    actuales.update(mensajes)
    nt = aap("PATCH", f"notification_templates/{o['id']}/", {"notification_configuration": conf, "messages": actuales})
else:
    conf["password"] = ""
    nt = aap("POST", "notification_templates/", {"name": NOMBRE, "organization": wf["organization"], "notification_type": "email",
                                                  "description": "Avisa a Cristian cuando un cambio de red espera aprobación",
                                                  "notification_configuration": conf, "messages": mensajes})
for nombre_wf in (WF, "Red · Preparar red OpenShift con aprobación", "Red · Revocar acceso con aprobación"):
    w = uno("workflow_job_templates", name=nombre_wf)
    if w:
        aap("POST", f"workflow_job_templates/{w['id']}/notification_templates_approvals/", {"id": nt["id"]}, tolerar=True)
print(f"   · Notificador '{NOMBRE}' (id {nt['id']}) asociado a las aprobaciones del workflow {wf['id']}")
print(f"   NOTIFICADOR_ID {nt['id']}")
