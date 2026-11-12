#!/bin/bash
# Recuperación del lab: liberar disco con lo seguro (sin borrar datos), reanudar VMs pausadas y levantar AAP. Sin secretos.
L() { echo "-- $1: $(df -h / | awk 'NR==2{print $4" libres ("$5")"}')"; }
L inicio
sudo journalctl --vacuum-size=500M >/dev/null 2>&1; L "journal 500M"
sudo find /var/log -xdev -type f \( -name '*.gz' -o -name '*.[0-9]' -o -name '*-20[0-9][0-9][0-9][0-9][0-9][0-9]' \) -delete 2>/dev/null; L "logs rotados /var/log"
sudo find /var/tmp /tmp -xdev -type f -mtime +2 -delete 2>/dev/null; L "tmp > 2 días"
sudo podman image prune -af >/dev/null 2>&1; L "imágenes root sin contenedor"
for u in $(ls /home); do id "$u" >/dev/null 2>&1 || continue; sudo -iu "$u" podman image prune -af >/dev/null 2>&1; done; L "imágenes rootless sin contenedor"
for u in $(ls /home); do [ -d /home/$u/aap ] || [ -d /home/$u/.local/share/containers ] || continue
  sudo find /home/$u -xdev -path '*log*' -type f \( -name '*.gz' -o -name '*.log.[0-9]*' \) -delete 2>/dev/null; done; L "logs rotados AAP"
echo "== mayores consumidores (top)"; sudo du -xh --max-depth=2 / 2>/dev/null | sort -rh | sed -n 2,14p
echo "== archivos > 20G"; sudo find / -xdev -type f -size +20G -printf '%s %TY-%Tm-%Td %p\n' 2>/dev/null | sort -rn | head -12 | awk '{printf "%.0fG %s %s\n",$1/1073741824,$2,$3}'
LIB=$(df --output=avail -BG / | tail -1 | tr -dc 0-9)
echo "== libres: ${LIB}G"
if [ "$LIB" -ge 20 ]; then
  for d in $(sudo virsh list --state-paused --name); do sudo virsh resume "$d" >/dev/null 2>&1 && echo "reanudada $d" || echo "no reanudó $d"; done
  for u in $(ls /home); do id "$u" >/dev/null 2>&1 || continue; uid=$(id -u $u)
    us=$(sudo -iu $u XDG_RUNTIME_DIR=/run/user/$uid systemctl --user list-units 'automation-controller*' --all --no-legend 2>/dev/null | awk '{print $1}')
    [ -z "$us" ] && continue
    echo "AAP de $u: $(echo $us | wc -w) unidades"; sudo -iu $u XDG_RUNTIME_DIR=/run/user/$uid systemctl --user list-units 'automation-*' --all --no-legend 2>/dev/null | awk '{print "  "$1" "$3" "$4}'
  done
else echo "menos de 20G: no se reanuda nada"; fi
echo "== ping AAP"; for i in 1 2 3; do c=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 https://127.0.0.1/api/controller/v2/ping/); echo "controller: $c"; [ "$c" = 200 ] && break; sleep 20; done
echo "== autodeploy"; systemctl list-timers --all 2>/dev/null | grep -i faceops; systemctl status faceops-autodeploy.timer --no-pager 2>/dev/null | head -4
