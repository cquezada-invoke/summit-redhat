#!/bin/bash
# Mueve la fuente de playbooks de AAP del git daemon a Gitea (invoke/automatizacion). Idempotente.
# 1) repo privado con el historial  2) usuario aap-lectura con token read:repository
# 3) credencial "Gitea · lectura de playbooks" por organización  4) proyectos 7, 15 y 21 apuntan a Gitea
# 5) ~/demo-summit-repo hace push a Gitea. Ninguna clave se imprime.
set +e
S=~/demo-drift/cicd-secretos.txt
T=$(sed -n 's/^gitea_token=//p' $S)
G=http://localhost:3000/api/v1
H=(-H "Authorization: token $T" -H "Content-Type: application/json")
curl -s -o /dev/null "${H[@]}" -X POST "$G/user/repos" \
  -d '{"name":"automatizacion","description":"TODAS LAS DEMOS · Playbooks que ejecuta AAP","private":true,"default_branch":"main"}'
git -C ~/demo-summit-repo push -q "http://invoke:$T@localhost:3000/invoke/automatizacion.git" main 2>&1 | sed "s/$T/***/g"
grep -q '^aap_lectura_gitea_clave=' $S || echo "aap_lectura_gitea_clave=$(openssl rand -hex 16)" >> $S
P=$(sed -n 's/^aap_lectura_gitea_clave=//p' $S)
podman exec gitea su-exec git gitea admin user create --config /data/gitea/conf/app.ini --username aap-lectura \
  --email aap-lectura@invoke.cl --password "$P" --must-change-password=false >/dev/null 2>&1
curl -s -o /dev/null "${H[@]}" -X PUT "$G/repos/invoke/automatizacion/collaborators/aap-lectura" -d '{"permission":"read"}'
curl -s -o /dev/null "${H[@]}" -X PUT "$G/repos/invoke/automatizacion/collaborators/invoke" -d '{"permission":"write"}'
if ! grep -q '^aap_lectura_gitea_token=' $S; then
  TK=$(curl -s -u "aap-lectura:$P" -H "Content-Type: application/json" -X POST "$G/users/aap-lectura/tokens" \
       -d '{"name":"aap-proyectos","scopes":["read:repository"]}' | python3 -c 'import sys,json;print(json.load(sys.stdin).get("sha1",""))')
  [ -n "$TK" ] && echo "aap_lectura_gitea_token=$TK" >> $S
fi
export A=$(sed -n 's/^gateway_admin_password=//p' ~/aap/*/inventory-demo) TK=$(sed -n 's/^aap_lectura_gitea_token=//p' $S)
python3 - <<'PY'
import os, json, ssl, time, base64, urllib.request, urllib.parse
B = "https://localhost/api/controller/v2"; ctx = ssl._create_unverified_context()
au = "Basic " + base64.b64encode(f"admin:{os.environ['A']}".encode()).decode()
def api(m, p, d=None):
    r = urllib.request.Request(B + p, method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Authorization": au, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, context=ctx) as x:
        t = x.read(); return json.loads(t) if t else {}
scm = api("GET", "/credential_types/?kind=scm")["results"][0]["id"]
URL = "http://192.168.122.1:3000/invoke/automatizacion.git"; NOMBRE = "Gitea · lectura de playbooks"
creds = {}
for org in (1, 2):
    ex = api("GET", f"/credentials/?name={urllib.parse.quote(NOMBRE)}&organization={org}")["results"]
    datos = {"inputs": {"username": "aap-lectura", "password": os.environ["TK"]}}
    creds[org] = (api("PATCH", f"/credentials/{ex[0]['id']}/", datos) if ex else
                  api("POST", "/credentials/", dict(datos, name=NOMBRE, organization=org, credential_type=scm,
                      description="Solo lectura sobre invoke/automatizacion (usuario aap-lectura)")))["id"]
for nombre in ("Demo Summit", "Despliegues de aplicaciones", "Cumplimiento de servidores"):
    p = api("GET", f"/projects/?name={urllib.parse.quote(nombre)}")["results"][0]
    api("PATCH", f"/projects/{p['id']}/", {"scm_url": URL, "credential": creds.get(p["organization"], creds[1])})
    u = api("POST", f"/projects/{p['id']}/update/", {})
    while api("GET", f"/project_updates/{u['id']}/")["status"] not in ("successful", "failed", "error", "canceled"):
        time.sleep(3)
    p = api("GET", f"/projects/{p['id']}/")
    print("·", nombre, "→", p["status"], p["scm_revision"][:7])
PY
git config --global credential.helper store
grep -q 'invoke:.*@192.168.122.1:3000' ~/.git-credentials 2>/dev/null || printf 'http://invoke:%s@192.168.122.1:3000\n' "$T" >> ~/.git-credentials
chmod 600 ~/.git-credentials
git -C ~/demo-summit-repo remote set-url origin http://192.168.122.1:3000/invoke/automatizacion.git
