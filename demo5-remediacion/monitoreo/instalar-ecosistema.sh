#!/bin/bash
# Monitoreo de TODO el ecosistema (tablero "Summit · Ecosistema"). Idempotente. Requiere instalar-monitoreo.sh.
#  · blackbox: un sondeo por componente (HTTP, TCP o DNS) · estado_ecosistema.py: EDA, laboratorio de red, BGP, Tekton
#  · alertas ComponenteCaido / ActivacionEDACaida / LaboratorioRedCaido / VigiaCaido · Grafana: inicio = Ecosistema
set -e
D=$(cd "$(dirname "$0")" && pwd); M=/opt/summit-monitoreo; S=~/demo-drift/cicd-secretos.txt
# node_exporter de la VM: métricas de archivo (textfile)
sudo mkdir -p /var/lib/node_exporter/textfile
if ! grep -q textfile /etc/systemd/system/node_exporter.service; then
  sudo sed -i 's#--collector.systemd$#--collector.systemd --collector.textfile.directory=/var/lib/node_exporter/textfile#' /etc/systemd/system/node_exporter.service
  sudo systemctl daemon-reload; sudo systemctl restart node_exporter
fi
sudo cp "$D/summit-estado-ecosistema.service" /etc/systemd/system/
sudo systemctl daemon-reload; sudo systemctl enable -q --now summit-estado-ecosistema.service; sudo systemctl restart summit-estado-ecosistema.service
# Prometheus, blackbox y tableros
sudo cp "$D/prometheus.yml" "$D/alertas.yml" $M/prometheus/; sudo cp "$D/blackbox.yml" $M/blackbox/
sudo cp "$D"/grafana/dashboards/*.json $M/grafana/dashboards/
sudo systemctl restart summit-blackbox.service
sleep 3; curl -s -X POST http://127.0.0.1:9090/-/reload && echo "Prometheus: configuración recargada"
# Grafana: el tablero del ecosistema es la página de inicio
GA=$(sed -n 's/^grafana_admin_clave=//p' $S)
for i in $(seq 1 20); do
  curl -s -u "admin:$GA" http://127.0.0.1:3001/grafana/api/dashboards/uid/summit-ecosistema -o /dev/null -w "%{http_code}" | grep -q 200 && break; sleep 3
done
curl -s -u "admin:$GA" -X PUT -H 'Content-Type: application/json' http://127.0.0.1:3001/grafana/api/org/preferences \
  -d '{"homeDashboardUID":"summit-ecosistema"}' -o /dev/null -w "Grafana: inicio = Summit · Ecosistema (%{http_code})\n"
unset GA
