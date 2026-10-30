#!/bin/bash
# Sube al registro de paquetes (repositorio/rpm) los RPM que necesitan los equipos sin repositorios Red Hat para sumarse a
# FaceOps (por ahora git-core, para el respaldo continuo). Se descargan desde los repositorios del servidor de la plataforma.
set -u
T=$(cat ~/faceops/respaldo-token); D=$(mktemp -d)
sudo dnf -q download --destdir "$D" git-core git-core-doc perl-Git perl-Error 2>/dev/null || sudo dnf -q download --destdir "$D" git-core
for f in "$D"/*.rpm; do
  n=$(basename "$f")
  c=$(sudo curl -s -o /dev/null -w '%{http_code}' -X PUT -H "Authorization: token $T" -T "$f" http://127.0.0.1:3000/api/packages/repositorio/rpm/upload)
  echo "$n → $c"
done
sudo rm -rf "$D"
