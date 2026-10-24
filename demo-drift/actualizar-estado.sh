#!/usr/bin/env bash
# Demo 1 · Instala "Solo registrar" (estado en Git) y "Usar como referencia" (propagar). Idempotente.
#   bash actualizar-estado.sh            -> playbooks, Gitea, AAP y panel nuevo (reinicio breve del panel)
set -euo pipefail
R=~/summit-redhat; D=~/demo-drift
git -C $R pull -q
echo "== Playbooks estado/ al repositorio de AAP"
mkdir -p ~/demo-summit-repo/estado && cp -r $R/demo-summit-repo/estado/. ~/demo-summit-repo/estado/
cd ~/demo-summit-repo && git add estado && { git diff --cached --quiet || git -c user.name='Cristian Quezada' -c user.email=cquezada@invoke.cl commit -qm "Demo 1: registrar estado en Git y propagar referencia"; } && git push -q origin main
cd - >/dev/null
echo "== Gitea y AAP"
python3 $R/demo-drift/configurar_estado.py
echo "== Panel"
python3 - <<'PY'
import json, os
p = os.path.expanduser("~/demo-drift/panel/config.json"); c = json.load(open(p))
c["gitea_estado"] = "http://34-176-42-56.sslip.io:3000/invoke/estado-servidores"
c["panel_url_aap"] = "http://192.168.122.1:%d" % c["puerto"]
c.setdefault("similares", ["nodo-so", "nodo-nginx", "nodo-postgres"])
json.dump(c, open(p, "w"), ensure_ascii=False, indent=2)
PY
cp $D/panel/panel.py $D/panel/panel.py.antes-estado
cp $R/demo-drift/panel/panel.py $D/panel/panel.py
sudo systemctl restart panel-cumplimiento; sleep 2
curl -sf http://127.0.0.1:8088/api/gestion >/dev/null && echo "Panel en línea"
