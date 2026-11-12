cd ~/summit-redhat && git pull -q --ff-only origin main; git log --oneline -1 | cut -c1-60
python3 faceops/aap/configurar.py 2>&1 | grep -E "OpenShift · (Crear|Eliminar) en GCP|Traceback|Error" | head -6
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 15
for i in $(seq 1 20); do sudo podman exec faceops true 2>/dev/null && break; sleep 6; done; sleep 10
cat > /tmp/ocg-ui.py <<'Q'
import re, json
from app import create_app
app = create_app()
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    h = c.get("/lab/contenedores").get_data(as_text=True)
    print("UI botón:", "Crear plataforma de contenedores" in h, "| sin 'Crear nueva máquina virtual':", "Crear nueva máquina virtual" not in h, "| diálogo:", 'id="dlg-crear-plat"' in h, "| conectar existente:", "Conectar existente" in h)
    r = c.get("/lab/contenedores/crear/opciones"); j = r.get_json() or {}
    for o in j.get("opciones", []): print("OPCION", o["nombre"], "|", o["version"], o["canal"], "| habilitada:", o["habilitada"], "|", o["motivo"])
Q
sudo podman cp /tmp/ocg-ui.py faceops:/tmp/ocg-ui.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/ocg-ui.py 2>&1 | grep -E "UI|OPCION|Error|Traceback" | head -8
cat > /tmp/ocg-val.py <<'Q'
# Casos de validación de «OpenShift · Crear en GCP» y «OpenShift · Eliminar en GCP» (no crean recursos). Corre en el contenedor de FaceOps.
# Imprime solo número de ticket, estado y el motivo (sin secretos).
import json, os, sys, time
from app import create_app
from app.bd import q1, j
a = create_app()
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 LIMIT 1")
from app.operacion import sincronizar_catalogo, solicitar, resolver_aprobacion
sincronizar_catalogo(cl)
A = {n: q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], n) for n in ("OpenShift · Crear en GCP", "OpenShift · Eliminar en GCP")}
print("acciones:", {k: (v or {}).get("id") for k, v in A.items()})
base = {"ocg_proyecto": "master-ia-320218", "ocg_region": "southamerica-west1", "ocg_dominio": os.environ.get("DOM") or "sin-zona-faceops.invoke.cl",
        "ocg_topologia": "sno", "ocg_vence_horas": 2, "ocg_horario": "siempre"}
casos = [("crear", "C1", dict(base, ocg_cluster="val-ver", ocg_version="4.99.0"), "no existe"),
         ("crear", "C2", dict(base, ocg_cluster="val-dns", ocg_version="ultima-4", ocg_dominio="sin-zona-faceops.invoke.cl"), "Zona DNS"),
         ("crear", "C3", dict(base, ocg_cluster="Mal_Nombre", ocg_version="ultima-4"), "Datos inválidos"),
         ("crear", "C4", dict(base, ocg_cluster="val-rama", ocg_version="ultima-6"), "sin versión estable"),
         ("eliminar", "C4", {"ocg_cluster": "Mal_Nombre", "ocg_modo": "eliminar"}, "Indica el clúster"),
         ("eliminar", "C2", {"ocg_cluster": "noexiste", "ocg_modo": "eliminar", "ocg_proyecto": "master-ia-320218"}, "Nada que eliminar"),
         ("eliminar", "C3", {"ocg_modo": "ciclo", "ocg_cluster": "", "ocg_proyecto": "master-ia-320218"}, "")]
solo = set(sys.argv[1:])
ids = {}
with a.test_request_context("/"):
    from flask import g
    g.usuario, g.cliente, g.perfil = u, cl, {"modo": "escritura", "segmentos": ["*"]}
    for acc, c_, p, _ in casos:
        if solo and f"{acc}:{c_}" not in solo:
            continue
        au = A["OpenShift · Crear en GCP" if acc == "crear" else "OpenShift · Eliminar en GCP"]
        t = solicitar(u, cl, au, None, p, f"Prueba {acc} {c_}", prueba=True)
        ids[(acc, c_)] = t["id"]
for k in range(120):
    for t in ids.values():
        if q1("SELECT estado FROM tickets WHERE id=?", t)["estado"] == "pendiente_aprobacion":
            try: resolver_aprobacion(u, t, True, "Aprobado por Claude (regla de Cristian)")
            except Exception as x: print("aprobar:", str(x)[:120])
    ts = {k_: q1("SELECT numero, estado, resultado FROM tickets WHERE id=?", t) for k_, t in ids.items()}
    if all(t["estado"] in ("exitoso", "fallido", "cancelado", "rechazado") for t in ts.values()): break
    time.sleep(6)
espera = {(acc, c_): e for acc, c_, _, e in casos}
for k_, t in ts.items():
    r = (t["resultado"] or "").replace("\n", " | ")
    ok = (t["estado"] == "fallido" and espera[k_] in r) if k_ in (("crear", "C1"), ("crear", "C2"), ("crear", "C3"), ("crear", "C4"), ("eliminar", "C4")) \
        else (t["estado"] == "exitoso" and espera[k_] in r)
    print("CASO", k_[0], k_[1], t["numero"], t["estado"], "OK" if ok else "NO", "·", r[-260:])
Q
sudo podman cp /tmp/ocg-val.py faceops:/tmp/ocg-val.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/ocg-val.py 2>&1 | grep -E "acciones|CASO|aprobar|Error|Traceback" | cut -c1-420 | head -20
sudo podman exec faceops rm -f /tmp/ocg-val.py /tmp/ocg-ui.py; rm -f /tmp/ocg-val.py /tmp/ocg-ui.py
