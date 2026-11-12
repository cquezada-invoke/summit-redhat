sudo podman inspect faceops --format '{{.Config.Cmd}} {{.Config.Entrypoint}}' | cut -c1-300
sudo podman exec faceops sh -c 'env | grep -iE "SCRIPT_NAME|GUNICORN|PREFIJO|RAIZ" ; ps -eo args | grep -m2 gunicorn' | cut -c1-300
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/metricas/plataformas
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/faceops/metricas/plataformas
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v Warn | tail -30
import re
from app import create_app
app = create_app()
print([r.rule for r in app.url_map.iter_rules() if "metricas" in r.rule or "plataformas/<pid>" in r.rule])
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    for pid in ["PL-DATA-LAKE-0027", "PL-KAFKA-0008", "PL-OPENSHIFT-0029", "PL-VYOS-FIREWALL-0014", "PL-DATA-LAKE-0029"]:
        r = c.get(f"/lab/plataformas/{pid}")
        h = r.get_data(as_text=True)
        m = re.search(r'slb-tot"><small>Salud</small><b>([^<]*)</b>', h)
        print(pid, r.status_code, "salud", m.group(1) if m else "-", "· skills", h.count('class="hb-it'), "· campana", "sí" if 'class="al-btn' in h else "no",
              "· remediables", h.count('class="sl-fila"'), r.headers.get("Location", "")[:60])
        if r.status_code >= 500: print(h[-3000:])
    r = c.get("/metricas/plataformas", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    print("metricas tc", r.status_code, r.get_data(as_text=True)[:600])
Q
