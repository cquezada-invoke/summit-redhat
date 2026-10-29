#!/bin/bash
# Firewall VyOS: cierra las sesiones de configuración que quedaron abiertas (procesos unionfs-fuse huérfanos).
# Cada "configure" que no termina limpio deja un proceso de 3 hilos; el contenedor tiene un límite de ~300 procesos
# y, al llenarse, cualquier cambio en el firewall falla con "fork: Resource temporarily unavailable".
# Solo cierra sesiones de más de 10 minutos (no toca una que esté en curso). Lo llaman reiniciar-lab.sh y reiniciar-seguridad.sh.
P=$(sudo podman inspect clab-summit-red-fw --format '{{.State.Pid}}' 2>/dev/null) || exit 0
CG=/sys/fs/cgroup$(cut -d: -f3 /proc/$P/cgroup | head -1)
CG=${CG%/init.scope}; CG=${CG%/container}   # con systemd dentro del contenedor, el cgroup del proceso 1 es .../container/init.scope
n=0
for p in $(find $CG -name cgroup.procs -exec cat {} + 2>/dev/null); do
  [ "$(cat /proc/$p/comm 2>/dev/null)" = unionfs-fuse ] || continue
  [ $(( $(date +%s) - $(stat -c %Y /proc/$p) )) -gt 600 ] || continue
  sudo nsenter -t $P -m umount -l "$(tr '\0' '\n' </proc/$p/cmdline | tail -1)" 2>/dev/null
  sudo kill $p 2>/dev/null && n=$((n+1))
done
[ $n -gt 0 ] && echo "firewall: $n sesiones de configuración huérfanas cerradas ($(cat $CG/pids.current)/$(cat $CG/pids.max) procesos)"
exit 0
