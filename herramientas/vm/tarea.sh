sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v -i warn | tail -30
import json
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q, q1, j
    for n in ("OpenShift · Recolectar clúster", "OpenShift · Operador"):
        for r in q("SELECT nombre_aap, plan, casos, tickets, fecha, resultado FROM pruebas_automatizacion WHERE nombre_aap=? ORDER BY id DESC LIMIT 1", n):
            print("CERT", r["nombre_aap"], r["fecha"], r["resultado"], r["plan"][:200], r["tickets"][:200]); print("  CASOS", r["casos"][:900])
    a = q1("SELECT * FROM automatizaciones WHERE nombre_aap='OpenShift · Operador'")
    print("OPAUTO", {k: a[k] for k in ("id","nombre","tipo","usa_limite","requiere_aprobacion","aap_tipo","inventario","visible","categoria")})
    print("TICKS", [dict(x) for x in q("SELECT numero, estado, limite, creado FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE a.nombre_aap='OpenShift · Recolectar clúster' ORDER BY t.id DESC LIMIT 3")])
    from app.aap import AAP
    aap = AAP.de_cliente(q1("SELECT * FROM clientes ORDER BY id LIMIT 1"))
    for s in aap.todos("schedules/", name__icontains="ecolect"): print("SCHED", s["name"], s["rrule"][:80])
    for ee in aap.todos("execution_environments/"): print("EE", ee["id"], ee["name"], ee["image"])
Q
for img in $(sudo podman images --format '{{.Repository}}:{{.Tag}}' | grep -i -E "ee|execution|creator" | head -5); do echo "IMG $img: $(sudo podman run --rm --entrypoint python3 $img -c 'import kubernetes;print(kubernetes.__version__)' 2>&1 | tail -1) $(sudo podman run --rm --entrypoint ansible-galaxy $img collection list kubernetes.core 2>&1 | grep kubernetes | tail -1)"; done
grep -rn "Recolectar clúster" ~/summit-redhat/herramientas/pruebas/*.sh | head -3
