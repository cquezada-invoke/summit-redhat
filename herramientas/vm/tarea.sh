cat > /tmp/redver.py <<'PY'
import re, html as H
from app import create_app
app = create_app()
PRE = "/lab"
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    for u0 in ("/faceops/lab/inventario/14", "/lab/inventario/14", "/faceops/lab/inventario"):
        r0 = c.get(u0); print("PRUEBA", u0, r0.status_code, r0.headers.get("Location"))
    for eid in (14, 16, 121, 122, 123, 124, 125, 17, 27):
        r = c.get(PRE + f"/inventario/{eid}"); h = r.get_data(as_text=True)
        i = h.find('id="hb-dlg"'); k = h.find("</dialog>", i); d = h[i:k]
        filas = re.split(r'<tr class="hb-it', d)[1:]
        print(f"== {eid} HTTP {r.status_code} dlg={i>0} skills={len(filas)} habilitadas={sum(1 for f in filas if not f.startswith(' bloq'))} btn={'hb-btn' in h}")
        for f in filas:
            n = re.search(r'<b title="([^"]+)"', f); am = re.search(r'class="hb-amb">([^<]*)<', f); m = re.search(r'Bloqueada: ([^<"]*)', f)
            print("   ", "OFF" if f.startswith(" bloq") else "ON ", H.unescape(n.group(1) if n else "?"), "|", am.group(1) if am else "", "|", H.unescape(m.group(1))[:75] if m else "")
PY
sudo podman cp /tmp/redver.py faceops:/tmp/redver.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/redver.py 2>&1 | grep -v -i warn | cut -c1-200
