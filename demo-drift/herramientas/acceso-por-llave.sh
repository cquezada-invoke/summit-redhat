#!/usr/bin/env bash
# Demo 1 · Acceso SSH solo por llave: operador (una llave por nodo) y visitantes (llave por persona).
# Idempotente. Uso en la VM: bash ~/summit-redhat/demo-drift/herramientas/acceso-por-llave.sh
set -euo pipefail
R=~/summit-redhat; D=~/demo-drift
git -C $R pull -q
echo "== Copias locales (panel, plantilla y estándar)"
cp $R/demo-drift/{operador.yml,visitante.yml,so-base.yml} $D/
cp $R/demo-summit-repo/drift/files/sshd_config.j2 $D/files/sshd_config.j2
grep -q '^  - operador$' $D/group_vars/nodos.yml || sed -i 's/^  - grupo-admin$/  - grupo-admin\n  - operador/' $D/group_vars/nodos.yml
echo "== Plantilla al repositorio de AAP"
cp $R/demo-summit-repo/drift/files/sshd_config.j2 ~/demo-summit-repo/drift/files/
cd ~/demo-summit-repo && git add drift && { git diff --cached --quiet || git -c user.name='Cristian Quezada' -c user.email=cquezada@invoke.cl commit -qm "Demo 1: visitantes solo con llave"; } && git push -q origin main; cd - >/dev/null
echo "== Panel"
cp $D/panel/panel.py $D/panel/panel.py.antes-llaves
cp $R/demo-drift/panel/panel.py $D/panel/panel.py
sudo systemctl restart panel-cumplimiento
sleep 3
echo "== Operador en los 5 nodos"
ansible-playbook -i $D/inventario.ini $D/operador.yml | tail -8
echo "== sshd_config en los servidores gestionados"
G=$(python3 -c "import json,os;c=json.load(open(os.path.expanduser('~/demo-drift/panel/config.json')));print(','.join(h for h,g in c.get('gestion',{}).items() if g.get('estado')=='activo'))")
echo "gestionados: $G"
[ -n "$G" ] && (cd $D && ansible-playbook -i inventario.ini so-base.yml -l "$G" | tail -4)
echo "== Prueba operador"
for n in nodo-nginx nodo-so nodo-postgres nodo-tomcat nodo-jboss; do printf '%s: ' $n; ssh -o BatchMode=yes -o ConnectTimeout=5 op-$n 'sudo whoami' 2>&1 | tail -1; done
