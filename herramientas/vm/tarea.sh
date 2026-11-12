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
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v Warn | tail -120
import re
from app import create_app
app = create_app(); c = app.test_client()
from app.bd import q, q1
cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
u = q1("SELECT * FROM usuarios WHERE usuario='cquezada'")
with c.session_transaction() as s: s["uid"]=u["id"]; s["csrf"]="x"; s["cliente_id"]=cl["id"]
for x in q("SELECT id,nombre,tipo,ubicacion,centro_datos FROM infraestructuras WHERE cliente_id=?", cl["id"]): print("INF", dict(x))
with app.test_request_context("/"):
    from flask import g
    from app.infraestructuras import lista
    from app.flujo_arq import resolver_centros
    eqs = [dict(r) for r in q("SELECT id,nombre,ubicacion,inventario,datos,categoria FROM equipos WHERE cliente_id=?", cl["id"])]
    m = resolver_centros(eqs, lista(cl["id"], eqs))
    from collections import Counter
    print("CENTROS", Counter(m.values()))
    for e in eqs:
        if not (e["ubicacion"] or "").strip() or m[e["id"]] == "Sin centro asignado" or (e["ubicacion"] or "") not in [x["ubicacion"] for x in lista(cl["id"], eqs)]:
            print("EQ", e["id"], e["nombre"], "|", e["ubicacion"], "|", e["inventario"], "|", e["categoria"], "→", m[e["id"]])
for pl in ["servicios,centro:centro-de-datos-santiago,otros,perimetro,isp,red,balanceo,aplicacion,datos,storage,centro:sede-providencia-sala-de-comunicaciones", ""]:
    r = c.get("/infraestructuras/arquitectura?plegar=" + pl)
    h = r.get_data(as_text=True)
    print("GET", r.status_code, len(h), re.findall(r'fl-infra-t"><title>([^<]*)', h))
    print("  sin centro:", re.findall(r'sin centro de datos asignado[^<]{0,300}', h)[:1], "| sin máquinas:", h.count("sin máquinas"), "| Traceback" in h)
Q
