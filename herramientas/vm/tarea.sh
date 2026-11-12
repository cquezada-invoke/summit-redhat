sudo podman exec -i faceops python3 - <<'Q' 2>&1 | tail -40
import json
from app import bd
for e in bd.q("SELECT nombre, datos FROM equipos WHERE cliente_id=(SELECT id FROM clientes WHERE lower(codigo)='lab') AND nombre IN ('fw','rtr','ocp-sno')"):
    d = json.loads(e["datos"] or "{}")
    if e["nombre"] == "ocp-sno":
        ns = [x for x in (d.get("ocp") or {}).get("namespaces_app") or [] if "lake" in json.dumps(x)]
        print("ocp ns", json.dumps(ns)[:1500])
        print("ocp cargas", json.dumps((d.get("ocp") or {}).get("cargas"))[:400])
        continue
    r = d.get("red") or {}
    print("==", e["nombre"], "bgp", json.dumps(r.get("bgp"))[:600])
    print("   if0", json.dumps((r.get("interfaces") or [None])[0])[:500], len(r.get("interfaces") or []))
    print("   gestion", json.dumps(r.get("gestion"))[:200], "rutas", json.dumps(r.get("rutas"))[:200])
Q
