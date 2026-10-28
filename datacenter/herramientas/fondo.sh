#!/bin/bash
# Lanza un comando largo como unidad de systemd (sobrevive al cierre de la sesión o del runner).
# Uso: fondo.sh NOMBRE comando...   → log en ~/datacenter/logs/NOMBRE.log ; estado: systemctl status dc-NOMBRE
N=$1; shift
mkdir -p "$HOME/datacenter/logs"
sudo systemctl reset-failed "dc-$N" 2>/dev/null || true
sudo systemd-run --quiet --collect --unit="dc-$N" --uid="$(id -u)" --gid="$(id -g)" \
  --setenv=HOME="$HOME" --setenv=PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" \
  --working-directory="$HOME/summit-redhat" \
  bash -c "$* > $HOME/datacenter/logs/$N.log 2>&1"
echo "lanzado dc-$N (log: ~/datacenter/logs/$N.log)"
