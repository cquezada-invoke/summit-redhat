#!/bin/bash
cat > /tmp/e1s27.py <<'PY'
import re, html
from app import create_app
app = create_app()
from app.bd import q1
c = app.test_client(); u = q1("SELECT id FROM usuarios WHERE usuario='qa.nucleo'")["id"]
with c.session_transaction() as s: s["uid"] = u; s["csrf"] = "x"; s["cliente_id"] = 1
b = c.get("/lab/inventario/6").get_data(as_text=True); m = b[b.find("<main"):]
for x in re.finditer(r'<(div|section|details|dialog)\b([^>]*\bid="([^"]+)"[^>]*)>', m):
    seg = m[x.end():x.end()+1500]; t = re.search(r'<(h2|h3|summary|b)[^>]*>(.*?)</\1>', seg, re.S)
    print(x.group(1), x.group(3), "|", re.sub(r"<[^>]+>|\s+", " ", html.unescape(t.group(2))).strip()[:60] if t else "")
PY
sudo podman cp /tmp/e1s27.py faceops:/tmp/e1s27.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/e1s27.py 2>&1 | grep -v -i warn | head -150
