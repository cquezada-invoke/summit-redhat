#!/bin/bash
# =====================================================================================================================
#  FaceOps · instalación completa para {{ c.nombre }}  (RHEL 9)
#  Contrato {{ k.numero }} · Instancia {{ c.instancia_id }} · Versión fijada {{ commit[:12] }} · Generado {{ generado }}
#  PASO 2 de 3 (instalación) y 3 de 3 (commit si todo quedó correcto, rollback de todo lo configurado si algo falla).
#  Instala en esta máquina: Ansible Automation Platform (gateway, controller, hub, EDA y sus bases PostgreSQL), el catálogo
#  de acciones de FaceOps y FaceOps, y lo enlaza con FaceAdmin. Este archivo lleva la LLAVE PROPIA del cliente: no lo copies
#  a otro cliente. Se usa así:   curl -fsSL <enlace de FaceAdmin> | sudo bash
#  Bitácora completa: /var/log/faceops-instalacion.log · Claves generadas: /root/faceops-aap.env (solo root)
# =====================================================================================================================
# con «curl | bash» el script llega por la entrada estándar: se lee entero dentro de principal() y se ejecuta sin esa entrada
principal() {
set -euo pipefail
INSTANCIA="{{ c.instancia_id }}"; CODIGO="{{ c.codigo }}"; NOMBRE="{{ c.nombre }}"; CONTRATO="{{ k.numero }}"
REPO="{{ repo }}"; COMMIT="{{ commit }}"; FADMIN="{{ admin }}"; LLAVE="{{ tok }}"; HID="{{ host_id }}"
LOG=/var/log/faceops-instalacion.log; R=/opt/faceops-repo; ENVF=/root/faceops-aap.env
TX=/var/lib/faceops-instalacion; DIARIO=$TX/diario; COMMITEADO=0; PASO=inicio; MOTIVO=""
[ "$(id -u)" = 0 ] || { echo "✗ Ejecuta con sudo"; exit 1; }
install -d -m 700 "$TX"; touch "$LOG" "$DIARIO"; chmod 600 "$LOG" "$DIARIO"
[ "$(printf '%s|%s' "$(cat /etc/machine-id 2>/dev/null)" "$INSTANCIA" | sha256sum | cut -c1-32)" = "$HID" ] \
  || { echo "✗ Este paso 2 se generó para otra máquina: corre el paso 1 (validación) en esta."; exit 1; }
# avisa a FaceAdmin cada paso (para verlo en cajas desde la ficha del contrato); si FaceAdmin no responde, se sigue igual
ev() { local f=$1 e=$2 p=$3 m=${4:-} l=${5:-}
  curl -sk -m 15 -X POST -H "X-FaceAdmin-Token: $LLAVE" -H "Content-Type: application/json" -d "$(python3 -c 'import json,sys
print(json.dumps({"fase": sys.argv[1], "estado": sys.argv[2], "host_id": sys.argv[3], "host": sys.argv[4], "detalle": {"paso": sys.argv[5], "motivo": sys.argv[6], "log": sys.argv[7][-12000:]}}, ensure_ascii=False))' \
  "$f" "$e" "$HID" "$(hostname)" "$p" "$m" "$l")" "$FADMIN/api/instalacion/evento" >/dev/null 2>>"$LOG" || echo "[$(date '+%F %T')] aviso a FaceAdmin no entregado ($f/$e/$p)" >> "$LOG"; }
L0=1
log_paso() {   # el log del paso en curso, sin claves (se reemplazan por ***)
  local t; t=$(tail -n +"$L0" "$LOG" 2>/dev/null | tail -150)
  if [ -s "$ENVF" ]; then while IFS='=' read -r _ v; do [ -n "$v" ] && t=${t//"$v"/***}; done < "$ENVF"; fi
  printf '%s' "$t"; }
paso() { [ "$PASO" != inicio ] && [ "$PASO" != "$1" ] && ev instalacion ok "$PASO" "" "$(log_paso)"; PASO=$1; shift; L0=$(( $(wc -l < "$LOG") + 1 ))
  echo "· $*"; echo "[$(date '+%F %T')] [$PASO] $*" >> "$LOG"; ev instalacion en_curso "$PASO" "$*"; }
falla() { MOTIVO="$*"; echo; echo "✗ $*"; echo "[$(date '+%F %T')] ERROR: $*" >> "$LOG"; exit 1; }
diario() { echo "$*" >> "$DIARIO"; }   # cada cambio que se hace, para poder revertirlo

rollback() {   # deshace en orden inverso todo lo anotado en el diario
  echo; echo "↺ Revirtiendo todo lo configurado…"; local hecho=() a x
  set +e
  while IFS='|' read -r a x; do
    case "$a" in
      faceops) systemctl disable --now faceops faceops-actualizar >/dev/null 2>&1; rm -f /etc/containers/systemd/faceops.container /etc/systemd/system/faceops-actualizar.service \
                 /usr/local/sbin/faceops-actualizar /etc/faceops.env; rm -rf /etc/faceops /var/lib/faceops; podman rmi -f localhost/faceops:latest >/dev/null 2>&1
               systemctl daemon-reload; hecho+=("FaceOps");;
      repo) rm -rf "$R"; hecho+=("código de FaceOps");;
      aap) [ -f "$x/inventory-faceops" ] && sudo -iu aap bash -c "cd '$x' && ansible-playbook -i inventory-faceops ansible.containerized_installer.uninstall" >>"$LOG" 2>&1 < /dev/null
           sudo -iu aap podman system reset -f >>"$LOG" 2>&1 < /dev/null; hecho+=("AAP y sus bases de datos");;
      envf) rm -f "$ENVF"; hecho+=("claves generadas");;
      usuario) loginctl disable-linger aap 2>/dev/null; loginctl terminate-user aap 2>/dev/null; systemctl stop "user@$(id -u aap 2>/dev/null).service" 2>/dev/null
               for _ in 1 2 3 4 5; do pkill -9 -u aap 2>/dev/null; sleep 2; userdel -r aap >/dev/null 2>&1 && break; done
               rm -f /etc/sudoers.d/aap-faceops; id aap >/dev/null 2>&1 && hecho+=("usuario aap (NO se pudo borrar)") || hecho+=("usuario aap");;
      hostname) hostnamectl set-hostname "$x"; sed -i "/ faceops-$CODIGO\$/d" /etc/hosts; hecho+=("nombre de host ($x)");;
      firewall) firewall-cmd -q --remove-service=https --remove-port=8843/tcp 2>/dev/null; firewall-cmd -q --permanent --remove-service=https --remove-port=8843/tcp 2>/dev/null; hecho+=("firewall");;
      paquetes) [ -n "$x" ] && dnf -q -y remove $x >>"$LOG" 2>&1; hecho+=("paquetes instalados (${x:-ninguno})");;
    esac
  done < <(tac "$DIARIO")
  : > "$DIARIO"
  echo "  revertido: $(IFS=', '; echo "${hecho[*]:-nada}")"
  ev rollback ok "$PASO" "$MOTIVO · revertido: $(IFS=', '; echo "${hecho[*]:-nada}")" "$(log_paso)"
}
al_salir() { local rc=$?
  if [ "$COMMITEADO" = 1 ]; then return; fi
  [ -n "$MOTIVO" ] || MOTIVO="falló el paso «$PASO» (código $rc)"
  ev instalacion error "$PASO" "$MOTIVO" "$(log_paso)"
  L0=$(( $(wc -l < "$LOG") + 1 ))
  rollback
  echo "✗ La instalación no se completó: $MOTIVO. La máquina quedó como estaba. Bitácora: $LOG"
}
trap al_salir EXIT
trap 'MOTIVO="falló el paso «$PASO» (línea $LINENO)"' ERR
echo "══ FaceOps · paso 2 de 3: instalación · $NOMBRE · contrato $CONTRATO · $INSTANCIA"
echo "   ⚠  Máquina dedicada: se eliminan restos de instalaciones anteriores de AAP y FaceOps en esta máquina."
echo "   Si algo falla, se revierte todo lo configurado (paso 3: commit o rollback)."

