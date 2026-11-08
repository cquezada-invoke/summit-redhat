#!/bin/bash
# Escribe version.json de FaceAdmin desde el repositorio central.
D=$(cd "$(dirname "$0")" && pwd); cd "$D/.." || exit 0
printf '{"linea": "1.0", "build": %s, "commit": "%s", "fecha": "%s"}\n' "$(git rev-list --count HEAD -- faceadmin)" "$(git rev-parse HEAD)" \
  "$(git log -1 --format=%cd --date=format:'%Y-%m-%d %H:%M')" > "$D/version.json"
