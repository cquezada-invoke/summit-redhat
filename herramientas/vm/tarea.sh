#!/bin/bash
cat > /tmp/e1s26.py <<'PY'
import re, html
from app import create_app
app = create_app()
from app.bd import q1
c = app.test_client(); u = q1("SELECT id FROM usuarios WHERE usuario='qa.nucleo'")["id"]
with c.session_transaction() as s: s["uid"] = u; s["csrf"] = "x"; s["cliente_id"] = 1
for ruta in ("/lab/inventario/6", "/lab/inventario/12"):
    b = c.get(ruta).get_data(as_text=True); m = b[b.find("<main"):]
    print("=====", ruta, len(m))
    vistos = set()
    for tag, at, txt in re.findall(r'<(a|button|summary|select)\b([^>]*)>(.{0,60}?)<', m, re.S):
        h = re.search(r'(href|action|data-url|formaction|data-accion|data-a|onclick|data-hab|data-sk)="([^"]+)"', at)
        cl = re.search(r'class="([^"]+)"', at); i = re.search(r'id="([^"]+)"', at)
        k = (tag, h.group(2)[:60] if h else "", cl.group(1)[:30] if cl else "", i.group(1) if i else "", re.sub(r"\s+", " ", html.unescape(txt)).strip()[:30])
        if k in vistos: continue
        vistos.add(k); print(*k, sep=" | ")
PY
sudo podman cp /tmp/e1s26.py faceops:/tmp/e1s26.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/e1s26.py 2>&1 | grep -v -i warn | grep -v -i "token\|clave" | head -260
