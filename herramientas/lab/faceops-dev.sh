#!/bin/bash
# Modo desarrollo de FaceOps en el lab: el contenedor monta ~/faceops-dev/faceops/app (un worktree propio, que el autodeploy NO toca)
# y relee plantillas y estáticos en cada carga (FACEOPS_DEV=1). Editar en el host -> recargar el navegador -> se ve al instante.
#   faceops-dev.sh on     activa (crea el worktree desde origin/main si no existe) y reinicia el contenedor
#   faceops-dev.sh off    vuelve a la imagen desplegada (autodeploy normal)
#   faceops-dev.sh sync   trae origin/main al worktree (si no tiene cambios locales) y reinicia (cambios .py requieren reinicio)
# Flujo: editar en ~/faceops-dev/faceops/app/templates|static -> recargar -> git commit/push desde ~/faceops-dev -> el autodeploy construye la imagen.
# Los .py no se recargan solos: tras editarlos, `faceops-dev.sh restart`.
set -e
DEV=$HOME/faceops-dev; REPO=$HOME/summit-redhat; DROP=/etc/containers/systemd/faceops.container.d
case "${1:-}" in
 on)
  if [ ! -d "$DEV" ]; then git -C "$REPO" fetch -q origin main; git -C "$REPO" worktree add --detach "$DEV" origin/main; fi
  sudo mkdir -p $DROP
  printf '[Container]\nVolume=%s/faceops/app:/opt/faceops/app:z\nEnvironment=FACEOPS_DEV=1\n' "$DEV" | sudo tee $DROP/dev.conf >/dev/null
  sudo systemctl daemon-reload; sudo systemctl restart faceops; echo "modo desarrollo ACTIVO · edita en $DEV/faceops/app" ;;
 off)
  sudo rm -f $DROP/dev.conf; sudo systemctl daemon-reload; sudo systemctl restart faceops; echo "modo desarrollo apagado (imagen desplegada)" ;;
 restart) sudo systemctl restart faceops ;;
 sync)
  git -C "$DEV" fetch -q origin main; git -C "$DEV" merge --ff-only origin/main; sudo systemctl restart faceops ;;
 *) echo "uso: $0 on|off|sync|restart"; exit 1 ;;
esac
