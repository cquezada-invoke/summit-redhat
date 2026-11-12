#!/bin/bash
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>&1 <<'Q' | grep -E "^(A|E|EL|OK)" | grep -v -i token
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1, q, j
    from app.operacion import motivo_no_aplica
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); u = q1("SELECT * FROM usuarios WHERE id=1")
    for n in ("Plataforma · Medir salud", "Plataforma · Programar medición"):
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], n)
        print("A", n, "inv=", a["inventario"], "por_inv=", a["por_inventario"], "usa_limite=", a["usa_limite"], "visible=", a["visible"])
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], "Plataforma · Medir salud")
    for n in ("nodo-storage", "nodo-jboss", "nodo-tomcat", "nodo-nginx"):
        e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cl["id"], n)
        print("E", n, e and e["inventarios"], e and e["estado"], motivo_no_aplica(u, cl["id"], dict(a), dict(e)) if e else None)
Q
cd ~/summit-redhat && git log -3 --format='%h %s' -- faceops/app/operacion.py | cut -c1-120; cat ~/faceops-autodeploy/estado
