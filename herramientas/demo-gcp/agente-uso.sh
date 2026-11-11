#!/bin/bash
# Agente de uso de la Demo instantánea (cada 5 min, timer de systemd). Mide el uso y lo informa a FaceAdmin;
# FaceAdmin responde si hay que apagarse (sin uso, fuera de horario o vencida). Sin FaceAdmin, decide solo con los metadatos.
set -u
M=http://metadata.google.internal/computeMetadata/v1/instance/attributes
md() { curl -sf -H "Metadata-Flavor: Google" "$M/$1"; }
FA=$(md faceadmin-url); NUM=$(md demo-numero); TOK=$(md demo-token); INA=$(md demo-inactividad-min || echo 30); EXP=$(md demo-expira)
EST=/var/lib/demo/ultimo-uso; mkdir -p /var/lib/demo; [ -f "$EST" ] || date +%s > "$EST"

# Uso: conexiones HTTPS establecidas desde fuera (Caddy en 443), sesiones SSH y trabajos de AWX corriendo
https=$(ss -Htn state established '( sport = :443 )' 2>/dev/null | grep -vc '127.0.0.1')
ssh_=$(who | wc -l)
jobs=$(k3s kubectl -n awx get pods -l 'ansible-awx-job-id' --no-headers 2>/dev/null | grep -c Running)
activo=false; { [ "$https" -gt 0 ] || [ "$ssh_" -gt 0 ] || [ "$jobs" -gt 0 ]; } && { activo=true; date +%s > "$EST"; }

r=$(curl -skf -m 20 -X POST -H "Content-Type: application/json" -H "X-Demo-Token: $TOK" \
     -d "{\"activo\": $activo, \"sesiones\": $ssh_, \"http\": $https, \"ssh\": $ssh_, \"cpu\": \"$(cut -d' ' -f1 /proc/loadavg)\"}" \
     "$FA/api/demo-gcp/$NUM/uso" 2>/dev/null)
if [ -n "$r" ]; then
  [ "$(echo "$r" | jq -r .apagar)" = "true" ] && { logger -t demo "apagado pedido por FaceAdmin: $(echo "$r" | jq -r .motivo)"; systemctl poweroff; }
  exit 0
fi
# FaceAdmin no responde: reglas locales (vencida o sin uso)
ahora=$(date +%s)
[ -n "$EXP" ] && [ "$ahora" -ge "$(date -d "$EXP" +%s)" ] && { logger -t demo "vencida"; systemctl poweroff; }
[ $((ahora - $(cat "$EST"))) -gt $((INA * 60)) ] && { logger -t demo "sin uso por $INA min"; systemctl poweroff; }
exit 0
