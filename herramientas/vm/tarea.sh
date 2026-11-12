cd ~/summit-redhat && git pull -q --ff-only origin main; git log -1 --format=%h
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 25
timeout 900 python3 faceops/aap/configurar.py 2>&1 | grep -i -E "Traceback|error|Grafana de OpenShift" | grep -v -i -E "clave|password|token" | tail -3
source herramientas/pruebas/comun.sh >/dev/null 2>&1; sincronizar
echo "== diagnóstico ocp-sno en el contenedor"
sudo podman exec faceops getent hosts thanos-querier-openshift-monitoring.apps.sno.invoke.lab || echo "contenedor sin DNS para thanos"
sudo podman exec -i faceops python3 - <<'Q' 2>&1 | grep -E "^(OCPDIAG|Traceback|\w+Error)"
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1
    from app import alertas_objeto as ao
    from app.boveda import leer
    e = q1("SELECT * FROM equipos WHERE id=29")
    cr = leer("maquinas/ocp-sno/grafana-monitoreo") or {}
    print("OCPDIAG bóveda:", sorted(cr.keys()), "| url:", cr.get("url"))
    import requests
    try:
        r = requests.get(cr["url"] + "/api/v1/rules", params={"type": "alert"}, headers={"Authorization": "Bearer " + cr["token"]}, timeout=6, verify=False)
        print("OCPDIAG thanos rules:", r.status_code, len([g for g in r.json()["data"]["groups"] if g["name"] == "faceops-ocp-sno"]))
    except Exception as x:
        print("OCPDIAG thanos error:", type(x).__name__, str(x)[:200])
    m = ao.reglas_monitoreo(e, "maquina", "openshift")
    print("OCPDIAG reglas_monitoreo:", None if m is None else len(m))
Q
E2E=$(mktemp --suffix=.py)
cat > $E2E <<'PYEOF'
import json, re, sys, time, urllib.request, urllib.parse
from app import create_app
app = create_app()
from app.bd import q, q1
from app.operacion import actualizar_tickets, resolver_aprobacion
cl = q1("SELECT * FROM clientes WHERE lower(codigo)='lab'") or q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
u = q1("SELECT * FROM usuarios WHERE id=1")
e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre='nodo-jboss' AND estado!='retirado'", cl["id"])
c = app.test_client()
with c.session_transaction() as s:
    s["uid"] = 1; s["cliente_id"] = cl["id"]; s["csrf"] = "e2e"
with app.test_request_context():
    from flask import url_for
    U_EQ = url_for("consola.equipo", eid=e["id"]); U_AL = url_for("consola.equipo_alertas", eid=e["id"]); U_PR = url_for("consola.equipo_alerta_probar", eid=e["id"])
def prom_rules(g_):
    d = json.load(urllib.request.urlopen("http://127.0.0.1:9090/api/v1/rules?type=alert", timeout=10))["data"]["groups"]
    g = [x for x in d if x["name"] == g_]
    return sorted({(r["labels"].get("faceops_alerta"), r["labels"].get("faceops_accion")) for r in (g[0]["rules"] if g else [])})
def flot(sx=""):
    h = c.get(U_EQ, headers={"Accept": "text/html"}, follow_redirects=True).get_data(as_text=True)
    d = h[h.find(f'id="al-dlg{sx}"'):h.find("</dialog>", h.find(f'id="al-dlg{sx}"'))]
    b = re.search(r'<button type="button" id="alertas' + re.escape(sx) + r'"[^>]*class="([^"]+)"[\s\S]*?</button>', h)
    n = re.search(r'class="al-n">(\d+)<', b.group(0)) if b else None
    return d, (b.group(1) if b else ""), (n.group(1) if n else "0")
def ticket(form, paso, ambito="maquina"):
    antes = q1("SELECT MAX(id) m FROM tickets")["m"] or 0
    r = c.post(U_AL, data=dict(form, _csrf="e2e", ambito=ambito), headers={"X-Requested-With": "fetch"})
    js = r.get_json() or {}
    print(paso, "→", r.status_code, {k: js.get(k) for k in ("numero", "estado", "error")}, "| tickets nuevos:", len(q("SELECT id FROM tickets WHERE id>?", antes)), flush=True)
    if not js.get("numero"):
        return None
    t = q1("SELECT * FROM tickets WHERE numero=?", js["numero"]); fin, ap = time.time() + 900, set()
    while time.time() < fin:
        time.sleep(8)
        try: actualizar_tickets()
        except Exception: pass
        x = q1("SELECT * FROM tickets WHERE id=?", t["id"]); k = (x.get("aap_aprobacion_id"), x.get("aprobado_en"))
        if x["estado"] == "pendiente_aprobacion" and k not in ap:
            ap.add(k); resolver_aprobacion(u, t["id"], True, "Aprobado por Claude (regla de Cristian)")
        if x["estado"] in ("exitoso", "fallido", "rechazado", "vencido", "cancelado", "error"):
            break
    print(paso, "ticket", t["numero"], x["estado"], flush=True); time.sleep(20)
    return x
