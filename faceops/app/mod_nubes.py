"""FaceOps · módulo Nubes: máquinas en GCP, AWS, Azure y OCI. Vive en su propio repositorio (faceops-invoke/mod-nubes); usa el núcleo (vistas, permisos, inventario)."""

from flask import abort, g, render_template
from . import requiere_login
from .vistas import bp, inventario, _resumen_grupo, equipos_visibles, j


@bp.route("/nubes", methods=["GET", "POST"])
@requiere_login
def nubes():
    return inventario(seccion="nubes")


@bp.route("/nubes/<int:iid>")
@requiere_login
def nube(iid):
    """Detalle de una nube: proveedor y cuenta, conexión real en AAP (credencial, inventario, fuente dinámica y su última
    sincronización) y todo lo que AAP ve en ella: regiones y zonas, redes y subredes, máquinas, discos, IPs públicas y
    clústeres OpenShift. FaceOps no toca la nube: lo lee de AAP (inventario dinámico) y de su propio inventario."""
    from .aap import AAP, ErrorAAP
    from .infraestructuras import DATO_CUENTA, FUENTE_NUBE, estado_nube, lista, maquinas_nube
    from .uso_energia import uso
    eqs_ = equipos_visibles()
    x = next((i for i in lista(g.cliente["id"], eqs_) if i["id"] == iid and i["ambito"] == "nube"), None)
    if not x:
        abort(404)
    con, hosts, err = None, [], ""
    try:
        aap = AAP.de_cliente(g.cliente)
        con = estado_nube(aap, x, g.cliente["id"])
        if con.get("inv") and con["inv"]["hosts"]:
            hosts = maquinas_nube(aap, con["inv"]["id"])
    except ErrorAAP as e:
        err = str(e)[:200]
    # máquinas: las que AAP trae de la nube y las que FaceOps ya tiene en esa nube (unidas por nombre)
    propias = [e for e in eqs_ if (e.get("ubicacion") or "") == x["ubicacion"] or (con and con.get("inv") and e.get("inventario") == con["inv"]["nombre"])]
    por_nombre = {e["nombre"]: e for e in propias}
    maqs = []
    for h in hosts:
        e = por_nombre.pop(h["nombre"], None)
        maqs.append(dict(h, equipo=e))
    for e in por_nombre.values():
        d_ = j(e.get("datos"), {})
        maqs.append({"nombre": e["nombre"], "estado": e.get("energia") or "", "tipo": d_.get("tipo_maquina") or d_.get("machine_type") or "",
                     "zona": d_.get("zona") or "", "region": d_.get("region") or "", "ip": e.get("ip") or "", "publicas": [], "redes": [],
                     "discos": [{"nombre": dd.get("dispositivo") or dd.get("nombre") or "", "gb": dd.get("gb"), "tipo": "", "arranque": False}
                                for dd in j(e.get("discos"), [])], "etiquetas": {}, "cluster": "", "equipo": e, "solo_faceops": True})
    for m in maqs:
        e = m.get("equipo")
        m["on"] = (m["estado"] or "").upper() in ("RUNNING", "ENCENDIDO", "VM RUNNING") or (e and e.get("energia") == "encendido" and not m["estado"])
        try:
            u_ = uso(e, j(e.get("datos"), {})) if e else None
        except Exception:  # noqa: BLE001
            u_ = None
        m["costo_mes"] = u_.get("costo_mes_clp") if u_ else None
        m["tarifa"] = u_.get("tarifa") if u_ else ""
    maqs.sort(key=lambda m: (m["region"], m["zona"], not m["on"], m["nombre"]))
    zonas = {}
    for m in maqs:
        if m["zona"] or m["region"]:
            zonas.setdefault(m["region"] or "—", {}).setdefault(m["zona"] or "—", []).append(m)
    redes = {}
    for m in maqs:
        for n in m["redes"]:
            k = (n["vpc"] or "—", n["subred"] or "—", n["region"] or "—")
            redes.setdefault(k, []).append(m["nombre"])
    discos = [dict(d, maquina=m["nombre"], zona=m["zona"]) for m in maqs for d in m["discos"]]
    publicas = [{"ip": ip, "maquina": m["nombre"], "zona": m["zona"], "on": m["on"]} for m in maqs for ip in m["publicas"]]
    clusters = {}
    for m in maqs:
        if m["cluster"]:
            clusters.setdefault(m["cluster"], []).append(m)
    ocp = [e for e in propias if (e.get("categoria") or "").startswith("clúster")]
    cuenta_k, cuenta_t = DATO_CUENTA.get(x["tipo"], ("", "Cuenta"))
    return render_template("nube.html", x=x, con=con or {}, err=err, maqs=maqs, zonas=zonas, redes=redes, discos=discos, publicas=publicas,
                           clusters=clusters, ocp=ocp, res=_resumen_grupo(propias) if propias else None, fuente_tipo=FUENTE_NUBE.get(x["tipo"], ""),
                           cuenta=x["detalle_d"].get(cuenta_k) or ((con or {}).get("cred") or {}).get("proyecto") or "",
                           cuenta_t=cuenta_t, disco_gb=round(sum(float(d.get("gb") or 0) for d in discos)), costo_total=sum(m["costo_mes"] or 0 for m in maqs), con_costo=any(m["costo_mes"] is not None for m in maqs))
