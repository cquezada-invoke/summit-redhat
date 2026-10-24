#!/usr/bin/env bash
# Demo 1 · Instala la gestión de servidores del panel (agregar/quitar con AAP). Idempotente.
#   bash actualizar-gestion.sh prueba   -> deja una copia del panel nuevo en el puerto interno 8089
#   bash actualizar-gestion.sh activar  -> reemplaza el panel publicado (8088/9443) y deja solo nodo-so gestionado
set -euo pipefail
R=~/summit-redhat; D=~/demo-drift
git -C $R pull -q
echo "== Playbooks drift/ al repositorio de AAP"
mkdir -p ~/demo-summit-repo/drift && cp $R/demo-summit-repo/drift/*.yml ~/demo-summit-repo/drift/
cd ~/demo-summit-repo && git add drift && { git diff --cached --quiet || git -c user.name='Cristian Quezada' -c user.email=cquezada@invoke.cl commit -qm "Demo 1: incorporar y retirar servidores de la gestión"; } && git push -q origin main
cd - >/dev/null
echo "== Estándar y autorizaciones"
cp $R/demo-drift/so-base.yml $D/so-base.yml
grep -q 'grupo-admin' $D/group_vars/nodos.yml || sed -i 's/^  - visitantes$/  - visitantes\n  - grupo-admin/' $D/group_vars/nodos.yml
echo "== AAP"
python3 $R/demo-drift/configurar_gestion_aap.py
python3 - <<'PY'
import json, os
p = os.path.expanduser("~/demo-drift/panel/config.json"); c = json.load(open(p))
c.setdefault("aap", {}).update({"ui": "https://34-176-42-56.sslip.io", "secretos": os.path.expanduser("~/demo-drift/cicd-secretos.txt"),
                                "usuario": "panel-drift", "inventario": "Drift · Servidores"})
c.setdefault("usuario_admin", "cquezada")
for n in c["nodos"]:
    if n["nombre"] == "nodo-tomcat": n["rol"] = "Keycloak · Artemis · Valkey"
json.dump(c, open(p, "w"), ensure_ascii=False, indent=2)
PY
case "${1:-prueba}" in
  prueba)
    P=$D/panel-prueba; mkdir -p $P
    cp $R/demo-drift/panel/panel.py $P/
    python3 - <<'PY'
import json, os
c = json.load(open(os.path.expanduser("~/demo-drift/panel/config.json")))
c["puerto"] = 8089; c.pop("puerto_https", None); c.pop("gestion", None)
c["url_publica"] = "http://127.0.0.1:8089"
json.dump(c, open(os.path.expanduser("~/demo-drift/panel-prueba/config.json"), "w"), ensure_ascii=False, indent=2)
PY
    pkill -f "panel-prueba/panel.py" || true
    cd $P && nohup python3 $P/panel.py > $P/panel.log 2>&1 &
    sleep 2; curl -sf http://127.0.0.1:8089/api/gestion >/dev/null && echo "Panel de prueba en http://127.0.0.1:8089" ;;
  activar)
    pkill -f "panel-prueba/panel.py" || true
    cp $D/panel/panel.py $D/panel/panel.py.anterior
    cp $R/demo-drift/panel/panel.py $D/panel/panel.py
    sudo systemctl restart panel-cumplimiento; sleep 2
    bash $R/demo-drift/herramientas/reiniciar-panel.sh ;;
esac
