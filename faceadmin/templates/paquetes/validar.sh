#!/bin/bash
# =====================================================================================================================
#  FaceOps · PASO 1 de 3 · VALIDACIÓN · {{ c.nombre }} · contrato {{ k.numero }} · {{ generado }}
#  Solo revisa: no cambia nada en esta máquina. Si todo se cumple, sigue solo con el paso 2 (instalación) y el paso 3 (commit o rollback).
#  Uso:  curl -fsSL <enlace de FaceAdmin> | sudo bash
# =====================================================================================================================
# con «curl | bash» el script llega por la entrada estándar: se lee entero dentro de principal() y se ejecuta sin esa entrada
principal() {
set -uo pipefail
ADMIN="{{ admin }}"; LLAVE="{{ tok }}"; ENLACE="{{ enlace }}"; INSTANCIA="{{ c.instancia_id }}"
OK=(); FALLA=(); AVISO=(); NOK=0; NFA=0; NAV=0
SALIDA=""
bien() { OK+=("$1"); NOK=$((NOK + 1)); echo "  ✓ $1"; SALIDA+="✓ $1"$'\n'; }
mal() { FALLA+=("$1"); NFA=$((NFA + 1)); echo "  ✗ $1"; SALIDA+="✗ $1"$'\n'; }
ojo() { AVISO+=("$1"); NAV=$((NAV + 1)); echo "  ! $1"; SALIDA+="! $1"$'\n'; }
[ "$(id -u)" = 0 ] || { echo "✗ Ejecuta con sudo:  curl -fsSL <enlace> | sudo bash"; exit 1; }
HID=$(printf '%s|%s' "$(cat /etc/machine-id 2>/dev/null)" "$INSTANCIA" | sha256sum | cut -c1-32); HOST=$(hostname)
echo "══ FaceOps · paso 1 de 3: validación · {{ c.nombre }} · contrato {{ k.numero }}"

echo "· contrato"
V=$(curl -sk -m 20 -H "X-FaceAdmin-Token: $LLAVE" "$ADMIN/api/instalacion/validar" 2>/dev/null)
R=$(echo "$V" | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: print("x|FaceAdmin no respondió"); sys.exit()
if not d.get("ok"): print("x|"+(d.get("motivo") or "FaceAdmin rechazó la instalación")); sys.exit()
a=d.get("archivos") or {}
print("ok|contrato "+d["contrato"]+" vigente (puesta en marcha hasta "+(d.get("puesta_en_marcha_hasta") or "—")+")|"+("1" if a.get("bundle") else "0")+("1" if a.get("manifiesto") else "0")+("1" if a.get("catalogo") else "0"))' 2>/dev/null)
case "$R" in ok\|*) bien "$(echo "$R" | cut -d'|' -f2)"; AR=$(echo "$R" | cut -d'|' -f3)
  [ "${AR:0:1}" = 1 ] && bien "FaceAdmin tiene el bundle de AAP" || mal "FaceAdmin aún no tiene el bundle de AAP (avisa a Invoke)"
  [ "${AR:1:1}" = 1 ] || ojo "sin manifiesto de suscripción CCSP: AAP quedará sin suscripción hasta cargarlo"
  [ "${AR:2:1}" = 1 ] || ojo "FaceAdmin no tiene el catálogo de acciones: FaceOps queda sin acciones hasta cargarlo";;
  *) mal "$(echo "$R" | cut -d'|' -f2-) (¿la máquina llega a $ADMIN por HTTPS?)";; esac

echo "· sistema y recursos"
if grep -qE 'release 9' /etc/redhat-release 2>/dev/null; then bien "$(cat /etc/redhat-release)"; else mal "se necesita RHEL 9 (hay: $(cat /etc/redhat-release 2>/dev/null || echo desconocido))"; fi
CPU=$(nproc); RAM=$(awk '/MemTotal/{printf "%d", $2/1024/1024}' /proc/meminfo)
[ "$CPU" -ge {{ minimo.cpu }} ] && bien "$CPU vCPU" || mal "$CPU vCPU (mínimo {{ minimo.cpu }})"
[ "$RAM" -ge {{ minimo.ram_gb }} ] && bien "$RAM GB de memoria" || mal "$RAM GB de memoria (mínimo 16)"
LIBRE=$(df -BG --output=avail / | tail -1 | tr -dc 0-9); RAIZ=$(findmnt -no SOURCE /); PK=$(lsblk -no PKNAME "$RAIZ" 2>/dev/null | head -1)
DISCO=$([ -n "$PK" ] && lsblk -bdno SIZE "/dev/$PK" 2>/dev/null | awk '{printf "%d", $1/1024/1024/1024}' || echo 0)
USADO=$(df -BG --output=used / | tail -1 | tr -dc 0-9)
if [ "$LIBRE" -ge {{ minimo.disco_gb }} ]; then bien "$LIBRE GB libres en /"
elif [ "$((DISCO - USADO))" -ge {{ minimo.disco_gb }} ]; then bien "disco de $DISCO GB: la partición raíz se extenderá al instalar ($LIBRE GB libres hoy)"
else mal "$LIBRE GB libres en / y disco de $DISCO GB (mínimo {{ minimo.disco_gb }} GB libres)"; fi
[ "$(getenforce 2>/dev/null)" = Enforcing ] && bien "SELinux en modo enforcing" || ojo "SELinux no está en enforcing"

