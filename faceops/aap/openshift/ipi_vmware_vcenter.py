#!/usr/bin/env python3
"""FaceOps · OpenShift IPI sobre VMware: revisa el vCenter contra lo que necesita la arquitectura pedida.

Entrada: JSON en argv[1] {datacenter, cluster, datastore, red, carpeta, crear_carpeta, disco_total_gb, nodos}
Credencial: VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (credencial «VMware vCenter» de AAP).
Salida (stdout, JSON): {"vcenter": {...}, "chequeos": [{"item", "ok", "detalle", "bloquea"}]}
No cambia nada en el vCenter salvo crear la carpeta de las VM si se pide (crear_carpeta)."""
import json
import os
import ssl
import sys
from urllib.parse import urlparse

from pyVim.connect import SmartConnect
from pyVmomi import vim

PRIVILEGIOS = [  # los mínimos que pide el instalador IPI (resumen de la documentación de OpenShift sobre vSphere)
    "VirtualMachine.Config.AddNewDisk", "VirtualMachine.Inventory.Create", "VirtualMachine.Interact.PowerOn",
    "VirtualMachine.Provisioning.DeployTemplate", "Datastore.AllocateSpace", "Network.Assign", "Resource.AssignVMToPool",
    "Folder.Create", "StorageProfile.View", "Cns.Searchable", "Sessions.ValidateSession",
]


def main():
    p = json.loads(sys.argv[1])
    ch = []

    def chk(item, ok, detalle, bloquea=True):
        ch.append({"item": item, "ok": bool(ok), "detalle": detalle, "bloquea": bloquea})

    h = os.environ.get("VMWARE_HOST", "")
    u = urlparse(h if "://" in h else "https://" + h)
    try:
        si = SmartConnect(host=u.hostname, port=u.port or 443, user=os.environ.get("VMWARE_USER", ""), pwd=os.environ.get("VMWARE_PASSWORD", ""),
                          sslContext=ssl._create_unverified_context())
    except Exception as x:  # noqa: BLE001
        chk("Conexión al vCenter", False, f"{u.hostname}:{u.port or 443} · {str(x)[:200]}")
        print(json.dumps({"vcenter": {}, "chequeos": ch}))
        return
    c = si.RetrieveContent()
    ab = c.about
    simulado = "simulator" in (ab.fullName or "").lower()
    vc = {"host": u.hostname, "puerto": u.port or 443, "version": ab.version, "build": ab.build, "nombre": ab.fullName, "simulado": simulado}
    chk("Conexión al vCenter", True, f"{ab.fullName}")
    mayor = int((ab.version or "0").split(".")[0] or 0)
    chk("Versión de vSphere", mayor >= 7 or simulado, f"{ab.version} (OpenShift 4.14+ pide vSphere 7.0 Update 2 o superior)" + (" · simulado: no se exige" if simulado else ""))

    dc = next((x for x in c.rootFolder.childEntity if isinstance(x, vim.Datacenter) and x.name == p["datacenter"]), None)
    chk("Datacenter", dc, p["datacenter"] if dc else f"no existe «{p['datacenter']}»")
    if not dc:
        print(json.dumps({"vcenter": vc, "chequeos": ch}))
        return
    cl = next((x for x in dc.hostFolder.childEntity if isinstance(x, vim.ClusterComputeResource) and x.name == p["cluster"]), None)
    chk("Clúster de vSphere", cl, (f"{p['cluster']} · {len(cl.host)} host(s)" if cl else f"no existe «{p['cluster']}»"))
    if cl:
        cpu = sum(hh.hardware.cpuInfo.numCpuThreads for hh in cl.host if hh.hardware)
        mem = round(sum(hh.hardware.memorySize for hh in cl.host if hh.hardware) / 2**30)
        pide_cpu = sum(n["cpu"] for n in p["nodos"])
        pide_mem = sum(n["ram_gb"] for n in p["nodos"])
        vc.update(cpu_total=cpu, ram_total_gb=mem)
        chk("Capacidad del clúster (CPU)", cpu >= pide_cpu or simulado, f"pide {pide_cpu} vCPU · el clúster tiene {cpu} hilos", bloquea=False)
        chk("Capacidad del clúster (memoria)", mem >= pide_mem or simulado, f"pide {pide_mem} GB · el clúster tiene {mem} GB", bloquea=False)
    ds = next((x for x in dc.datastore if x.name == p["datastore"]), None)
    if ds:
        libre = round(ds.summary.freeSpace / 2**30)
        chk("Datastore", libre >= p["disco_total_gb"] or simulado, f"{ds.name} · {libre} GB libres · pide {p['disco_total_gb']} GB (thin)")
        vc["datastore_libre_gb"] = libre
    else:
        chk("Datastore", False, f"no existe «{p['datastore']}» · hay: {', '.join(x.name for x in dc.datastore)}")
    red = next((x for x in dc.network if x.name == p["red"]), None)
    chk("Red (port group)", red, p["red"] if red else f"no existe «{p['red']}» · hay: {', '.join(x.name for x in dc.network)}")
    carpeta = None
    ruta = [x for x in (p.get("carpeta") or "").split("/") if x]
    if ruta:
        f = dc.vmFolder
        for nombre in ruta[2:] if len(ruta) >= 2 and ruta[0] == dc.name and ruta[1] == "vm" else ruta:
            sig = next((x for x in f.childEntity if isinstance(x, vim.Folder) and x.name == nombre), None)
            if not sig and p.get("crear_carpeta"):
                sig = f.CreateFolder(nombre)
            if not sig:
                f = None
                break
            f = sig
        carpeta = f
    chk("Carpeta de las VM", carpeta, (p.get("carpeta") if carpeta else f"no existe «{p.get('carpeta')}» (el instalador la puede crear si no se indica)"), bloquea=False)
    # permisos del usuario en el datacenter
    try:
        am = c.authorizationManager
        sesion = c.sessionManager.currentSession
        res = am.HasUserPrivilegeOnEntities(entities=[dc], userName=sesion.userName, privId=PRIVILEGIOS)
        faltan = [pp.privId for r in res for pp in r.privAvailability if not pp.isGranted]
        chk("Permisos del usuario de vCenter", not faltan, "tiene los privilegios mínimos del instalador" if not faltan else "faltan: " + ", ".join(faltan[:8]))
    except Exception as x:  # noqa: BLE001
        chk("Permisos del usuario de vCenter", simulado, ("simulado: no se verifican" if simulado else f"no se pudieron leer: {str(x)[:120]}"), bloquea=not simulado)
    vc["certificado"] = "sin validar" if simulado else "revisar: el instalador exige confiar en la CA del vCenter"
    print(json.dumps({"vcenter": vc, "chequeos": ch}))


if __name__ == "__main__":
    main()
