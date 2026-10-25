#!/usr/bin/env bash
# Demo 1 · /home en modo "solo registro": el vigía avisa y el panel guarda la foto en Git; nunca corrige.
# Idempotente. Reinicia el vigía de los nodos y el panel (unos segundos).
set -euo pipefail
R=~/summit-redhat; D=~/demo-drift
git -C $R pull -q
echo "== Playbooks estado/ al repositorio de AAP"
cp -r $R/demo-summit-repo/estado/. ~/demo-summit-repo/estado/
cd ~/demo-summit-repo && git add estado && { git diff --cached --quiet || git -c user.name='Cristian Quezada' -c user.email=cquezada@invoke.cl commit -qm "Demo 1: /home en la foto de estado"; } && git push -q origin main
echo "== Reglas y vigía en los nodos"
cp $R/demo-drift/files/{drift.rules,vigia.py} $D/files/
cd $D && ansible-playbook -i inventario.ini preparar-nodos.yml | tail -8
echo "== Panel"
python3 - <<'PY'
import json, os
p = os.path.expanduser("~/demo-drift/panel/config.json"); c = json.load(open(p))
c["gitea_estado"] = "http://34.176.42.56:3000/invoke/estado-servidores"
json.dump(c, open(p, "w"), ensure_ascii=False, indent=2)
PY
cp $D/panel/panel.py $D/panel/panel.py.antes-home
cp $R/demo-drift/panel/panel.py $D/panel/panel.py
sudo systemctl restart panel-cumplimiento; sleep 2
curl -sf http://127.0.0.1:8088/api/gestion >/dev/null && echo "Panel en línea"
