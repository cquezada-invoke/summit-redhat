#!/bin/bash
# Diagnóstico de disco lleno del lab (solo lectura): qué ocupa los 500 GB. Sin secretos.
df -h / | tail -1
sudo du -xh --max-depth=1 / 2>/dev/null | sort -rh | head -12
echo "== /var"; sudo du -xh --max-depth=2 /var 2>/dev/null | sort -rh | head -15
echo "== /home"; sudo du -xh --max-depth=2 /home 2>/dev/null | sort -rh | head -12
echo "== libvirt images (top)"; sudo ls -laSh /var/lib/libvirt/images 2>/dev/null | head -15
echo "== podman"; sudo podman system df 2>/dev/null
echo "== journal"; sudo journalctl --disk-usage 2>/dev/null
echo "== archivos > 5G recientes (24h)"; sudo find / -xdev -type f -size +5G -mmin -1440 -printf '%TY-%Tm-%Td %TH:%TM %s %p\n' 2>/dev/null | sort | tail -15
echo "== archivos > 20G"; sudo find / -xdev -type f -size +20G -printf '%s %p\n' 2>/dev/null | sort -rn | head -15
echo "== VMs pausadas: motivo"; for d in $(sudo virsh list --state-paused --name); do echo "$d: $(sudo virsh domstate --reason $d)"; done
echo "== AAP"; sudo podman ps --format '{{.Names}} {{.Status}}' | grep -iE "automation|controller|gateway|receptor|postgres|redis|eda|hub" | head -20
