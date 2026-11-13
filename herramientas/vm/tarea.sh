#!/bin/bash
# A21 · plan «Sistema · Leer log» (visor de logs de la ficha). Tickets reales (prueba=True). Sin secretos en la salida.
SHA=a680a5f2; for i in $(seq 1 150); do D=$(cat ~/faceops-autodeploy/desplegado 2>/dev/null); [ -n "$D" ] && git -C ~/summit-redhat merge-base --is-ancestor $SHA $D 2>/dev/null && break; sleep 10; done
echo "desplegado: $(cut -c1-8 ~/faceops-autodeploy/desplegado) · $(cut -c1-140 ~/faceops-autodeploy/estado)"
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="Sistema · Leer log"; M=srv-e1prueba-01; W=srv-windows-iis-01
fopy() { sudo podman exec -i -e FACEOPS_VIGIA=0 -w /opt/faceops -e PYTHONPATH=/opt/faceops faceops python3 - "$@" 2>/dev/null; }
# el catálogo debe tener la acción (sincronización con AAP)
for i in $(seq 1 30); do R=$(fopy <<'Q' | tail -1
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1
    from app.operacion import sincronizar_catalogo
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
    if not q1("SELECT 1 x FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Sistema · Leer log'", cl["id"]):
        try: sincronizar_catalogo(cl)
        except Exception as x: print("sync", type(x).__name__, str(x)[:100])
    print("SI" if q1("SELECT 1 x FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Sistema · Leer log'", cl["id"]) else "NO")
Q
); [ "$R" = SI ] && break; sleep 20; done; echo "catálogo: $R"
FIS=$(fopy <<'Q' | tail -1
from app import create_app
create_app()
from app.bd import q
import json
for e in q("SELECT nombre, datos, so FROM equipos WHERE forma='fisico' AND estado NOT IN ('retirado') AND ip LIKE '192.168.122.%' ORDER BY (id=97) DESC, id"):
    d = json.loads(e["datos"] or "{}")
    if "windows" not in (e["so"] or "").lower() and (d.get("logs") or d.get("servicios_det") or e["so"]):
        print(e["nombre"]); break
else: print("")
Q
); echo "física: ${FIS:-ninguna con logs}"
lg() { caso "{\"auto\":\"$N\",\"equipo\":\"$1\",\"params\":{\"lg_ruta\":\"$2\",\"lg_lineas\":\"${3:-300}\",\"lg_saltar\":\"${4:-0}\",\"lg_buscar\":\"${5:-}\"},\"min\":8,\"directo\":true,\"caso\":\"$6\"}" > /dev/null
  echo "== $6 $1 $2: $(estado) $(ticket) · $(jq_ "(lambda L: f\"{len(L.get('lineas') or [])} líneas · total {L.get('total')} · error «{L.get('error','')}»\")(s.get('faceops_log') or {})")"; }
nl() { jq_ "len((s.get('faceops_log') or {}).get('lineas') or [])"; }
tot() { jq_ "(s.get('faceops_log') or {}).get('total')"; }
L1=/var/log/messages
lg $M $L1 300 0 "" C1; N1=$(nl); T1=$(tot); P1=$(jq_ "((s.get('faceops_log') or {}).get('lineas') or [''])[0][:80]")
OK=falla; [ "$(estado)" = exitoso ] && [ "${N1:-0}" -ge 1 ] && [ "${N1:-0}" -le 300 ] && [ "${T1:-0}" -ge "${N1:-0}" ] && OK=ok
anotar C1 "RHEL virtual: /var/log/messages" $OK "$(ticket): $N1 líneas de $T1, sin aprobación"
lg $M journal 100 0 "" C2; N2=$(nl); F2=$(jq_ "sum(1 for l in ((s.get('faceops_log') or {}).get('lineas') or []) if l[:4].isdigit())")
OK=falla; [ "$(estado)" = exitoso ] && [ "${N2:-0}" -ge 1 ] && [ "${F2:-0}" -ge 1 ] && OK=ok
anotar C2 "journal del sistema" $OK "$(ticket): $N2 líneas del journal ($F2 con fecha ISO)"
lg $M $L1 300 0 systemd C3; N3=$(nl); T3=$(tot); MAL=$(jq_ "sum(1 for l in ((s.get('faceops_log') or {}).get('lineas') or []) if 'systemd' not in l.lower())")
OK=falla; [ "$(estado)" = exitoso ] && [ "${N3:-0}" -ge 1 ] && [ "$MAL" = 0 ] && OK=ok
anotar C3 "Búsqueda" $OK "$(ticket): $N3 líneas, todas con «systemd» ($T3 coincidencias)"
if [ "${T1:-0}" -gt 300 ]; then lg $M $L1 300 300 "" C4; N4=$(nl); P4=$(jq_ "((s.get('faceops_log') or {}).get('lineas') or [''])[-1][:80]")
  OK=falla; [ "$(estado)" = exitoso ] && [ "${N4:-0}" -ge 1 ] && OK=ok
  anotar C4 "Carga progresiva" $OK "$(ticket): $N4 líneas anteriores (saltar 300)"
else lg $M journal 50 50 "" C4; N4=$(nl); OK=falla; [ "$(estado)" = exitoso ] && [ "${N4:-0}" -ge 1 ] && OK=ok; anotar C4 "Carga progresiva" $OK "$(ticket): $N4 líneas anteriores del journal (saltar 50)"; fi
lg $W evento:System 100 0 "" C5; N5=$(nl)
OK=falla; [ "$(estado)" = exitoso ] && [ "${N5:-0}" -ge 1 ] && OK=ok
anotar C5 "Windows: visor de eventos System" $OK "$(ticket): $N5 eventos · $(jq_ "((s.get('faceops_log') or {}).get('lineas') or [''])[-1][:70]")"
if [ -n "$FIS" ]; then lg $FIS journal 100 0 "" C6; N6=$(nl); OK=falla; [ "$(estado)" = exitoso ] && [ "${N6:-0}" -ge 1 ] && OK=ok
  anotar C6 "Máquina física" $OK "$(ticket): $FIS · $N6 líneas"
else anotar C6 "Máquina física" falla "no hay máquina física Linux con logs en el inventario"; fi
lg $M /var/log/no-existe-a21.log 50 0 "" C7; ER=$(echo "$RES" | python3 -c 'import json,sys;r=json.load(sys.stdin);print(((r.get("stats") or {}).get("faceops_log") or {}).get("error") or r.get("detalle","")[:200])')
TK7=$(ticket); VIS=$(fopy "$TK7" <<'Q' | tail -1
import sys, json
from app import create_app
app = create_app()
from app.bd import q1
t = q1("SELECT * FROM tickets WHERE numero=?", sys.argv[1]); e = q1("SELECT id FROM equipos WHERE nombre=? AND estado!='retirado'", t["limite"])
u = q1("SELECT id FROM usuarios WHERE usuario='qa.nucleo'")["id"]; c = app.test_client()
with c.session_transaction() as s: s["uid"] = u; s["csrf"] = "x"; s["cliente_id"] = t["cliente_id"]
r = c.get(f"/lab/inventario/{e['id']}/log/{t['id']}"); print("VISOR", r.status_code, json.dumps(r.get_json(), ensure_ascii=False)[:200])
Q
); echo "  $VIS"
OK=falla; [ "$(estado)" = fallido ] && echo "$ER $VIS" | grep -qi "no existe" && OK=ok
anotar C7 "Archivo que ya no existe" $OK "$TK7 fallido: «$(echo $ER | cut -c1-90)»; el visor recibe el error"
V8=$(fopy <<'Q' | tail -1
from app import create_app
app = create_app()
from app.bd import q1
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); e = q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre='srv-e1prueba-01' AND estado!='retirado'", cl["id"])
u = q1("SELECT id FROM usuarios WHERE usuario='qa.nucleo'")["id"]; c = app.test_client()
with c.session_transaction() as s: s["uid"] = u; s["csrf"] = "x"; s["cliente_id"] = cl["id"]
n0 = q1("SELECT max(id) m FROM tickets")["m"]
r = c.post(f"/lab/inventario/{e['id']}/log", data={"_csrf": "x", "ruta": "/etc/shadow"})
print("R8", r.status_code, (r.get_json() or {}).get("error", "")[:80], "tickets nuevos", q1("SELECT count(*) n FROM tickets WHERE id>?", n0)["n"])
Q
); echo "  $V8"
OK=falla; echo "$V8" | grep -q "R8 400 .*tickets nuevos 0" && OK=ok
anotar C8 "Ruta fuera del inventario" $OK "${V8#R8 }"
echo "CASOS_LOG $CASOS"
certificar "$N" "planes-prueba.yml · $N"
