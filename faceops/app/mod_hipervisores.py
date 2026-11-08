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
    asig = {"cpu": sum(e.get("cpu") or 0 for e in vms), "ram": round(sum(e.get("ram_mb") or 0 for e in vms) / 1024),
            "ram_on": round(sum(e.get("ram_mb") or 0 for e in vms if e.get("energia") == "encendido") / 1024),
            "disco": round(sum(sum(dd.get("gb") or 0 for dd in j(e.get("discos"), [])) for e in vms))}
    try:
        cat = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "hipervisor-automatizaciones.yml"), encoding="utf-8")) or {}
    except OSError:
        cat = {}
    autos = (cat.get("todos") or []) + (cat.get(x["tipo"]) or [])
    areas = list(dict.fromkeys(a["area"] for a in autos))
    return render_template("hipervisor.html", x=x, info=d_.get("info") or {}, cap=cap, asig=asig, vms=vms, host=host,
                           res=_resumen_grupo(vms) if vms else None, autos=autos, areas=areas,
                           etiq=etiqueta_centro([i for i in infras if i["centro"] == x["centro"]]))
