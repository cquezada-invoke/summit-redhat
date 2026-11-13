#!/bin/bash
echo "desplegado: $(cut -c1-8 ~/faceops-autodeploy/desplegado) · $(cut -c1-120 ~/faceops-autodeploy/estado)"
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>/dev/null <<'PY'
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q, q1
    from app.pruebas import estado
    import yaml
    acc = yaml.safe_load(open("datos/etapas.yml"))["etapas"][1]["acciones"]
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
    for n in acc:
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], n)
        if not a: print("NO EXISTE", n); continue
        e = estado(dict(a))
        print("AUTO", n, "·", e.get("estado"), "·", {k: v for k, v in e.items() if k in ("fecha", "version", "motivo", "casos")})
    for r in q("SELECT nombre, estado, ip FROM equipos WHERE cliente_id=? AND (nombre LIKE 'srv-e1%' OR nombre LIKE 'srv-windows-iis%' OR nombre LIKE 'rtr-e1%')", cl["id"]):
        print("EQ", dict(r))
PY
sudo virsh list --all 2>/dev/null | grep -i "e1\|iis\|prueba"
curl -sk -o /dev/null -w "aap ping %{http_code}\n" https://aap.34.176.42.56.sslip.io/api/controller/v2/ping/
