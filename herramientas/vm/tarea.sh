#!/bin/bash
# SIEM · etapa B: capacidad del anfitrión, despliegue de FaceOps, catálogo y creación de la VM del SIEM con «Habilitar servidor»
echo "== capacidad del anfitrión"
echo "cores: $(nproc) · $(grep MemTotal /proc/meminfo | awk '{printf "RAM total %.1f GB", $2/1048576}') · $(grep MemAvailable /proc/meminfo | awk '{printf "disponible %.1f GB", $2/1048576}')"
df -BG /var/lib/libvirt/images | tail -1 | awk '{print "disco libre imágenes:", $4}'
for v in $(sudo virsh list --name); do sudo virsh dominfo "$v"; done | awk '/^CPU\(s\)/{c+=$2}/^Max memory/{m+=$3}END{printf "VM encendidas: %d vCPU · %.1f GB asignados\n", c, m/1048576}'
echo "VM definidas: $(sudo virsh list --all --name | grep -c .) · encendidas: $(sudo virsh list --name | grep -c .)"
DISP=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo); LIBRE=$(df -BG --output=avail /var/lib/libvirt/images | tail -1 | tr -dc 0-9)
echo "== despliegue"
cd ~/summit-redhat && git pull -q --ff-only origin main; git log --oneline -1 | cut -c1-70
python3 faceops/aap/configurar.py 2>&1 | grep -E "SIEM|Contención|Traceback|ERROR" | head -8
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops
for i in $(seq 1 30); do sudo podman exec faceops true 2>/dev/null && break; sleep 5; done; sleep 15
cat > /tmp/siem_b.py <<PY
import json, time, sys
from app import create_app
from app.bd import q, q1
from app.operacion import sincronizar_catalogo, solicitar, resolver_aprobacion
a = create_app()
DISP, LIBRE = int("$DISP"), int("$LIBRE")
with a.app_context():
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); u = q1("SELECT * FROM usuarios WHERE usuario='cquezada'")
    try:
        print("catálogo:", sincronizar_catalogo(cl, "Claude"))
    except Exception as x:
        print("catálogo:", type(x).__name__, str(x)[:200])
    for n in ("SIEM · Instalar Wazuh", "SIEM · Enviar logs al SIEM", "SIEM · Enviar logs de AAP", "Seguridad · Contención de incidente"):
        x = q1("SELECT id, nombre, categoria, habilita_skill FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], n)
        print(" ", n, "→", dict(x) if x else "NO ESTÁ")
    for i in q("SELECT nombre, tipo, ubicacion FROM infraestructuras WHERE cliente_id=?", cl["id"]):
        print(" infra:", dict(i))
    ya = q1("SELECT id, nombre, ip FROM equipos WHERE cliente_id=? AND nombre LIKE 'srv-siem-%' AND estado!='retirado'", cl["id"])
    if ya:
        print("VM del SIEM ya existe:", dict(ya)); sys.exit(0)
    if DISP < 10 or LIBRE < 60:
        print(f"CAPACIDAD INSUFICIENTE: {DISP} GB de RAM disponible (se necesitan 8 + 2 de margen) y {LIBRE} GB de disco (se necesitan 60): NO SE CREA"); sys.exit(0)
    inf = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND tipo='kvm' ORDER BY id LIMIT 1", cl["id"])
    hab = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='FaceOps · Habilitar servidor'", cl["id"])
    p = {"hab_interno": True, "hab_so": "rhel9", "hab_destino": "onpremise", "hab_infraestructura": inf["nombre"], "hab_ubicacion": inf["ubicacion"],
         "hab_nombre": "siem", "hab_replicas": "1", "hab_cpu": "4", "hab_ram_gb": "8", "hab_disco_gb": "80", "hab_ambiente": "produccion"}
    t = solicitar(u, cl, hab, None, p, "SIEM del laboratorio (Wazuh todo en uno): 4 vCPU, 8 GB, 80 GB")
    print("ticket", t["numero"], t["estado"], flush=True)
    for k in range(300):
        t = q1("SELECT * FROM tickets WHERE id=?", t["id"])
        if t["estado"] == "pendiente_aprobacion":
            try: resolver_aprobacion(u, t["id"], True, "Aprobado por Claude (regla de Cristian)"); print("aprobado", flush=True)
            except Exception as x: print("aprobar:", type(x).__name__, str(x)[:150], flush=True)
        if t["estado"] in ("exitoso", "fallido", "cancelado", "rechazado"): break
        time.sleep(8)
    print("FINAL", t["numero"], t["estado"]); print("\n".join((t["resultado"] or "").strip().splitlines()[-8:])[-1500:])
    time.sleep(20)
    from app.inventario import sincronizar
    try: sincronizar(cl, "Claude")
    except Exception as x: print("sync:", x)
    print("equipo:", q1("SELECT id, nombre, ip, so, estado FROM equipos WHERE cliente_id=? AND nombre LIKE 'srv-siem-%'", cl["id"]))
PY
sudo podman cp /tmp/siem_b.py faceops:/tmp/siem_b.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/siem_b.py 2>&1 | grep -v -i "warn" | tail -40
sudo podman exec faceops rm -f /tmp/siem_b.py
