#!/bin/bash
# Configura AWX y EDA para la prueba y levanta FaceOps en modo awx (corre como root después de instalar.sh). Idempotente.
set -euo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml PATH=$PATH:/usr/local/bin
P=/root/prueba; umask 077
NODO=$(hostname -I | awk '{print $1}')
python3 - <<'PY'
import base64, json, os, time, urllib.request, urllib.error
P = "/root/prueba"
CLAVE = open(f"{P}/clave").read().strip()
NODO = os.popen("hostname -I").read().split()[0]
B = "Basic " + base64.b64encode(f"admin:{CLAVE}".encode()).decode()
def h(m, url, d=None, auth=B, ok404=False):
    r = urllib.request.Request(url, method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Content-Type": "application/json", "Authorization": auth})
    try:
        with urllib.request.urlopen(r, timeout=60) as x:
            b = x.read(); return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if ok404 and e.code == 404: return None
        raise SystemExit(f"ERROR {m} {url.split('?')[0]} -> {e.code}: {e.read()[:400].decode(errors='replace')}")
A = "http://127.0.0.1:30080/api/v2/"; E = "http://127.0.0.1:30081/api/eda/v1/"
awx = lambda m, p, d=None: h(m, A + p, d)
def uno(rec, nombre, base=A):
    r = h("GET", f"{base}{rec}/?name={urllib.parse.quote(nombre)}")["results"]; return r[0] if r else None
import urllib.parse
def asegurar(rec, nombre, datos, base=A):
    o = uno(rec, nombre, base)
    return h("PATCH", f"{base}{rec}/{o['id']}/", datos) if o else h("POST", f"{base}{rec}/", dict(datos, name=nombre))
org = uno("organizations", "Default")
# token del usuario de servicio (AWX: token personal OAuth2 del admin)
if not os.path.exists(f"{P}/awx_token"):
    t = awx("POST", "users/1/personal_tokens/", {"description": "FaceOps prueba", "scope": "write"})["token"]
    fd = os.open(f"{P}/awx_token", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, t.encode()); os.close(fd)
ct = [c for c in awx("GET", "credential_types/?namespace=ssh")["results"]][0]
cred = asegurar("credentials", "Prueba · SSH", {"organization": org["id"], "credential_type": ct["id"],
                "inputs": {"username": "root", "ssh_key_data": open(f"{P}/id_awx").read()}})
inv = asegurar("inventories", "Prueba", {"organization": org["id"], "description": "Inventario de la prueba FaceOps-AWX"})
if not uno(f"inventories/{inv['id']}/hosts", "nodo-prueba"):
    awx("POST", f"inventories/{inv['id']}/hosts/", {"name": "nodo-prueba", "variables": json.dumps({"ansible_host": NODO, "ambiente": "Producción"})})
pr = asegurar("projects", "Prueba", {"organization": org["id"], "scm_type": "git", "scm_url": open(f"{P}/repo_url").read().strip(),
                                     "scm_branch": "main", "scm_update_on_launch": False})
for _ in range(60):
    st = awx("GET", f"projects/{pr['id']}/")["status"]
    if st in ("successful", "failed", "error"): break
    time.sleep(5)
print("proyecto AWX:", st)
comunes = {"project": pr["id"], "playbook": "ping.yml", "inventory": inv["id"], "ask_limit_on_launch": True, "ask_variables_on_launch": True,
           "ask_credential_on_launch": True}
jt = asegurar("job_templates", "Prueba · Ping", dict(comunes, description="Ping y facts de la máquina"))
jr = asegurar("job_templates", "Prueba · Responder alerta", dict(comunes, description="Lo lanza EDA cuando llega una alerta"))
for j in (jt, jr):
    if cred["id"] not in {c["id"] for c in awx("GET", f"job_templates/{j['id']}/credentials/")["results"]}:
        awx("POST", f"job_templates/{j['id']}/credentials/", {"id": cred["id"]})
wf = asegurar("workflow_job_templates", "Prueba · Workflow con aprobación", {"organization": org["id"], "ask_variables_on_launch": True,
              "ask_limit_on_launch": True, "description": "Aprobación → ping"})
if not awx("GET", f"workflow_job_templates/{wf['id']}/workflow_nodes/")["results"]:
    n1 = awx("POST", f"workflow_job_templates/{wf['id']}/workflow_nodes/", {})
    awx("POST", f"workflow_job_template_nodes/{n1['id']}/create_approval_template/", {"name": "¿Aprueba el ping?", "timeout": 0})
    n2 = awx("POST", f"workflow_job_templates/{wf['id']}/workflow_nodes/", {"unified_job_template": jt["id"]})
    awx("POST", f"workflow_job_template_nodes/{n1['id']}/success_nodes/", {"id": n2["id"]})
print("AWX: inventario, credencial, proyecto, 2 plantillas y workflow con aprobación")
# EDA: proyecto, credencial hacia AWX, decision environment y activación con webhook
ep = uno("projects", "Prueba", E) or h("POST", E + "projects/", {"name": "Prueba", "url": open(f"{P}/repo_url").read().strip(), "organization_id": 1})
for _ in range(60):
    ep = h("GET", f"{E}projects/{ep['id']}/")
    if ep.get("import_state") in ("completed", "failed"): break
    time.sleep(5)
print("proyecto EDA:", ep.get("import_state"), (ep.get("import_error") or "")[:200])
de = uno("decision-environments", "Prueba DE", E) or h("POST", E + "decision-environments/",
     {"name": "Prueba DE", "image_url": "quay.io/ansible/ansible-rulebook:main", "organization_id": 1})
tipos = {t["name"]: t["id"] for t in h("GET", E + "credential-types/?page_size=100")["results"]}
cr = uno("eda-credentials", "Prueba AWX", E) or h("POST", E + "eda-credentials/", {"name": "Prueba AWX", "organization_id": 1,
     "credential_type_id": tipos["Red Hat Ansible Automation Platform"],
     "inputs": {"host": "http://awx-service.awx.svc.cluster.local/api/v2/", "username": "admin", "password": CLAVE, "verify_ssl": False}})
rb = [r for r in h("GET", f"{E}rulebooks/?project_id={ep['id']}")["results"] if r["name"] == "alerta.yml"][0]
act = uno("activations", "Prueba alertas", E)
if not act:
    act = h("POST", E + "activations/", {"name": "Prueba alertas", "is_enabled": True, "decision_environment_id": de["id"], "rulebook_id": rb["id"],
                                          "organization_id": 1, "eda_credentials": [cr["id"]], "restart_policy": "on-failure",
                                          "k8s_service_name": "prueba-alertas"})
print("EDA: activación", act["id"], act.get("status"))
PY
TOK=$(cat $P/awx_token)
cat > $P/faceops.env <<F
FACEOPS_AAP_MODO=awx
FACEOPS_AAP_URL=http://127.0.0.1:30080
FACEOPS_AAP_TOKEN=$TOK
FACEOPS_AAP_PUBLICA=http://127.0.0.1:30080
FACEOPS_EDA_URL=http://127.0.0.1:30081
FACEOPS_EDA_USUARIO=admin
FACEOPS_EDA_CLAVE=$(cat $P/clave)
FACEOPS_VALIDADOR_URL=http://127.0.0.1:8120
FACEOPS_CLIENTE_CODIGO=PRUEBA
FACEOPS_CLIENTE_NOMBRE=Prueba AWX
FACEOPS_ADMIN=prueba
FACEOPS_ADMIN_NOMBRE=Prueba
F
if ! podman container exists faceops; then
  podman run -d --name faceops --network host --env-file $P/faceops.env -v /var/lib/prueba/faceops:/var/lib/faceops:Z localhost/faceops:prueba >/dev/null
fi
for i in $(seq 1 60); do curl -s -o /dev/null -m 3 http://127.0.0.1:8110/ingresar && break; sleep 3; done
echo "FaceOps: HTTP $(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8110/ingresar)"
# enrolar FaceOps con el validador local (código de un solo uso en archivo, nunca en pantalla)
if ! podman exec faceops test -f /var/lib/faceops/confianza/instancia.json 2>/dev/null; then
  podman exec validador python validador.py codigo prueba /data/codigo >/dev/null
  podman exec -e COD="$(cat /var/lib/prueba/validador/codigo)" faceops python3 -c "
import os
from app.confianza import registrar
registrar(os.environ['COD'], 'prueba-awx'); print('FaceOps enrolado en el validador local')"
fi
echo "CONFIGURACION TERMINADA"
