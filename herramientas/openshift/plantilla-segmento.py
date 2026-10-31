#!/usr/bin/python3
"""Plantilla «OpenShift · Desplegar segmento» en AAP con sus credenciales fijas (despliegue en OpenShift y escritura en
el repositorio GitOps). La lanza FaceOps desde el plan de un segmento de negocio. Idempotente."""
import base64, glob, json, os, re, ssl, urllib.parse, urllib.request
CL = re.search(r"^gateway_admin_password=(.*)$", open(glob.glob(os.path.expanduser("~/aap/*/inventory-demo"))[0]).read(), re.M).group(1).strip()
AU = "Basic " + base64.b64encode(f"admin:{CL}".encode()).decode(); CTX = ssl._create_unverified_context()
def aap(m, p, d=None):
    q = urllib.request.Request(f"https://127.0.0.1/api/controller/v2/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Authorization": AU, "Content-Type": "application/json"})
    with urllib.request.urlopen(q, context=CTX, timeout=30) as r:
        b = r.read(); return json.loads(b) if b else {}
uno = lambda rec, **k: (aap("GET", f"{rec}/?" + urllib.parse.urlencode(k))["results"] or [None])[0]
org = uno("organizations", name="FaceOps")
proy = uno("projects", name="FaceOps · automatizaciones") or uno("projects", organization=org["id"])
ee = uno("execution_environments", name="Default execution environment")
inv = uno("inventories", name="OpenShift · clústeres")
d = {"organization": org["id"], "project": proy["id"], "playbook": "playbooks/openshift-segmento.yml", "inventory": inv["id"],
     "execution_environment": ee["id"] if ee else None, "ask_variables_on_launch": True,
     "description": "Segmento de negocio en OpenShift: namespace y cuota (Ansible), estado deseado en Git (Argo CD) y pipeline por componente"}
t = uno("job_templates", name="OpenShift · Desplegar segmento")
t = aap("PATCH", f"job_templates/{t['id']}/", d) if t else aap("POST", "job_templates/", dict(d, name="OpenShift · Desplegar segmento"))
ya = {c["id"] for c in aap("GET", f"job_templates/{t['id']}/credentials/")["results"]}
for n in ("OpenShift · SNO (despliegue)", "FaceOps · Gitea GitOps"):
    c = uno("credentials", name=n)
    if not c:
        raise SystemExit(f"Falta la credencial «{n}» (correr preparar-gitops.sh)")
    if c["id"] not in ya:
        aap("POST", f"job_templates/{t['id']}/credentials/", {"id": c["id"]})
print(f"   · plantilla «OpenShift · Desplegar segmento» (id {t['id']})")