echo "· repositorios y red"
if timeout 180 dnf -q makecache >/dev/null 2>&1 && dnf -q repolist --enabled 2>/dev/null | grep -qiE 'baseos'; then bien "repositorios de Red Hat (BaseOS y AppStream) disponibles"
else mal "dnf no llega a los repositorios de Red Hat: registra la máquina (subscription-manager) o habilita sus repositorios"; fi
timeout 30 git ls-remote -q https://github.com/cquezada-invoke/summit-redhat.git HEAD >/dev/null 2>&1 || timeout 20 curl -sf -o /dev/null https://github.com \
  && bien "salida a GitHub" || mal "sin salida HTTPS a github.com"
curl -sfk -m 20 -r 0-0 -o /dev/null -H "X-FaceAdmin-Token: $LLAVE" "$ADMIN/api/instalacion/bundle" && bien "descarga desde FaceAdmin" || mal "no se puede descargar desde FaceAdmin"
for p in 443 8843; do ss -ltnH "sport = :$p" | grep -q . && mal "el puerto $p está ocupado" || bien "puerto $p libre"; done

echo "· instalaciones previas"
if [ -e /etc/faceops.env ] || systemctl list-unit-files 2>/dev/null | grep -q '^faceops'; then mal "ya hay un FaceOps instalado en esta máquina"; else bien "sin FaceOps previo"; fi
if id aap >/dev/null 2>&1 || podman ps -a --format '{{ '{{' }}.Names{{ '}}' }}' 2>/dev/null | grep -q automation-gateway; then mal "ya hay un AAP o un usuario «aap» en esta máquina"; else bien "sin AAP previo"; fi

ESTADO=$([ "$NFA" -eq 0 ] && echo ok || echo error)
DET=$(python3 -c 'import json,sys; a=sys.argv; print(json.dumps({"fallas": json.loads(a[1]), "avisos": json.loads(a[2]), "motivo": "requisitos cumplidos" if a[3]=="ok" else "requisitos no cumplidos", "log": a[4]}, ensure_ascii=False))' \
  "$(printf '%s\n' "${FALLA[@]:-}" | python3 -c 'import json,sys;print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')" \
  "$(printf '%s\n' "${AVISO[@]:-}" | python3 -c 'import json,sys;print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')" "$ESTADO" "$SALIDA")
curl -sk -m 20 -X POST -H "X-FaceAdmin-Token: $LLAVE" -H "Content-Type: application/json" \
  -d "{\"fase\": \"validacion\", \"estado\": \"$ESTADO\", \"host_id\": \"$HID\", \"host\": \"$HOST\", \"detalle\": $DET}" "$ADMIN/api/instalacion/evento" >/dev/null 2>&1
echo
if [ "$ESTADO" = ok ]; then
  echo "══ Paso 1 · validación correcta ($NOK puntos, $NAV aviso(s)). Sigue solo con el paso 2."
  echo "   (si se corta tu conexión, la instalación sigue: retoma la salida con  sudo tail -f /var/log/faceops-instalacion.salida)"
  echo
  sleep 3
  # paso 2 (instalación) y paso 3 (commit o rollback): se descargan y ejecutan solos, en esta misma sesión
  curl -fsSLk "$ADMIN/instalar/$ENLACE/instalar?h=$HID" -o /root/faceops-paso2.sh || { echo "✗ No se pudo descargar el paso 2 desde FaceAdmin"; exit 1; }
  chmod 700 /root/faceops-paso2.sh
  # corre desacoplado de esta sesión: si se corta la conexión SSH la instalación sigue (y su avance se ve en FaceAdmin)
  SAL=/var/log/faceops-instalacion.salida; : > "$SAL"; chmod 600 "$SAL"
  setsid bash -c 'bash /root/faceops-paso2.sh; echo $? > /root/.faceops-paso2.rc; rm -f /root/faceops-paso2.sh' > "$SAL" 2>&1 < /dev/null &
  PID=$!
  tail -n +1 --pid="$PID" -f "$SAL"
  RC=$(cat /root/.faceops-paso2.rc 2>/dev/null || echo 1); rm -f /root/.faceops-paso2.rc
  exit "$RC"
else
  echo "══ Paso 1 · la máquina no cumple $NFA requisito(s). Corrígelos y vuelve a correr el mismo comando."
  exit 1
fi

}
principal < /dev/null
