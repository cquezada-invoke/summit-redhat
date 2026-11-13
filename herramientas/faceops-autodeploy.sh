#!/bin/bash
# FaceOps · despliegue consolidado en el anfitrión (lo ejecuta faceops-autodeploy.timer cada 10 min; ver dep-fo.sh para esperar).
# Si origin/main cambió respecto de lo desplegado: pull --ff-only, configurar.py (solo si cambió faceops/aap),
# version.sh, podman build y restart de faceops; espera a que /ingresar responda 200.
# Si el build falla NO reinicia: FaceOps sigue con la imagen anterior y el error queda en el log y en el estado.
# Estado en ~/faceops-autodeploy/: desplegado (SHA en producción), estado (una línea), autodeploy.log.
# Los agentes ya no construyen: hacen push a main y esperan con dep-fo.sh a que "desplegado" contenga su commit.
set -u
REPO=${FACEOPS_REPO:-$HOME/summit-redhat}
D=$HOME/faceops-autodeploy; mkdir -p "$D"
LOG=$D/autodeploy.log; EST=$D/estado; DEP=$D/desplegado
exec 9>"$D/lock"; flock -n 9 || { echo "otro despliegue en curso"; exit 0; }
log() { echo "$(date -Is) $*" | tee -a "$LOG"; }
estado() { echo "$(date -Is) $*" > "$EST"; log "ESTADO $*"; }
# El log no crece sin límite
[ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 5000 ] && { tail -2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; }

cd "$REPO" || { estado "error: no existe $REPO"; exit 1; }
for i in 1 2 3; do git fetch -q origin main 2>>"$LOG" && break; sleep 10; done
NUEVO=$(git rev-parse origin/main) || { estado "error: fetch"; exit 1; }
ANT=$(cat "$DEP" 2>/dev/null || true)
# Con commits locales rebasados, lo desplegado es un descendiente de origin/main: no se vuelve a desplegar cada 10 min
if [ "${1:-}" != "--forzar" ] && { [ "$NUEVO" = "$ANT" ] || { [ -n "$ANT" ] && git merge-base --is-ancestor "$NUEVO" "$ANT" 2>/dev/null; }; }; then exit 0; fi
log "== despliegue ${ANT:0:8} -> ${NUEVO:0:8}"

# pull --ff-only (con reintento: puede haber otro git trabajando en el mismo árbol)
# Si el anfitrión tiene commits locales (no puede hacer push), se rebasan sobre origin/main en vez de frenar el despliegue.
ok=0; for i in 1 2 3 4 5; do git pull -q --ff-only origin main >>"$LOG" 2>&1 && { ok=1; break; }
  if git -c user.name='FaceOps autodeploy' -c user.email=cquezada@invoke.cl pull -q --rebase --autostash origin main >>"$LOG" 2>&1; then ok=1; log "commits locales rebasados sobre origin/main"; break; fi
  git rebase --abort >/dev/null 2>&1; sleep 10; done
[ $ok = 1 ] || { estado "error: git pull --ff-only falló en $(git rev-parse --short HEAD) (¿cambios locales o historia divergente?) · objetivo ${NUEVO:0:8}"; exit 1; }
HEADSHA=$(git rev-parse HEAD)

# Catálogo de AAP: dep-fo.sh corría configurar.py antes del build; aquí solo si cambió faceops/aap (o primera vez)
if [ -z "$ANT" ] || ! git cat-file -e "$ANT^{commit}" 2>/dev/null || [ -n "$(git diff --name-only "$ANT" "$HEADSHA" -- faceops/aap)" ]; then
  log "configurar.py (cambió faceops/aap)"
  timeout 900 python3 faceops/aap/configurar.py > "$D/configurar.log" 2>&1; rc=$?
  log "configurar.py código $rc"; grep -E "Traceback|^ERROR" "$D/configurar.log" | grep -v -i -E "clave|password|token|secret" | head -5 | tee -a "$LOG"
  CONF="configurar=$rc"
else CONF="configurar=sin cambios"; fi

bash faceops/version.sh >/dev/null 2>>"$LOG"
if ! sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops > "$D/build.log" 2>&1; then
  tail -20 "$D/build.log" >> "$LOG"
  estado "error: build falló en ${HEADSHA:0:8}; FaceOps sigue con ${ANT:0:8} (ver $D/build.log) · $CONF"
  exit 1
fi
sudo systemctl restart faceops

# Espera a que FaceOps responda (hasta ~5 min)
URLS=${FACEOPS_URL:-"http://127.0.0.1:8110/ingresar http://127.0.0.1:8080/ingresar https://127.0.0.1/faceops/ingresar"}
code=000
for i in $(seq 1 30); do
  sleep 10
  for u in $URLS; do code=$(curl -sk -o /dev/null -m 10 -w '%{http_code}' "$u"); [ "$code" = 200 ] && break 2; done
done
if [ "$code" = 200 ]; then
  echo "$HEADSHA" > "$DEP"
  estado "ok: desplegado ${HEADSHA:0:8} · /ingresar 200 · $CONF"
else
  estado "error: ${HEADSHA:0:8} construido y reiniciado pero /ingresar no responde 200 (último $code) · $CONF"
  exit 1
fi
