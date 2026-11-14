#!/bin/bash
# Claves sensibles de FaceOps fuera del entorno visible del contenedor (podman inspect ya no las muestra en Env).
#  · Si una clave figura en /etc/faceops.env o en ~/faceops/faceops.env, se mueve a un podman secret (root) y se quita de ambos.
#  · Genera /etc/containers/systemd/faceops.container.d/secretos.conf con un Secret= por cada secreto existente:
#    el contenedor la recibe como variable de entorno igual que antes (la app no cambia).
#  · Idempotente; nunca imprime valores. Lo llaman faceops/desplegar.sh y herramientas/kb/instalar.sh.
set -euo pipefail
CLAVES="FACEOPS_SMTP_CLAVE FACEOPS_KB_DSN"
ENVS="/etc/faceops.env $HOME/faceops/faceops.env"
DROP=/etc/containers/systemd/faceops.container.d
nombre() { echo "$1" | tr 'A-Z_' 'a-z-'; }   # FACEOPS_SMTP_CLAVE -> faceops-smtp-clave
for k in $CLAVES; do
  s=$(nombre "$k")
  for f in $ENVS; do
    S=; [ "$f" = /etc/faceops.env ] && S=sudo   # el de $HOME se edita como su dueño (sed -i con sudo le cambiaría el dueño)
    $S test -f "$f" || continue
    if $S grep -q "^$k=" "$f"; then
      # el valor viaja por la tubería, sin pasar por argumentos ni salida
      $S grep -m1 "^$k=" "$f" | cut -d= -f2- | tr -d '\n' | sudo podman secret create --replace "$s" - >/dev/null
      $S sed -i "/^$k=/d" "$f"
    fi
  done
done
sudo mkdir -p $DROP
{
  echo "[Container]"
  for k in $CLAVES; do
    s=$(nombre "$k")
    if sudo podman secret exists "$s"; then echo "Secret=$s,type=env,target=$k"; fi
  done
} | sudo tee $DROP/secretos.conf >/dev/null
sudo chmod 644 $DROP/secretos.conf
sudo systemctl daemon-reload