# 1 · contrato -----------------------------------------------------------------------------------------------------
paso contrato "revalidando el contrato en FaceAdmin"
V=$(curl -sk -m 20 -H "X-FaceAdmin-Token: $LLAVE" "$FADMIN/api/instalacion/validar" || true)
echo "$V" | python3 -c 'import json,sys
d=json.load(sys.stdin)
if not d.get("ok"): print("✗", d.get("motivo") or "FaceAdmin rechazó la instalación"); sys.exit(1)
print("  ✓ contrato", d["contrato"], "vigente")' 2>>"$LOG" || falla "FaceAdmin no confirmó el contrato"

# 2 · sistema --------------------------------------------------------------------------------------------------------
paso sistema "preparando el sistema (limpieza de restos, disco, paquetes base, firewall)"
# la máquina se asume dedicada: se eliminan restos de intentos anteriores (no se pueden restaurar)
if [ -e /etc/faceops.env ] || [ -e /etc/containers/systemd/faceops.container ] || [ -d "$R" ]; then
  echo "  limpiando restos de un FaceOps anterior" | tee -a "$LOG"
  systemctl disable --now faceops faceops-actualizar >/dev/null 2>&1 || true
  rm -f /etc/containers/systemd/faceops.container /etc/systemd/system/faceops-actualizar.service /usr/local/sbin/faceops-actualizar /etc/faceops.env
  rm -rf /etc/faceops /var/lib/faceops "$R"; podman rmi -f localhost/faceops:latest >/dev/null 2>&1 || true; systemctl daemon-reload
