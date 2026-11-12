set -a; . ~/faceops/faceops.env; set +a
cat > /tmp/redq.py <<'PY'
import json
from app import create_app
from app.bd import q
create_app()
cl = {c["id"]: c["slug"] for c in q("SELECT id, slug FROM clientes")}
for e in q("SELECT * FROM equipos WHERE id=14 OR categoria IN ('router','switch','firewall','balanceador') OR datos LIKE '%\"categoria\"%' ORDER BY cliente_id, id"):
    e = dict(e); d = {}
    try: d = json.loads(e.get("datos") or "{}")
    except Exception: pass
    if e["id"] != 14 and (e.get("categoria") or "") not in ('router','switch','firewall','balanceador') and d.get("categoria") not in ('router','switch','firewall','balanceador','waf','vpn','wifi'):
        continue
    print(cl.get(e["cliente_id"]), e["id"], e["nombre"], "|cat", e.get("categoria"), "|so", e.get("so"), "|ip", e.get("ip"), "|estado", e.get("estado"),
          "|datos", {k: d[k] for k in list(d)[:30] if k in ("categoria","subcategoria","fabricante","modelo","marca","rol","tipo","sistema","so","version","descripcion","plataforma","origen","lab","kind","imagen","os","funcion")},
          "|skills", e.get("skills_auto"), e.get("inventarios"))
cols = [r[1] for r in q("PRAGMA table_info(equipos)")] if True else []
print("COLS", cols)
PY
sudo podman cp /tmp/redq.py faceops:/tmp/redq.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/redq.py 2>&1 | grep -v -i warn | cut -c1-600
