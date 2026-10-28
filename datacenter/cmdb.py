#!/usr/bin/python3
# CMDB de ServiceNow desde el inventario único: cada equipo del data center queda como CI
# (Windows → cmdb_ci_win_server, RHEL → cmdb_ci_linux_server) con IP, FQDN, sistema, CPU, RAM, disco, roles
# y su estado real (encendido/apagado según libvirt). Idempotente; se puede programar en AAP.
# Uso: python3 cmdb.py [dc1]
import os, subprocess, sys

import yaml

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "demo4-redes", "servicenow"))
import sn  # noqa: E402  (credenciales desde Vault secret/servicenow)

DC = sys.argv[1] if len(sys.argv) > 1 else "dc1"
D = yaml.safe_load(open(f"{AQUI}/inventario/{DC}.yml"))
SO = {"rhel9": ("cmdb_ci_linux_server", "Linux Red Hat", "9"), "windows2022": ("cmdb_ci_win_server", "Windows 2022 Standard", "2022")}

print(f"CMDB · {D['dc']['nombre']}")
for e in D["equipos"]:
    tabla, so, ver = SO[e["so"]]
    ip = f"{D['dc']['red_base']}.{D['zonas'][e['zona']]['octeto']}.{e['ip']}"
    dom = D["dc"]["dominio_ad"] if e["so"].startswith("windows") else D["dc"]["dominio_idm"]
    estado = subprocess.run(["sudo", "virsh", "domstate", e["nombre"]], capture_output=True, text=True).stdout.strip()
    datos = {
        "name": e["nombre"], "fqdn": f"{e['nombre']}.{dom}", "dns_domain": dom, "ip_address": ip,
        "os": so, "os_version": ver, "cpu_count": str(e["cpu"]), "ram": str(e["ram_mb"]), "disk_space": str(e["disco_gb"]),
        "short_description": f"{D['dc']['nombre']} · zona {e['zona']} · {e['descripcion']}",
        "comments": "Roles: " + ", ".join(e["roles"]) + f"\nFuente: inventario único datacenter/inventario/{DC}.yml (Ansible)",
        "install_status": "1",                                     # Instalado
        "operational_status": "1" if estado == "running" else "2",  # Operativo / No operativo
        "virtual": "true",
    }
    r = sn.asegurar(tabla, f"name={e['nombre']}", datos)
    print(f"   · {e['nombre']:<10} {tabla:<22} {ip:<14} {'operativo' if estado == 'running' else estado or 'no existe'}")
print(f"LISTO · {sn.URL}/cmdb_ci_server_list.do?sysparm_query=nameSTARTSWITH{DC}-")
