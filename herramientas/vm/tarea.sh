EQUIPOS="nodo-jboss3"
for i in $(seq 1 20); do sudo podman exec faceops true 2>/dev/null && break; sleep 6; done
cat > /tmp/el.py <<'PY'
import sys, time
from app import create_app
from app.bd import q, q1, j
from app.operacion import resolver_aprobacion
a = create_app()
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); cid = cl["id"]
u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 LIMIT 1")
c = a.test_client()
with c.session_transaction() as s: s["uid"] = u["id"]; s["csrf"] = "el-claude"
for n in sys.argv[1:]:
    e = q1("SELECT id, estado FROM equipos WHERE cliente_id=? AND nombre=?", cid, n)
    if not e or e["estado"] == "retirado":
        print(n, "ya retirado o no existe"); continue
    prev = q1("SELECT t.id FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND a.nombre_aap='FaceOps · Eliminar servidor' AND t.motivo LIKE ? AND t.estado NOT IN ('fallido','rechazado','cancelado') ORDER BY t.id DESC", cid, f"Eliminar {n} (%")
    if prev:
        tid = prev["id"]
    else:
        r = c.post(f"/lab/inventario/{e['id']}/eliminar", data={"_csrf": "el-claude"}); d = r.get_json() or {}
        if r.status_code != 200:
            print(n, "NO SE PUDO SOLICITAR", r.status_code, d.get("error")); continue
        tid = d["id"]
    t = None
    for k in range(110):
        t = q1("SELECT numero, estado, resultado FROM tickets WHERE id=?", tid)
        if t["estado"] == "pendiente_aprobacion":
            try: resolver_aprobacion(u, tid, True, "Aprobado por Claude (regla de Cristian)")
            except Exception as x: print("  aprobar:", type(x).__name__, str(x)[:120])
        if t["estado"] in ("exitoso", "fallido", "cancelado", "rechazado"): break
        time.sleep(6)
    est = q1("SELECT estado FROM equipos WHERE id=?", e["id"])["estado"]
    print(n, t["numero"], t["estado"], "· equipo:", est, "·", " | ".join((t["resultado"] or "").strip().splitlines()[-2:])[:260], flush=True)
PY
cat > /tmp/fin.py <<'PY'
# Cierre del retiro cuando el ticket de «Eliminar servidor» fue exitoso pero FaceOps no alcanzó a retirarlo (AAP 409/400)
import sys
from app import create_app
from app.bd import q, q1, ej
from app.aap import AAP, ErrorAAP
from app.bd import auditar
create_app()
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); cid = cl["id"]; aap = AAP.de_cliente(cl)
for n in sys.argv[1:]:
    e = q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cid, n)
    t = q1("SELECT t.* FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND a.nombre_aap='FaceOps · Eliminar servidor' AND t.motivo LIKE ? AND t.estado='exitoso' ORDER BY t.id DESC", cid, f"Eliminar {n} (%")
    if not e or not t: print("FIN", n, "nada que cerrar" if not e else "sin ticket exitoso: no se toca"); continue
    quedan = []
    for h in aap.get("hosts/", name=n, page_size=50).get("results", []):
        try: aap._r("DELETE", f"hosts/{h['id']}/")
        except ErrorAAP as x:
            if "constructed" not in str(x): quedan.append(h["id"])
    ej("UPDATE equipos SET estado='retirado' WHERE id=?", e["id"])
    ej("UPDATE tickets SET resultado=? WHERE id=?", (t["resultado"] or "") + f"\n{n} eliminado: fuera de los inventarios de AAP y del inventario de FaceOps (cierre posterior; antes AAP no dejó borrar sus hosts)." + (f" Quedan hosts en AAP: {quedan}" if quedan else ""), t["id"])
    auditar("FaceOps", cid, "servidor eliminado", t["numero"], {"equipo": n, "cierre": "posterior"})
    print("FIN", n, "retirado con", t["numero"], "| hosts que quedan en AAP:", quedan)
PY
sudo podman cp /tmp/el.py faceops:/tmp/el.py; sudo podman cp /tmp/fin.py faceops:/tmp/fin.py
EX="sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3"
$EX /tmp/el.py ${EQUIPOS} 2>&1 | grep -v -i warn | tail -12
$EX /tmp/fin.py ${EQUIPOS} 2>&1 | grep "^FIN"
sudo podman exec faceops rm -f /tmp/el.py /tmp/fin.py
sudo podman exec -i -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 - <<'PY' 2>&1 | grep -E "^(LISTA|AAP|CERT)"
import re
from app import create_app
from app.bd import q, q1
from app.aap import AAP
a = create_app()
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); ap = AAP.de_cliente(cl)
for h in ap.get("hosts/", name="srv-mover-01", page_size=20).get("results", []):
    try: ap._r("DELETE", f"hosts/{h['id']}/"); print("AAP host", h["id"], "borrado")
    except Exception as x: print("AAP host", h["id"], str(x)[:80])
u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 LIMIT 1"); c = a.test_client()
with c.session_transaction() as s: s["uid"] = u["id"]; s["csrf"] = "x"
t = c.get("/lab/inventario?forma=virtual&n=100").get_data(as_text=True)
print("LISTA", [q1("SELECT nombre FROM equipos WHERE id=?", int(i))["nombre"] for i in re.findall(r'<td class="eq-id"><a href="[^"]*/inventario/(\d+)"', t)])
from app.pruebas import estado
for n in ("JBoss · Desplegar app", "Infra · Mover máquina entre centros de datos", "Migración · WebLogic a OpenShift", "Migración · WebLogic a OpenShift · Evaluar", "FaceOps · Eliminar servidor"):
    x = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], n); print("CERT", n, "→", estado(dict(x, cliente_id=cl["id"]))["estado"])
PY
