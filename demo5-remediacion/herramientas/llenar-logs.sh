#!/bin/bash
# Caso 2 (riesgo bajo): llena /var/log/app-jboss (tmpfs de 200 MB, no el disco real) con logs antiguos.
source "$(dirname "$0")/comun.sh"
$SSHJ 'sudo bash -s' <<'R'
cd /var/log/app-jboss
for i in $(seq 1 11); do
  f=app-jboss.$(date -d "-$((i+3)) days" +%F).log
  head -c 14M /dev/urandom | base64 -w 120 | head -c 14M > "$f"
  touch -d "-$((i+3)) days" "$f"
done
for i in 1 2; do f=app-jboss.$(date -d "-$i days" +%F).log; head -c 10M /dev/urandom | base64 -w 120 | head -c 10M > "$f"; touch -d "-$i days" "$f"; done
head -c 3M /dev/urandom | base64 -w 120 | head -c 3M > app-jboss.log
echo "Logs: $(ls | wc -l) archivos · uso $(df --output=pcent . | tail -1 | tr -d ' ')"
R
echo "El vigía avisa a EDA en unos 10 s → 'Remediación · Limpiar logs' borra lo que tenga más de 3 días (política en Git)."
echo "Ver en AAP: $AAP_URL/execution/jobs"