fi
if id aap >/dev/null 2>&1; then
  echo "  limpiando restos de un AAP anterior (usuario aap, contenedores y bases de datos)" | tee -a "$LOG"
  BV=$(ls -d /home/aap/ansible-automation-platform-containerized-setup-bundle-*/ 2>/dev/null | head -1 || true)
  [ -n "$BV" ] && [ -f "$BV/inventory-faceops" ] && sudo -iu aap bash -c "cd '$BV' && ansible-playbook -i inventory-faceops ansible.containerized_installer.uninstall" >>"$LOG" 2>&1 < /dev/null || true
  sudo -iu aap podman system reset -f >>"$LOG" 2>&1 < /dev/null || true
  loginctl disable-linger aap 2>/dev/null || true; loginctl terminate-user aap 2>/dev/null || true
  for _ in 1 2 3 4 5; do pkill -9 -u aap 2>/dev/null || true; sleep 2; userdel -r aap >/dev/null 2>&1 && break; done
  id aap >/dev/null 2>&1 && falla "No se pudo eliminar el usuario «aap» de un intento anterior"
  rm -f /etc/sudoers.d/aap-faceops
fi
rm -f "$ENVF"
RAIZ=$(findmnt -no SOURCE /); DEV=/dev/$(lsblk -no PKNAME "$RAIZ" 2>/dev/null | head -1); PART=$(echo "$RAIZ" | grep -o '[0-9]*$')
if [ -n "$PART" ] && [ -b "$DEV" ]; then   # si el disco creció (nube), la partición raíz se extiende
  command -v growpart >/dev/null || dnf -q -y install cloud-utils-growpart >>"$LOG" 2>&1 || true
  growpart "$DEV" "$PART" >>"$LOG" 2>&1 && { xfs_growfs / >>"$LOG" 2>&1 || resize2fs "$RAIZ" >>"$LOG" 2>&1 || true; }
fi
CPU=$(nproc); RAM=$(awk '/MemTotal/{printf "%d", $2/1024/1024}' /proc/meminfo); DISCO=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
echo "  $CPU vCPU · $RAM GB de memoria · $DISCO GB libres en /"
[ "$CPU" -ge {{ minimo.cpu }} ] && [ "$RAM" -ge {{ minimo.ram_gb }} ] && [ "$DISCO" -ge {{ minimo.disco_gb }} ] \
  || falla "Mínimo: {{ minimo.cpu }} vCPU, 16 GB de memoria y {{ minimo.disco_gb }} GB libres"
