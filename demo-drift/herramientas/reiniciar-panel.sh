#!/usr/bin/env bash
# Deja la Demo 1 en su estado inicial: solo nodo-so bajo gestión (vigilar y corregir, visitantes con sudo).
# Los demás servidores se retiran con AAP (se quitan cuentas y accesos de visitantes).
# Uso: bash reiniciar-panel.sh [--limpiar-historial]
set -euo pipefail
PANEL=http://127.0.0.1:8088
curl -sf -X POST $PANEL/api/gestion/reiniciar >/dev/null
echo -n "Reiniciando la gestión con AAP"
for _ in $(seq 1 90); do
  sleep 2; echo -n "."
  E=$(curl -sf $PANEL/api/gestion | python3 -c 'import sys,json;g=json.load(sys.stdin)["gestion"];print(" ".join(f"{h}:{v.get(\"estado\")}" for h,v in g.items()))')
  case "$E" in *incorporando*|*retirando*) ;; *) echo; echo "Estado: $E"; break ;; esac
done
if [ "${1:-}" = "--limpiar-historial" ]; then
  python3 -c "import sqlite3,os;c=sqlite3.connect(os.path.expanduser('~/demo-drift/panel/panel.db'));c.execute('DELETE FROM incidentes');c.commit()"
  echo "Historial de cambios limpio."
fi
