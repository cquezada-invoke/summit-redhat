cd ~/summit-redhat; git log -1 --format=%h; sudo podman inspect faceops --format "{{.State.StartedAt}} {{.ImageName}}"; sudo podman exec faceops cat /opt/app-root/src/version.txt 2>/dev/null | head -2; sudo podman exec faceops grep -c "def reglas_monitoreo" /opt/app-root/src/app/alertas_objeto.py 2>/dev/null || sudo podman exec faceops sh -c "grep -rc \"def reglas_monitoreo\" /app/app/alertas_objeto.py /srv/app/alertas_objeto.py 2>/dev/null"
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
