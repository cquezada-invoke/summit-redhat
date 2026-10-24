# Funciones comunes de la Demo 5 (se cargan con: source comun.sh)
NODO=192.168.122.15
SSHJ="ssh -i $HOME/.ssh/id_demo -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR ansible@$NODO"
AAP_URL=https://34-176-42-56.sslip.io
estado_app(){
  local c b
  c=$(curl -s -o /tmp/.salud-d5 -w "%{http_code}" http://localhost:8090/app-jboss/salud)
  b=$(tr -s '\n' ' ' < /tmp/.salud-d5 | head -c 90)
  [ "$c" = 200 ] && echo "App: HTTP $c · $b" || echo "App: HTTP $c (no responde bien)"
}
