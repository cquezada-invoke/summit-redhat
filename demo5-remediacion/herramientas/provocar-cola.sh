#!/bin/bash
# Demo 5 · monitoreo · caso 1: el consumidor de pagos se detiene y la cola pagos.entrada empieza a acumular.
# Prometheus lo ve (> 100 mensajes), Alertmanager avisa a EDA y AAP reactiva el consumidor; la cola baja sola.
N=${1:-600}
ssh nodo-tomcat 'sudo systemctl stop consumidor-pagos'
echo "1) Consumidor de pagos detenido"
ssh nodo-tomcat "sudo podman exec artemis sh -c '/var/lib/artemis-instance/bin/artemis producer --user \"\$ARTEMIS_USER\" --password \"\$ARTEMIS_PASSWORD\" --url tcp://localhost:61616 --destination queue://pagos.entrada --message-count $N --message-size 200 --silent' >/dev/null 2>&1"
echo "2) $N pagos publicados en pagos.entrada (sin nadie que los procese)"
echo "3) Míralo en Grafana: http://34.176.42.56:8090/grafana/  → en ~30 s AAP reactiva el consumidor"
