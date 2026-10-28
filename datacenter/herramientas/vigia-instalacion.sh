#!/bin/bash
# Espera a que los equipos RHEL del DC estén registrados en Red Hat y entonces construye la identidad
# (sitio-linux.yml). Se lanza con fondo.sh; el avance queda en ~/datacenter/logs/vigia.log y sitio.log.
DC=${1:-dc1}
cd "$HOME/summit-redhat"
LISTO="$HOME/datacenter/.identidad-lista-$DC"
[ -f "$LISTO" ] && { echo "$(date +%T) la identidad de $DC ya está construida"; exit 0; }
HOSTS=$(awk "/^\[${DC}_linux\]/{f=1;next}/^\[/{f=0}f&&NF{print \$1}" "$HOME/datacenter/$DC.ini")
echo "$(date +%T) esperando el registro de: $HOSTS"
while true; do
  ok=0; n=0
  for h in $HOSTS; do n=$((n+1)); ssh -o ConnectTimeout=5 -o BatchMode=yes "$h" 'sudo subscription-manager identity' >/dev/null 2>&1 && ok=$((ok+1)); done
  [ "$ok" -eq "$n" ] && break
  sleep 30
done
echo "$(date +%T) registrados $ok de $n; construyendo la identidad"
git pull -q
bash datacenter/herramientas/correr.sh playbooks/sitio-linux.yml "$DC" > "$HOME/datacenter/logs/sitio.log" 2>&1
rc=$?
echo "$(date +%T) sitio-linux terminó con código $rc"
tail -25 "$HOME/datacenter/logs/sitio.log"
[ $rc -eq 0 ] || exit 1
touch "$LISTO"
python3 datacenter/cmdb.py "$DC" || true
