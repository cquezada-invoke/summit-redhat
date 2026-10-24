#!/bin/bash
# Deja la Demo 5 sana: JBoss arriba con la última versión validada, logs vacíos, vigía reiniciado y aprobaciones pendientes canceladas.
source "$(dirname "$0")/comun.sh"
JU=$(sed -n 's/^jboss_usuario=//p' ~/demo-drift/cicd-secretos.txt)
JC=$(sed -n 's/^jboss_clave=//p' ~/demo-drift/cicd-secretos.txt)
$SSHJ 'sudo systemctl stop vigia-jboss; sudo find /var/log/app-jboss -type f ! -name app-jboss.log -delete; sudo truncate -s 0 /var/log/app-jboss/app-jboss.log; sudo systemctl start eap8-standalone'
for i in $(seq 1 40); do $SSHJ 'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/app-jboss/salud' | grep -q 200 && break
  # si responde con error y JBoss está arriba, vuelve a la versión validada
  [ $((i % 6)) -eq 0 ] && $SSHJ "sudo /opt/rh/eap8/root/usr/share/wildfly/bin/jboss-cli.sh --connect --user='$JU' --password='$JC' --command='deploy /var/lib/app-jboss/actual.war --name=app-jboss.war --force'" >/dev/null 2>&1
  sleep 5; done
$SSHJ 'sudo rm -f /var/lib/app-jboss/defectuosa.war; sudo systemctl start vigia-jboss'
python3 - <<'P'
import base64,glob,json,os,re,ssl,urllib.request
inv=glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
c=re.search(r"^gateway_admin_password=(.*)$",open(inv).read(),re.M).group(1).strip()
h={"Authorization":"Basic "+base64.b64encode(f"admin:{c}".encode()).decode(),"Content-Type":"application/json"}
ctx=ssl._create_unverified_context()
def api(m,p,d=None):
    r=urllib.request.Request("https://localhost/api/controller/v2/"+p,method=m,headers=h,data=json.dumps(d).encode() if d is not None else None)
    with urllib.request.urlopen(r,context=ctx) as x: b=x.read(); return json.loads(b) if b else {}
n=0
for a in api("GET","workflow_approvals/?status=pending&page_size=50")["results"]:
    api("POST",f"workflow_approvals/{a['id']}/deny/",{}); n+=1
print(f"Aprobaciones pendientes canceladas: {n}")
P
estado_app
curl -s http://localhost:8090/app-jboss/ | grep -o "Base de datos: [^<]*" || echo "Base de datos: sin dato"
echo "Demo 5 lista."
