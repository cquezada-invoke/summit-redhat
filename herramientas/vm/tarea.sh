cd ~/summit-redhat; for i in $(seq 1 90); do sudo podman exec faceops grep -q recursos_p /opt/faceops/app/ficha_plataforma.py 2>/dev/null && break; sleep 10; done; echo desplegado
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v Warn | tail -40
import re
from app import create_app
app = create_app()
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    for pid in ["PL-DATA-LAKE-0027", "PL-DATA-LAKE-0029", "PL-KAFKA-0008", "PL-KEYCLOAK-0006", "PL-JBOSS-EAP-0006", "PL-REDIS-0010", "PL-POSTGRESQL-0009",
                "PL-ACTIVEMQ-0010", "PL-NGINX-0008", "PL-VAULT-0026", "PL-SATELLITE-0077", "PL-RED-HAT-IDM-0003", "PL-389-DIRECTORY-SERVER-0002",
                "PL-ACTIVE-DIRECTORY-0001", "PL-IIS-0012", "PL-VYOS-FIREWALL-0014", "PL-FRROUTING-0016", "PL-OPENSHIFT-0029", "PL-KEYCLOAK-0003"]:
        r = c.get(f"/lab/plataformas/{pid}")
        if r.status_code >= 500: print(r.get_data(as_text=True)[-700:])
        h = r.get_data(as_text=True)
        m = re.search(r'slb-tot"><small>Salud</small><b>([^<]*)</b>', h)
        sk = re.search(r'title="Skills · (\d+) disponibles', h)
        nsk = h.count('class="hb-it')
        al = re.search(r'id="alertas[^"]*"[^>]*title="([^"]*)"', h)
        print(pid, r.status_code, "salud", m.group(1) if m else "-", "· skills", nsk, "ok", sk.group(1) if sk else "-", "· campana", "sí" if 'class="al-btn' in h else "no",
              "· remediables", h.count('class="sl-fila"'), "· err" if "Traceback" in h else "", r.headers.get("Location", "")[:60])
    import urllib.request
    try:
        t = urllib.request.urlopen("http://192.168.122.1:8110/metricas/plataformas", timeout=10).read().decode()
        print("metricas", len(t.splitlines()), "líneas;", sum(1 for l in t.splitlines() if l.startswith("faceops_plataforma_indicador")), "indicadores")
    except Exception as x:
        print("metricas error", x)
Q
sudo journalctl -u faceops --since "-3 min" --no-pager 2>/dev/null | grep -iE "Traceback|Error" | tail -5
