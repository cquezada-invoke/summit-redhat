#!/bin/bash
# Demo 5 · monitoreo por máquina: node_exporter en los Linux (y la VM anfitriona), windows_exporter en nodo-windows,
# job "servidores" en Prometheus, alertas de máquina y tablero "Summit · Servidores" en Grafana. Idempotente.
set -e
D=$(cd "$(dirname "$0")" && pwd); M=/opt/summit-monitoreo
echo "   · node_exporter en los servidores Linux y la VM anfitriona"
ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook -i $D/inventario-servidores.ini $D/exportadores-linux.yml | tail -n +1 | grep -E "^(PLAY RECAP|nodo|anfitriona)" || true
echo "   · windows_exporter en nodo-windows (plantilla de AAP)"
PYTHONPATH=$D python3 $D/configurar_servidores.py
echo "   · Prometheus, alertas y tablero"
sudo cp $D/prometheus.yml $D/alertas.yml $M/prometheus/
sudo cp $D/grafana/dashboards/*.json $M/grafana/dashboards/; sudo chown -R 472:0 $M/grafana/dashboards
curl -s -X POST http://127.0.0.1:9090/-/reload && echo "   · Prometheus recargado"
