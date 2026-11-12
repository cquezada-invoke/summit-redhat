sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v -i warn | tail -60
import json
from collections import Counter
from app import create_app
app = create_app()
from app.bd import q, q1, ej, j
cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
infs = q("SELECT * FROM infraestructuras WHERE cliente_id=?", cl["id"])
centros = {x["centro_datos"] for x in infs}
eqs = q("SELECT id,nombre,ubicacion,categoria FROM equipos WHERE cliente_id=? AND lower(COALESCE(categoria,'')) NOT LIKE 'nodo%'", cl["id"])
fuera = [e for e in eqs if (e["ubicacion"] or "") not in centros]
print("ANTES total", len(eqs), "fuera de centro (columna propia o sin ubicación):", len(fuera))
print("POR UBICACION", Counter(e["ubicacion"] or "(vacía)" for e in fuera))
e = q1("SELECT id,ubicacion,inventario FROM equipos WHERE cliente_id=? AND nombre='anfitrion-kvm'", cl["id"])
print("anfitrion-kvm", dict(e) if e else None)
if e and not (e["ubicacion"] or "").strip():
    ej("UPDATE equipos SET ubicacion='Centro de datos Santiago' WHERE id=?", e["id"])
    c_ = j(cl["clasificacion"], {}) or {}
    c_.setdefault("_equipos", {}).setdefault("anfitrion-kvm", {})["ubicacion"] = "Centro de datos Santiago"
    ej("UPDATE clientes SET clasificacion=? WHERE id=?", json.dumps(c_, ensure_ascii=False), cl["id"])
    print("CORREGIDO anfitrion-kvm → Centro de datos Santiago (equipo y clasificación)")
print("despues", dict(q1("SELECT ubicacion FROM equipos WHERE cliente_id=? AND nombre='anfitrion-kvm'", cl["id"])))
Q
