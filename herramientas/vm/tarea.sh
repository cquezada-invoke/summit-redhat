source ~/summit-redhat/herramientas/pruebas/comun.sh >/dev/null 2>&1
IP=$(ip_de nodo-jboss); echo "ip ok"
en $IP 'sudo systemctl is-active keycloak eap8-standalone redis node_exporter 2>&1; free -m | head -2; ls /etc/containers/systemd/ /var/lib/keycloak/deshabilitado/ 2>&1; sudo journalctl -u keycloak --no-pager -n 25 2>&1 | cut -c1-220 | grep -viE "password|secret|token"'
