echo "inicio espera: $(date -Is)"
# dep-fo.sh · ya NO construye: el despliegue lo hace faceops-autodeploy.timer en el anfitrión (cada 10 min).
# Espera (máx. 20 min) a que el SHA desplegado (~/faceops-autodeploy/desplegado) contenga el commit objetivo y reporta el estado.
R=~/summit-redhat; D=~/faceops-autodeploy
# Commit objetivo: el padre del commit de la tarea (origin/main al encolar); fuera del runner, origin/main actual
if git log -1 --format=%s 2>/dev/null | grep -q "^VM"; then OBJ=$(git cat-file -p HEAD | sed -n 's/^parent //p' | head -1); fi
[ -n "${OBJ:-}" ] || { git -C $R fetch -q origin main; OBJ=$(git -C $R rev-parse origin/main); }
echo "objetivo: ${OBJ:0:8}"
[ -f $D/estado ] || echo "AVISO: no hay estado del timer (¿faceops-autodeploy.timer instalado?)"
for i in $(seq 1 120); do
  DEP=$(cat $D/desplegado 2>/dev/null)
  if [ -n "$DEP" ]; then
    git -C $R cat-file -e "$OBJ^{commit}" 2>/dev/null || git -C $R fetch -q origin main
    git -C $R merge-base --is-ancestor "$OBJ" "$DEP" 2>/dev/null && { echo "DESPLEGADO ${DEP:0:8} (incluye ${OBJ:0:8}) tras $((i*10-10)) s"; break; }
  fi
  if grep -q "error: build" $D/estado 2>/dev/null && git -C $R merge-base --is-ancestor "$OBJ" "$(git -C $R rev-parse HEAD)" 2>/dev/null; then
    echo "BUILD FALLÓ con ${OBJ:0:8} incluido: FaceOps sigue con la imagen anterior (ver ~/faceops-autodeploy/build.log)"; break; fi
  [ $i = 120 ] && echo "TIMEOUT 20 min: desplegado ${DEP:0:8}, objetivo ${OBJ:0:8}"
  sleep 10
done
echo "estado timer: $(cat $D/estado 2>/dev/null)"
git -C $R log -1 --format='%h %s' "$(cat $D/desplegado 2>/dev/null || echo HEAD)" | cut -c1-80
echo "fin espera: $(date -Is) · /faceops/ingresar -> $(curl -sk -o /dev/null -m 10 -w %{http_code} https://127.0.0.1/faceops/ingresar)"; grep -E "ESTADO|== despliegue" ~/faceops-autodeploy/autodeploy.log | tail -3
