#!/bin/bash
# Escribe app/version.json desde el repositorio central (GitHub): línea V1 + número de compilación (commits) + commit y fecha.
D=$(cd "$(dirname "$0")" && pwd)
cd "$D/.." || exit 0
printf '{"linea": "%s", "build": %s, "commit": "%s", "fecha": "%s", "rama": "%s"}\n' \
  "$(sed -n 's/^VERSION = "\([0-9]*\.[0-9]*\).*/\1/p' "$D/app/__init__.py")" "$(git rev-list --count HEAD)" "$(git rev-parse HEAD)" \
  "$(TZ=UTC git log -1 --format=%cd --date=format-local:'%Y-%m-%d %H:%M')" "$(git rev-parse --abbrev-ref HEAD | sed s/^HEAD$/main/)" > "$D/app/version.json"
cat "$D/app/version.json"
# Cableado de los laboratorios de red (containerlab) para el diagrama de Redes › Arquitectura (no se versiona)
python3 demo4-redes/herramientas/exportar-topologia-faceops.py 2>/dev/null || true
