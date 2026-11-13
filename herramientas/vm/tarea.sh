#!/bin/bash
# Estado de las automatizaciones del Núcleo con falla (solo lectura). Sin secretos.
cat > /tmp/e1s02.py <<'PY'
import json
from app import create_app
from app.bd import q, q1, j
create_app()
from app import pruebas
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); cid = cl["id"]
for n in ["Sistema · Inventariar máquina", "Sistema · Paquetes · inventario y respaldo", "FaceOps · Vincular máquina on-premise", "FaceOps · Vincular máquina a la bóveda", "Infra · Encender o apagar equipo"]:
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cid, n)
    if not a: print("NO EXISTE", n); continue
    tec = j(a["tecnico"], {})
    print("==", n, a["id"], pruebas.estado(dict(a))["estado"], "visible", a["visible"], "pb", tec.get("playbook"), "nodos", [x.get("nombre") if isinstance(x, dict) else x for x in tec.get("nodos") or []][:6], "tipo", tec.get("tipo"))
for e in q("SELECT id, nombre, estado, ip, so, inventario, categoria, forma, aap_host_id FROM equipos WHERE cliente_id=? AND (nombre LIKE 'srv-prueba%' OR nombre LIKE 'srv-windows%' OR nombre LIKE 'srv-no-%' OR nombre LIKE 'srv-pn%' OR nombre LIKE 'nodo-%') ORDER BY nombre", cid):
    print(dict(e))
print("usuarios qa:", [ (u["usuario"], u["es_admin"]) for u in q("SELECT usuario, es_admin FROM usuarios WHERE usuario LIKE 'qa.%' OR es_admin=1")])
PY
sudo podman cp /tmp/e1s02.py faceops:/tmp/e1s02.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/e1s02.py 2>&1 | grep -v -i warn | tail -60; sudo podman exec faceops rm -f /tmp/e1s02.py
sudo virsh dominfo srv-no-existe-99 2>/dev/null | head -8
for v in srv-prueba-rhel9-01 srv-prueba-rhel8-01 srv-windows-iis-01 srv-prueba-resp-01 srv-no-existe-99; do echo "$v $(sudo virsh domifaddr $v 2>/dev/null | awk '/ipv4/{print $4}') $(sudo virsh dumpxml $v | grep -o '<description>[^<]*' | head -1)"; done
