S="ssh -i $HOME/.ssh/id_demo -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15 cquezada@34.176.114.77"
$S 'bash -s' <<'R'
cd ~/demo-gcp
if pgrep -f construir-imagen.py >/dev/null; then echo "ya corre"; else
  rm -f imagen.log imagen-1.log; setsid nohup python3 construir-imagen.py > imagen.log 2>&1 < /dev/null & echo lanzada; fi
sleep 60; grep -v "k3s\[\|Cloud Logging\|kernel:" imagen.log | grep -v "^  Nov " | tail -5
R
