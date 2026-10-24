# Funciones comunes de la Demo 5 (se cargan con: source comun.sh)
NODO=192.168.122.15
SSHJ="ssh -i $HOME/.ssh/id_demo -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR ansible@$NODO"
AAP_URL=https://34-176-42-56.sslip.io
estado_app(){ printf "App: "; curl -s -o /dev/null -w "HTTP %{http_code} · " http://localhost:8090/app-jboss/salud; curl -s http://localhost:8090/app-jboss/salud; echo; }
