#!/bin/bash
echo "== host"; hostname; nproc; free -g | head -2; df -h /var/lib/libvirt/images | tail -1
echo "== vms"; sudo virsh list --all --name | grep -v '^$' | while read v; do s=$(sudo virsh dominfo "$v" | awk '/^State/{print $2" "$3}/CPU\(s\)/{c=$2}/Max memory/{m=$3}END{print c" "m}' | tr '\n' ' '); echo "$v $s"; done
echo "== suma corriendo"; for v in $(sudo virsh list --name); do sudo virsh dominfo "$v"; done | awk '/^CPU\(s\)/{c+=$2}/^Used memory/{m+=$3}END{print "vcpu",c,"ram_gb",m/1048576}'
echo "== pools"; sudo virsh pool-list --details 2>/dev/null
echo "== podman"; sudo podman ps --format '{{.Names}} {{.Status}}' | head -40
echo "== faceops"
sudo podman exec -i -e FACEOPS_VIGIA=0 -w /opt/faceops -e PYTHONPATH=/opt/faceops faceops python3 - <<'Q' 2>&1 | tail -40
from app import create_app
from app.bd import q, q1
a = create_app()
with a.app_context():
    cl = q1("SELECT id FROM clientes WHERE codigo='lab'")
    for x in q("SELECT nombre,tipo,ubicacion FROM infraestructuras WHERE cliente_id=?", cl["id"]): print("infra", dict(x))
    print("tablas", [r["name"] for r in q("SELECT name FROM sqlite_master WHERE type='table'")])
    for x in q("SELECT nombre_aap, estado_prueba FROM automatizaciones WHERE cliente_id=? AND nombre_aap LIKE '%Habilitar servidor%'", cl["id"]): print(dict(x))
    print("usuarios", [dict(u) for u in q("SELECT id,usuario,correo FROM usuarios LIMIT 10")])
Q