PUB=${FACEOPS_IP_PUBLICA:-$(curl -s -m 3 -H "Metadata-Flavor: Google" http://metadata/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null || true)}
IPP=$(hostname -I | awk '{print $1}'); [ -n "$PUB" ] || PUB=$IPP
NUEVOS=$(for p in ansible-core podman rsync tar firewalld git-core openssl python3; do rpm -q "$p" >/dev/null 2>&1 || printf '%s ' "$p"; done)
diario "paquetes|$NUEVOS"
dnf -q -y install ansible-core podman rsync tar firewalld git-core openssl python3 >>"$LOG" 2>&1 \
  || falla "dnf no pudo instalar los paquetes base: la máquina debe estar registrada en Red Hat o tener sus repositorios"
if systemctl is-active -q firewalld; then   # solo si el cliente ya usa firewalld: no se activa uno nuevo (puede cortar la red de la máquina)
  diario "firewall|"
  firewall-cmd -q --add-service=https --add-port=8843/tcp 2>/dev/null || true; firewall-cmd -q --permanent --add-service=https --add-port=8843/tcp 2>/dev/null || true
else
  echo "  firewalld no está activo: no se toca (los puertos 443 y 8843 se abren en el firewall de la red del cliente)" | tee -a "$LOG"
fi
curl -sk -m 15 -o /dev/null "$FADMIN/version" || falla "Después de preparar el sistema, la máquina dejó de llegar a FaceAdmin ($FADMIN)"

# 3 · Ansible Automation Platform ----------------------------------------------------------------------------------
H=$(hostname -f 2>/dev/null || hostname)
if [ "$(printf %s "$H" | wc -c)" -gt 60 ] || [ "$H" = localhost ]; then   # el instalador de AAP no acepta nombres de más de 64 caracteres
  diario "hostname|$(hostname)"
  H="faceops-$CODIGO.local"; hostnamectl set-hostname "$H"; grep -q " $H" /etc/hosts || echo "$IPP $H faceops-$CODIGO" >> /etc/hosts
fi
diario "usuario|aap"
id aap >/dev/null 2>&1 || useradd -m -c "Ansible Automation Platform (FaceOps)" aap
echo "aap ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/aap-faceops; chmod 440 /etc/sudoers.d/aap-faceops
loginctl enable-linger aap
diario "envf|"
if [ ! -s "$ENVF" ]; then
  (umask 077; for v in ADMIN PG_ADMIN PG_GW PG_CTL PG_HUB PG_EDA PG_MET; do echo "$v=$(openssl rand -base64 30 | tr -dc A-Za-z0-9 | head -c 22)"; done > "$ENVF")
fi
. "$ENVF"
if curl -sk -m 10 -o /dev/null -w '%{http_code}' https://localhost/api/gateway/v1/ping/ | grep -q 200; then
  paso aap "AAP ya responde en esta máquina: no se reinstala"
else
  B=/home/aap/aap-bundle.tar
  paso bundle "descargando el bundle de AAP desde FaceAdmin (≈4 GB; se puede reanudar)"
  for i in 1 2 3 4 5; do
    if curl -fSk -C - --retry 3 --retry-delay 10 --progress-bar -H "X-FaceAdmin-Token: $LLAVE" -o "$B" "$FADMIN/api/instalacion/bundle" 2> >(tee -a "$LOG" >&2); then
      break
    fi
    echo "  intento $i de 5 falló (detalle arriba); reintento en 15 s" | tee -a "$LOG"
    [ "$i" = 5 ] && falla "No se pudo descargar el bundle de AAP desde FaceAdmin: $(grep -a 'curl:' "$LOG" | tail -1)"; sleep 15
  done
  echo "  ✓ bundle descargado ($(du -h "$B" | cut -f1))" | tee -a "$LOG"
  echo "  desempaquetando"
  tar -C /home/aap -xf "$B"; rm -f "$B"
  BD_=$(ls -d /home/aap/ansible-automation-platform-containerized-setup-bundle-*/ | head -1)
  cat > "$BD_/inventory-faceops" <<INV
[automationgateway]
$H
[automationcontroller]
$H
[automationhub]
$H
[automationeda]
$H
[automationmetrics]
$H
[database]
$H
[all:vars]
ansible_connection=local
postgresql_admin_username=postgres
postgresql_admin_password=$PG_ADMIN
bundle_install=true
bundle_dir='{{ '{{' }} lookup("ansible.builtin.env", "PWD") {{ '}}' }}/bundle'
redis_mode=standalone
gateway_admin_password=$ADMIN
gateway_pg_host=$H
gateway_pg_password=$PG_GW
controller_admin_password=$ADMIN
controller_pg_host=$H
controller_pg_password=$PG_CTL
controller_percent_memory_capacity=0.5
hub_admin_password=$ADMIN
hub_pg_host=$H
hub_pg_password=$PG_HUB
hub_seed_collections=false
eda_admin_password=$ADMIN
eda_pg_host=$H
eda_pg_password=$PG_EDA
automationmetrics_pg_host=$H
automationmetrics_pg_password=$PG_MET
automationmetrics_controller_read_pg_host=$H
automationmetrics_controller_read_pg_password=$PG_MET
INV
  chown -R aap:aap /home/aap; chmod 600 "$BD_/inventory-faceops"
  diario "aap|$BD_"
  paso aap "instalando AAP con sus bases de datos (30 a 45 minutos; avance en $LOG)"
  if ! sudo -iu aap bash -c "cd '$BD_' && ansible-playbook -i inventory-faceops ansible.containerized_installer.install" < /dev/null 2>&1 \
      | tee -a "$LOG" | { grep --line-buffered -E '^PLAY RECAP|fatal:|^TASK \[ansible.containerized_installer\.[a-z_]+ : (Create|Start|Wait|Install)' \
      | sed -u 's/^TASK \[ansible.containerized_installer\./   /; s/\] \*.*$//' || true; }; then
    falla "La instalación de AAP falló"
  fi
  grep -q 'failed=[1-9]' <(tail -40 "$LOG") && falla "La instalación de AAP terminó con errores"
fi
for i in $(seq 1 30); do curl -sk -m 10 -o /dev/null -w '%{http_code}' https://localhost/api/gateway/v1/ping/ | grep -q 200 && break; sleep 10; done
curl -sk -m 10 -o /dev/null -w '%{http_code}' https://localhost/api/gateway/v1/ping/ | grep -q 200 || falla "AAP no responde después de instalar"
echo "  ✓ AAP en línea: https://$PUB/ (usuario admin; clave en $ENVF)"

# 4 · suscripción CCSP de Invoke -----------------------------------------------------------------------------------
if curl -fsk -m 60 -H "X-FaceAdmin-Token: $LLAVE" -o /root/manifiesto-ccsp.zip "$FADMIN/api/instalacion/manifiesto" 2>>"$LOG"; then
  paso suscripcion "aplicando la suscripción CCSP de Invoke"
  python3 - <<PY >>"$LOG" 2>&1 || echo "  ! no se pudo aplicar el manifiesto: cárgalo en AAP › Configuración › Suscripción"
import base64, json, ssl, urllib.request
b = base64.b64encode(open("/root/manifiesto-ccsp.zip", "rb").read()).decode()
req = urllib.request.Request("https://localhost/api/controller/v2/config/", method="POST", data=json.dumps({"manifest": b, "eula_accepted": True}).encode(),
      headers={"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(b"admin:$ADMIN").decode()})
print(urllib.request.urlopen(req, context=ssl._create_unverified_context(), timeout=120).status)
PY
  rm -f /root/manifiesto-ccsp.zip
else
  echo "  ! sin manifiesto CCSP en FaceAdmin: AAP queda sin suscripción hasta que Invoke lo cargue"
fi

# 5 · catálogo de acciones y token para FaceOps --------------------------------------------------------------------
paso catalogo "código de FaceOps (versión ${COMMIT:0:12}) y catálogo de acciones"
diario "repo|"
[ -d "$R/.git" ] || git clone -q "$REPO" "$R" >>"$LOG" 2>&1
git -C "$R" fetch -q origin >>"$LOG" 2>&1 && git -C "$R" checkout -q "$COMMIT"
curl -fsk -m 60 -H "X-FaceAdmin-Token: $LLAVE" -o /root/catalogo-aap.json "$FADMIN/api/instalacion/catalogo" 2>>"$LOG" || echo '{"plantillas": []}' > /root/catalogo-aap.json
AAP_URL=https://localhost AAP_ADMIN_CLAVE="$ADMIN" CATALOGO=/root/catalogo-aap.json FACEOPS_REPO="$REPO" python3 "$R/herramientas/clientes/cargar-catalogo.py" 2>>"$LOG" \
  || echo "  ! el catálogo no se cargó completo (ver $LOG); FaceOps igual queda instalado"
rm -f /root/catalogo-aap.json
if ! grep -q '^FACEOPS_AAP_TOKEN=' "$ENVF"; then
  T=$(curl -sk -u "admin:$ADMIN" -H 'Content-Type: application/json' -d '{"description":"FaceOps","scope":"write"}' https://localhost/api/gateway/v1/tokens/ \
      | python3 -c 'import json,sys;print(json.load(sys.stdin).get("token",""))')
  [ -n "$T" ] || falla "AAP no entregó el token para FaceOps"
  echo "FACEOPS_AAP_TOKEN=$T" >> "$ENVF"
fi
. "$ENVF"

# 6 · FaceOps ------------------------------------------------------------------------------------------------------
paso faceops "construyendo e iniciando FaceOps"
diario "faceops|"
bash "$R/faceops/version.sh" >/dev/null
podman build -q -t localhost/faceops:latest -f "$R/faceops/Containerfile" "$R/faceops" >>"$LOG" 2>&1 || falla "No se pudo construir FaceOps"
install -d -m 700 /etc/faceops; install -d -o 1001 -g 0 -m 770 /var/lib/faceops
umask 077
cat > /etc/faceops.env <<E
FACEOPS_INSTANCIA_ID=$INSTANCIA
FACEOPS_CLIENTE_CODIGO=$CODIGO
FACEOPS_CLIENTE_NOMBRE=$NOMBRE
FACEOPS_INSTANCIA=$CODIGO
FACEOPS_ROL=cliente
FACEOPS_AAP_URL=https://localhost
FACEOPS_AAP_PUBLICA=https://$PUB
FACEOPS_AAP_TOKEN=$FACEOPS_AAP_TOKEN
FACEOPS_ADMIN_URL=$FADMIN
FACEOPS_ADMIN_TOKEN=$LLAVE
FACEOPS_CONSULTOR_USUARIO={{ c.consultor_usuario or '' }}
FACEOPS_CONSULTOR_NOMBRE={{ c.consultor_nombre or '' }}
FACEOPS_CONSULTOR_CORREO={{ c.consultor_correo or '' }}
SCRIPT_NAME=/faceops
E
umask 022
echo "$REPO" > /etc/faceops/repo
[ -s /etc/faceops/tls.crt ] || openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=faceops-$CODIGO" \
  -addext "subjectAltName=DNS:faceops.$PUB.sslip.io,IP:$PUB" -keyout /etc/faceops/tls.key -out /etc/faceops/tls.crt >/dev/null 2>&1
chmod 644 /etc/faceops/tls.crt /etc/faceops/tls.key
cat > /usr/local/sbin/faceops-actualizar <<'A'
#!/bin/bash
# Trae la última versión del repositorio central y, si cambió, reconstruye y reinicia.
R=/opt/faceops-repo; antes=$(git -C "$R" rev-parse HEAD)
git -C "$R" checkout -q main 2>/dev/null; for i in 1 2 3; do git -C "$R" pull -q --ff-only && break; sleep 15; done
if [ "$antes" != "$(git -C "$R" rev-parse HEAD)" ] || ! podman image exists localhost/faceops:latest; then
  bash "$R/faceops/version.sh" >/dev/null; podman build -q -t localhost/faceops:latest -f "$R/faceops/Containerfile" "$R/faceops" >/dev/null && systemctl try-restart faceops
fi
A
chmod 755 /usr/local/sbin/faceops-actualizar
cat > /etc/systemd/system/faceops-actualizar.service <<'U'
[Unit]
Description=FaceOps · actualizar desde el repositorio central al encender
Wants=network-online.target
After=network-online.target
Before=faceops.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/faceops-actualizar
TimeoutStartSec=1200
[Install]
WantedBy=multi-user.target
U
mkdir -p /etc/containers/systemd
cat > /etc/containers/systemd/faceops.container <<'U'
[Unit]
Description=FaceOps
After=network-online.target faceops-actualizar.service
[Container]
Image=localhost/faceops:latest
ContainerName=faceops
Network=host
Volume=/var/lib/faceops:/var/lib/faceops:Z
Volume=/etc/faceops/tls.crt:/etc/faceops/tls.crt:ro,Z
Volume=/etc/faceops/tls.key:/etc/faceops/tls.key:ro,Z
EnvironmentFile=/etc/faceops.env
Environment=TZ=America/Santiago
Exec=gunicorn --workers 1 --threads 8 --bind 0.0.0.0:8843 --certfile /etc/faceops/tls.crt --keyfile /etc/faceops/tls.key --access-logfile - wsgi:app
[Service]
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
U
systemctl daemon-reload; systemctl enable faceops-actualizar >/dev/null 2>&1; systemctl restart faceops
paso verificacion "verificación final: AAP, FaceOps y enlace con FaceAdmin"
curl -sk -m 10 -o /dev/null -w '%{http_code}' https://localhost/api/gateway/v1/ping/ | grep -q 200 || falla "AAP dejó de responder"
for i in $(seq 1 24); do curl -skf https://localhost:8843/faceops/version >/dev/null && break; sleep 10; done
curl -skf https://localhost:8843/faceops/version >/dev/null || falla "FaceOps no respondió (revisa: journalctl -u faceops)"

# 7 · enlace con FaceAdmin -----------------------------------------------------------------------------------------
URL="https://faceops.$PUB.sslip.io:8843/faceops"
curl -sfk -m 20 -X POST -H "X-FaceAdmin-Token: $LLAVE" -H "Content-Type: application/json" \
  -d "{\"instancia_id\": \"$INSTANCIA\", \"url\": \"$URL\", \"host\": \"$(hostname)\", \"commit\": \"$COMMIT\"}" "$FADMIN/api/instancia/registro" >>"$LOG" 2>&1 \
  || falla "FaceAdmin no aceptó el enlace de esta instancia"
ev instalacion ok verificacion "" "$(log_paso)"

# 8 · commit -------------------------------------------------------------------------------------------------------
COMMITEADO=1; trap - EXIT ERR
mv "$DIARIO" "$TX/diario-commit-$(date +%Y%m%d%H%M%S)"
curl -sk -m 10 -X POST -H "X-FaceAdmin-Token: $LLAVE" -H "Content-Type: application/json" \
  -d "{\"fase\": \"commit\", \"estado\": \"ok\", \"host_id\": \"$HID\", \"host\": \"$(hostname)\", \"detalle\": {\"paso\": \"commit\", \"motivo\": \"instalación confirmada\", \"url\": \"$URL/\"}}" \
  "$FADMIN/api/instalacion/evento" >/dev/null 2>&1 || true
echo
echo "══ Paso 3 · commit: instalación confirmada"
echo "   FaceOps:  $URL/"
echo "   AAP:      https://$PUB/   (admin · clave en $ENVF, solo root)"
{% if c.consultor_usuario %}T=$(podman exec faceops python3 -c 'from app import create_app; from app.activacion import nuevo
a = create_app()
with a.app_context(): print(nuevo("{{ c.consultor_usuario }}"))' 2>/dev/null | tail -1 || true)
[ -n "$T" ] && echo "   Consultor {{ c.consultor_nombre }} ({{ c.consultor_usuario }}, cluster-admin): define tu clave aquí (un uso, 72 h): $URL/definir-clave/$T"
{% endif %}echo "   El contrato $CONTRATO sigue en puesta en marcha: el cliente aprueba la activación desde FaceOps o se activa sola al día 30."
}
principal < /dev/null
