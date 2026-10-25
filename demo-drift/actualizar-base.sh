#!/usr/bin/env bash
# Demo 1 · Base aprobada en Git (rama master de invoke/estado-servidores): "Proponer como base" (Pull Request)
# y "Propagar base" (aplica un commit de master). Idempotente. Reinicia el panel (unos segundos).
set -euo pipefail
R=~/summit-redhat; D=~/demo-drift
git -C $R pull -q
echo "== Playbooks estado/ al repositorio de AAP"
rm -f ~/demo-summit-repo/estado/propagar.yml
cp -r $R/demo-summit-repo/estado/. ~/demo-summit-repo/estado/
cd ~/demo-summit-repo && git add -A estado && { git diff --cached --quiet || git -c user.name='Cristian Quezada' -c user.email=cquezada@invoke.cl commit -qm "Demo 1: base aprobada en Git (proponer y aplicar)"; } && git push -q origin main
cd - >/dev/null
echo "== Gitea y AAP"
python3 $R/demo-drift/configurar_estado.py
echo "== Panel"
cp $D/panel/panel.py $D/panel/panel.py.antes-base
cp $R/demo-drift/panel/panel.py $D/panel/panel.py
sudo systemctl restart panel-cumplimiento; sleep 2
curl -sf http://127.0.0.1:8088/api/gestion >/dev/null && echo "Panel en línea"
curl -s http://127.0.0.1:8088/api/base-commits | python3 -c 'import sys,json;[print(c["corto"],c["autor"],c["mensaje"]) for c in json.load(sys.stdin)]'
