#!/bin/bash
# Demo 5 · monitoreo · caso 3: "muchos logins fallidos en Keycloak" desde una IP de Internet.
# Se simula el DATO (no se generan intentos reales): nodo-tomcat publica la métrica de logins fallidos del último
# minuto; Prometheus dispara la alerta (se ve en Grafana) → Alertmanager → EDA → "Red · Bloquear IP" (Demo 4):
# la IP queda bloqueada en el firewall, se abre un incidente en ServiceNow y llega un correo.
IP=${1:-203.0.113.50}; N=${2:-60}
F=/var/lib/node_exporter/textfile/keycloak.prom
alcance(){ sudo podman exec clab-summit-red-isp timeout 4 nc -z -w3 -s "$IP" 10.20.20.2 22 >/dev/null 2>&1 && echo "LLEGA" || echo "BLOQUEADA"; }
publicar(){ ssh nodo-tomcat "printf '# HELP keycloak_logins_fallidos_ultimo_minuto Logins fallidos por IP en el último minuto\n# TYPE keycloak_logins_fallidos_ultimo_minuto gauge\nkeycloak_logins_fallidos_ultimo_minuto{ip=\"$IP\",intentos=\"$1\"} $1\n' | sudo tee $F.tmp >/dev/null && sudo mv $F.tmp $F"; }
echo "1) Desde Internet ($IP): $(alcance)"
publicar "$N"
echo "2) Keycloak registra $N logins fallidos desde $IP en el último minuto (míralo en Grafana: Alertas activas)"
printf "3) Esperando que Ansible bloquee la IP (Prometheus → EDA → AAP, ~30-60 s)"
for i in $(seq 1 45); do [ "$(alcance)" = BLOQUEADA ] && break; printf "."; sleep 2; done
echo; echo "4) Desde Internet ($IP): $(alcance)"
publicar 0
echo "5) El ataque se detuvo: la alerta se cierra sola en Grafana en unos segundos"
