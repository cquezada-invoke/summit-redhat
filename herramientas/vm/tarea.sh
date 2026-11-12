sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v Warn | tail -150
import json
from app import create_app
app = create_app()
from app.bd import q, q1, j
cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
print("cliente", cl["id"], cl["nombre"])
for x in q("SELECT id,nombre,tipo,ubicacion,centro_datos,estado,region,detalle FROM infraestructuras WHERE cliente_id=?", cl["id"]):
    d = j(x["detalle"], {}); print("INF", x["id"], x["nombre"], "|", x["tipo"], "|", x["ubicacion"], "|", x["centro_datos"], "|", x["estado"], "|", x["region"], "|", {k: d.get(k) for k in ("proyecto","zona","anfitrion","hipervisor","cluster")})
for e in q("SELECT id,nombre,ubicacion,categoria,forma,proveedor,inventario,energia,datos,estado FROM equipos WHERE cliente_id=? ORDER BY ubicacion,nombre", cl["id"]):
    d = j(e["datos"], {})
    print("EQ", e["id"], e["nombre"], "|", e["ubicacion"], "|", e["categoria"], "|", e["forma"], "|", e["proveedor"], "|", e["inventario"], "|", e["estado"], "|", {k: d.get(k) for k in d if k in ("hipervisor","anfitrion","infra","sitio","ubicacion","zona","centro","host","nube","red_libvirt")})
try:
    for r in q("SELECT infra_id, ts, substr(datos,1,0) FROM nube_resumen WHERE cliente_id=?", cl["id"]): print("RES", r["infra_id"], r["ts"])
    from app.nube_consumo import muestra
    print("MUESTRA", muestra(cl["id"], 2))
except Exception as ex: print("ERR", ex)
Q