MAQ = {"on_D01": "1", "acc_D01": "avisar", "sev_D01": "critico", "para_D01": "2",
       "on_C01": "1", "acc_C01": "avisar", "aviso_C01": "80", "critico_C01": "95", "para_C01": "2",
       "on_C02": "1", "acc_C02": "avisar", "aviso_C02": "85", "critico_C02": "95", "para_C02": "10",
       "on_C03": "1", "acc_C03": "avisar", "aviso_C03": "80", "critico_C03": "90", "para_C03": "5",
       "on_S01": "1", "acc_S01": "avisar", "aviso_S01": "5", "critico_S01": "20", "para_S01": "0",
       "on_B05": "1", "acc_B05": "avisar", "sev_B05": "aviso", "para_B05": "2"}
JB = {"on_J01": "1", "acc_J01": "diagnosticar", "sev_J01": "critico", "para_J01": "1"}
ticket(MAQ, "ACTIVAR máquina"); ticket(JB, "ACTIVAR JBoss", "plataforma:jboss")
print("PROM máquina", prom_rules("faceops-nodo-jboss"), flush=True)
print("PROM jboss", prom_rules("faceops-nodo-jboss--jboss"), flush=True)
for aid, amb in (("J01", "plataforma:jboss"),):
    r = c.post(U_PR, data={"_csrf": "e2e", "aid": aid, "ambito": amb}, headers={"X-Requested-With": "fetch"}); js = r.get_json() or {}
    print("PROBAR", aid, r.status_code, js.get("error") or "", flush=True)
    if js.get("url"):
        for i in range(90):
            time.sleep(10); p_ = c.get(js["url"], headers={"X-Requested-With": "fetch"}).get_json()
            if p_["estado"] != "en curso": break
        print("PROBAR", aid, p_["estado"], "|", " → ".join(p_["pasos"]), flush=True)
PYEOF
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 -u - < $E2E 2>&1 | grep -E "^(ACTIVAR|PROM|PROBAR|Traceback|  File|\w+Error)"; rm -f $E2E
echo "== FaceOps alcanzable desde el clúster"
export KUBECONFIG=$HOME/openshift/sno/instalar/auth/kubeconfig; OC=$HOME/openshift/sno/bin/oc
$OC -n openshift-monitoring exec alertmanager-main-0 -c alertmanager -- sh -c 'wget -q -O- --post-data="{}" --header="Content-Type: application/json" http://192.168.122.1:8110/api/alertas/ocp/ocp-sno 2>&1 | head -c 200; echo' 2>&1 | tail -2
echo "== plan Grafana de OpenShift (C1–C6)"
bash herramientas/pruebas/p-grafana-ocp.sh 2>&1 | grep -E "^(CASO|CERT|FIN|EVIDENCIA)"
$OC -n openshift-monitoring get secret alertmanager-main -o jsonpath='{.data.alertmanager\.yaml}' | base64 -d | grep -c "faceops-ocp-"
sudo podman exec -i faceops python3 - <<'Q' 2>&1 | grep -E "^(TABLA|Traceback|  File|\w+Error)"
import re
from app import create_app
app = create_app()
from app.bd import q1
cl = q1("SELECT * FROM clientes WHERE lower(codigo)='lab'") or q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
c = app.test_client()
with c.session_transaction() as s:
    s["uid"] = 1; s["cliente_id"] = cl["id"]
with app.test_request_context():
    from flask import url_for
    u = url_for("consola.alertas")
r = c.get(u, headers={"Accept": "text/html"}, follow_redirects=True); h = r.get_data(as_text=True)
print("TABLA", r.status_code, "| filas:", h.count('data-estado="'), "| encendidas:", h.count('data-estado="encendida'), "| disparando:", h.count(' disparando"'), "| objetos:", sorted(set(re.findall(r'class="mono">([^<]+)</a>', h)))[:12])
print("TABLA contadores:", re.findall(r'<div class="n"[^>]*>(\d+)</div><div class="l">([^<]+)', h))
Q
