"""FaceOps · módulo Hipervisores: VMware, KVM, Nutanix y OpenShift Virtualization. Vive en su propio repositorio (faceops-invoke/mod-hipervisores); usa el núcleo (vistas, permisos, inventario)."""
import os

from flask import abort, g, render_template
from . import requiere_login
from .vistas import bp, inventario, TIPOS_HIPERVISOR, _resumen_grupo, equipos_visibles, j


@bp.route("/hipervisores", methods=["GET", "POST"])
@requiere_login
def hipervisores():
    return inventario(seccion="hipervisores")


@bp.route("/hipervisores/<int:iid>")
@requiere_login
def hipervisor(iid):
    """Ficha del hipervisor: datos generales del anfitrión, recursos, salud, sus máquinas virtuales y, al final, la lista de
    automatizaciones que se pueden abordar sobre él (aún no implementadas)."""
    import yaml
    from .infraestructuras import etiqueta_centro, lista
    eqs_ = equipos_visibles()
    infras = lista(g.cliente["id"], eqs_)
    x = next((i for i in infras if i["id"] == iid and i["tipo"] in TIPOS_HIPERVISOR), None)
    if not x:
        abort(404)
    vms = sorted([e for e in eqs_ if (e.get("ubicacion") or "") == x["ubicacion"]], key=lambda e: (e.get("energia") != "encendido", e["nombre"]))
    d_ = x["detalle_d"]
    host = next((e for e in eqs_ if d_.get("anfitrion") and e.get("ip") == d_["anfitrion"] and (e.get("categoria") or "servidor") == "servidor"), None)
    vms = [e for e in vms if not host or e["id"] != host["id"]]
    cap = d_.get("capacidad") or {}
    fisicos = nodos_fisicos(x, eqs_)
    asig = {"cpu": sum(e.get("cpu") or 0 for e in vms), "ram": round(sum(e.get("ram_mb") or 0 for e in vms) / 1024),
            "ram_on": round(sum(e.get("ram_mb") or 0 for e in vms if e.get("energia") == "encendido") / 1024),
            "disco": round(sum(sum(dd.get("gb") or 0 for dd in j(e.get("discos"), [])) for e in vms))}
    try:
        cat = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "hipervisor-automatizaciones.yml"), encoding="utf-8")) or {}
    except OSError:
        cat = {}
    autos = (cat.get("todos") or []) + (cat.get(x["tipo"]) or [])
    areas = list(dict.fromkeys(a["area"] for a in autos))
    return render_template("hipervisor.html", x=x, info=d_.get("info") or {}, cap=cap, asig=asig, vms=vms, host=host, fisicos=fisicos,
                           res=_resumen_grupo(vms) if vms else None, autos=autos, areas=areas,
                           etiq=etiqueta_centro([i for i in infras if i["centro"] == x["centro"]]))


def nodos_fisicos(x, eqs):
    """Máquinas físicas que levantan el hipervisor: las que declara su detalle («nodos», p. ej. un clúster vSphere o Nutanix)
    o, si no, su anfitrión con el hardware leído al probar el acceso."""
    d_ = x.get("detalle_d") or {}
    info, cap = d_.get("info") or {}, d_.get("capacidad") or {}
    por_ip = {e.get("ip"): e for e in eqs if e.get("ip")}
    out = []
    for n in d_.get("nodos") or []:
        e = por_ip.get(n.get("ip"))
        out.append({"nombre": n.get("nombre") or (e or {}).get("nombre") or n.get("ip"), "ip": n.get("ip"), "equipo": e,
                    "cpu": n.get("cpu"), "sockets": n.get("sockets"), "cores": n.get("cores"), "hilos": n.get("hilos"), "virt": n.get("virt"),
                    "ram_gb": n.get("ram_gb"), "ram_libre": n.get("ram_disp_gb"), "disco_gb": n.get("disco_gb"), "disco_libre": n.get("disco_disp_gb"),
                    "estado": n.get("estado") or "—"})
    if not out and (d_.get("anfitrion") or info):
        e = por_ip.get(d_.get("anfitrion"))
        out.append({"nombre": info.get("nombre") or (e or {}).get("nombre") or d_.get("anfitrion"), "ip": d_.get("anfitrion"), "equipo": e,
                    "cpu": info.get("cpu"), "sockets": info.get("sockets"), "cores": cap.get("cores"), "hilos": info.get("hilos"), "virt": info.get("virt"),
                    "ram_gb": cap.get("ram_gb"), "ram_libre": cap.get("ram_disp_gb"), "disco_gb": cap.get("disco_gb"), "disco_libre": cap.get("disco_disp_gb"),
                    "estado": "encendido" if x.get("estado") == "conectada" else (x.get("estado") or "—")})
    return out
