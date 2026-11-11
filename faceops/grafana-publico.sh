#!/bin/bash
# FaceOps · Grafana del monitoreo por el acceso público, SOLO con sesión de FaceOps (tablero «Alertas de FaceOps» del botón
# «Alertas» de cada máquina). El proxy público (faceops-publico) pregunta a FaceOps por la sesión (auth_request a
# /sesion/valida); sin sesión manda a ingresar. Entra a Grafana como «visitante» (rol Viewer) y es de solo lectura: solo GET,
# y POST únicamente a las consultas de datos; administración, login, proxy de datos e instantáneas bloqueados.
# La clave del visitante sale de ~/demo-drift/cicd-secretos.txt y nunca se imprime. Idempotente.
set -euo pipefail
D=/var/lib/faceops-publico/conf
S=${GRAFANA_SECRETOS:-$HOME/demo-drift/cicd-secretos.txt}
GV=$(sed -n 's/^grafana_visitante_clave=//p' "$S")
[ -n "$GV" ] || { echo "Sin clave del visitante de Grafana en $S"; exit 1; }
umask 077
printf 'proxy_set_header Authorization "Basic %s";\n' "$(printf 'visitante:%s' "$GV" | base64 -w0)" | sudo tee $D/grafana-visitante.inc >/dev/null
sudo chmod 600 $D/grafana-visitante.inc
sudo tee $D/faceops-grafana.inc >/dev/null <<'C'
# Grafana del monitoreo detrás de la sesión de FaceOps (faceops/grafana-publico.sh)
location = /_faceops_sesion {
    internal;
    proxy_pass http://192.168.122.1:8110/sesion/valida;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header Accept application/json;
    include /etc/nginx/conf.d/faceops-cabeceras.inc;
}
location @faceops_ingresar { return 302 /faceops/ingresar; }
# nada de administración, sesiones propias de Grafana, proxy de datos ni instantáneas
location ~ ^/grafana/(login|logout|api/(admin|users|serviceaccounts|auth|snapshots|playlists|user/(password|auth-tokens)|datasources/proxy|datasources/uid/[^/]+/proxy)) { return 403; }
# consultas de datos (POST de solo lectura)
location ~ ^/grafana/api/(ds/query|frontend-metrics|datasources/uid/[^/]+/resources/api/v1/(labels|label/[^/]+/values|series|query|query_range|query_exemplars|metadata))$ {
    auth_request /_faceops_sesion;
    error_page 401 = @faceops_ingresar;
    limit_except GET POST { deny all; }
    limit_req zone=consolas burst=400 nodelay;
    proxy_pass http://127.0.0.1:3001;
    include /etc/nginx/conf.d/faceops-grafana-prox.inc;
}
location /grafana/ {
    auth_request /_faceops_sesion;
    error_page 401 = @faceops_ingresar;
    limit_except GET { deny all; }
    limit_req zone=consolas burst=400 nodelay;
    proxy_pass http://127.0.0.1:3001;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $http_connection;
    include /etc/nginx/conf.d/faceops-grafana-prox.inc;
}
C
sudo tee $D/faceops-grafana-prox.inc >/dev/null <<'C'
proxy_set_header Host $http_host;
proxy_set_header X-Forwarded-Proto https;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header Cookie "";
proxy_hide_header Set-Cookie;
include /etc/nginx/conf.d/grafana-visitante.inc;
C
# el bloque va dentro del servidor TLS, antes de su «location / { return 404; }»
if ! sudo grep -q 'faceops-grafana.inc' $D/faceops.conf; then
  sudo cp -p $D/faceops.conf $D/faceops.conf.antes-grafana
  sudo python3 - $D/faceops.conf <<'P'
import sys
p = sys.argv[1]; s = open(p).read(); k = s.rfind("    location / { return 404; }")
assert k > 0, "sin location final"
open(p, "w").write(s[:k] + "    include /etc/nginx/conf.d/faceops-grafana.inc;\n" + s[k:])
P
fi
if sudo podman exec faceops-publico nginx -t -q 2>/dev/null; then
  sudo podman exec faceops-publico nginx -s reload; echo "Grafana público detrás de la sesión de FaceOps: /grafana/"
else
  echo "nginx -t falló: vuelvo atrás"; [ -f $D/faceops.conf.antes-grafana ] && sudo cp -p $D/faceops.conf.antes-grafana $D/faceops.conf
  sudo podman exec faceops-publico nginx -t 2>&1 | grep -v -i basic | tail -3; exit 1
fi
