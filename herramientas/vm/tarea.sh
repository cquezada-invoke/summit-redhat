S="ssh -i $HOME/.ssh/id_demo -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR cquezada@34.176.114.77"
$S 'sudo timeout 240 /usr/local/sbin/faceadmin-actualizar; sleep 8; sudo podman ps --format "{{.Names}} {{.Status}}"; curl -sk -o /dev/null -w "local %{http_code}\n" https://localhost:8843/ingresar'
