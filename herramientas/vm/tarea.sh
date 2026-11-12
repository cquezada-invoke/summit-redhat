set -a; . ~/faceops/faceops.env; set +a
cat > /tmp/dbg2.py <<'PY'
from app import create_app
from app.bd import q, q1
a = create_app()
with a.test_request_context("/"):
    from flask import g
    u = q1("SELECT * FROM usuarios WHERE usuario='cquezada' AND es_admin=1"); cli = q1("SELECT * FROM clientes WHERE codigo='lab'")
    g.usuario, g.cliente = u, cli
    from app.operacion import motivo_no_aplica
    au = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Red · Respaldar configuración'", cli["id"])
    for n in ("rtr-wan-01", "sw-core-01", "lb-sede-01", "suc1", "fw"):
        e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cli["id"], n)
        print("MOTIVO", n, e["estado"], motivo_no_aplica(u, cli["id"], au, e))
    for t in q("SELECT numero, estado, aap_job_id, limite, resultado FROM tickets WHERE numero IN ('FO-LAB-02056','FO-LAB-02057','FO-LAB-02058','FO-LAB-02060','FO-LAB-02065')"):
        print("TK", t["numero"], t["estado"], t["aap_job_id"], t["limite"], (t["resultado"] or "")[:300].replace("\n", " "))
PY
sudo podman cp /tmp/dbg2.py faceops:/tmp/dbg2.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/dbg2.py 2>&1 | grep -v -i warn | cut -c1-400
python3 - <<'PY'
import os, ssl, urllib.request, json, re
cx = ssl._create_unverified_context(); B = os.environ["FACEOPS_AAP_URL"].rstrip("/") + "/api/controller/v2/"
H = {"Authorization": "Bearer " + os.environ["FACEOPS_AAP_TOKEN"]}
def get(u): return urllib.request.urlopen(urllib.request.Request(B + u, headers=H), context=cx).read().decode(errors="replace")
r = json.loads(get("unified_jobs/?name__startswith=Red%20%C2%B7%20&order_by=-id&page_size=12"))
for j in r["results"]:
    print("JOB", j["id"], j["type"], j["name"], j["status"], j.get("limit"))
    if j["type"] == "job" and j["status"] == "failed":
        t = get(f"jobs/{j['id']}/stdout/?format=txt")
        for l in t.splitlines():
            if re.search(r"fatal|FAILED|msg|ERROR", l) and not re.search(r"(?i)password|token|secret|\$6\$", l):
                print("    ", l.strip()[:300])
PY
