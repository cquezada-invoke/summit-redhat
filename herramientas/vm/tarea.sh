cat > /tmp/redq2.py <<'PY'
from app import create_app
from app.bd import q, q1
a = create_app()
with a.test_request_context("/"):
    from app import pruebas as P
    cli = q1("SELECT * FROM clientes WHERE codigo='lab'")
    for x in q("SELECT * FROM automatizaciones WHERE cliente_id=? AND (nombre_aap LIKE 'Red%' OR nombre_aap LIKE 'VPN%' OR nombre_aap LIKE '%firewall%' OR nombre_aap LIKE '%Balance%' OR nombre_aap LIKE 'Seguridad%' OR nombre_aap LIKE 'Drift%')", cli["id"]):
        x = dict(x)
        try: est = P.estado(dict(x, cliente_id=cli["id"]))["estado"]
        except Exception as ex: est = "err " + str(ex)[:60]
        print(x["id"], x["nombre_aap"], "|", x.get("playbook"), "|", x.get("proyecto"), "|inv", x.get("inventario"), "|vis", x["visible"], "|lim", x["usa_limite"], "|", est, "|campos", (x.get("campos") or "")[:150])
    print([k for k in dict(q1("SELECT * FROM automatizaciones LIMIT 1")).keys()])
PY
sudo podman cp /tmp/redq2.py faceops:/tmp/redq2.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/redq2.py 2>&1 | grep -v -i warn | cut -c1-500
ls ~/demo-drift 2>/dev/null | head; ls ~ | head -40
sudo podman exec faceops sh -c 'ansible-galaxy collection list 2>/dev/null | grep -iE "netcommon|vyos|cisco|fortinet|frr|haproxy|community.general|paloalto|f5|junos"' 2>/dev/null
