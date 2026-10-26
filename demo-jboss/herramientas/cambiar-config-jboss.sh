#!/bin/bash
# Demo · JBoss como código: cambia la configuración de TODOS los JBoss con un solo commit en jboss-config.
# Ejemplo: alterna el nivel de log raíz entre INFO y WARN (o el valor que se pase: cambiar-config-jboss.sh DEBUG).
# Gitea avisa a EDA → AAP aplica servidor por servidor → el manifiesto (repo ambientes) registra el commit.
set -e; source "$(dirname "$0")/comun.sh"
D=$(mktemp -d); trap 'rm -rf $D' EXIT
git clone -q "http://invoke:$GT@127.0.0.1:3000/invoke/jboss-config.git" $D && cd $D
ACT=$(sed -n 's/^  nivel_raiz: *\([A-Z]*\).*/\1/p' comun/jboss.yml)
NUEVO=${1:-$([ "$ACT" = INFO ] && echo WARN || echo INFO)}
sed -i "s/^  nivel_raiz: *$ACT/  nivel_raiz: $NUEVO/" comun/jboss.yml
git -c user.name="Cristian Quezada" -c user.email=cquezada@invoke.cl commit -qam "Logging: nivel raíz $ACT → $NUEVO"
git push -q 2>&1 | sed "s/$GT/***/g"
echo "Commit $(git rev-parse --short HEAD) en jboss-config: nivel de log raíz $ACT → $NUEVO"
echo "Antes:"; estado
echo "AAP lo aplica en ambos JBoss (uno a la vez). Esperando…"
sleep 8; PYTHONPATH=~/bin python3 - <<'PY'
import time, urllib.parse
from aapapi import aap
jt = aap("GET", "job_templates/?name=" + urllib.parse.quote("JBoss · Aplicar configuración"))["results"][0]
for _ in range(60):
    j = aap("GET", f"job_templates/{jt['id']}/jobs/?order_by=-id&page_size=1")["results"][0]
    if j["status"] in ("successful", "failed", "error", "canceled"): break
    time.sleep(4)
print(f"   AAP job {j['id']}: {j['status']} · https://34.176.42.56/execution/jobs/playbook/{j['id']}/output")
PY
echo "Después:"; estado
echo "Manifiesto: http://34.176.42.56:3000/invoke/ambientes/src/branch/main/produccion.yml"
