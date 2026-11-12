"""FaceOps · módulo Nubes: máquinas en GCP, AWS, Azure y OCI. Vive en su propio repositorio (faceops-invoke/mod-nubes); usa el núcleo (vistas, permisos, inventario)."""

from flask import abort, g, render_template, request, session, url_for
from . import requiere_login
from .bd import q, q1
from .vistas import bp, inventario, _resumen_grupo, equipos_visibles, hallazgos_visibles, j

# Acción que enciende o apaga máquinas en la nube (por ticket y con aprobación en AAP). Mientras no esté certificada, los
# botones de la caja Encendido quedan deshabilitados con el motivo.
ENERGIA_NUBE = "Nube · Encender o apagar VM"


def _accion_lista(cliente_id, nombre_aap):
    """(automatización, motivo): motivo vacío si se puede usar (publicada en AAP y certificada, o con excepción autorizada)."""
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, nombre_aap)
    if not a:
        return None, f"Aún no disponible: la acción «{nombre_aap}» no está publicada en AAP"
    from .pruebas import estado, exceptuada
    if estado(dict(a, cliente_id=cliente_id))["estado"] != "certificada" and not exceptuada(nombre_aap):
        return a, f"Aún no disponible: «{nombre_aap}» no ha pasado su plan de prueba (no está certificada)"
    return a, ""


def _radar_nube(x, con, maqs, propias, publicas, cuenta):
    """Salud de la nube con los 6 pilares: los criterios propios de la conexión y los de sus máquinas en FaceOps.
    Cada criterio no cumplido lleva cómo remediarlo («rem»)."""
    from .radar import _armar, radar_equipos
    r_eq = radar_equipos(propias, hallazgos_visibles(propias), g.cliente) if propias else None
    crit = [dict(c, rem="maquinas") for p_ in (r_eq or {}).get("principios", []) for c in p_["criterios"]]
    cred, fte = con.get("cred"), con.get("fuente")
    en_ff = [m for m in maqs if not m.get("solo_faceops")]
    def c_(cid, pr, nom, exp, v, rem):
        crit.append({"id": cid, "principio": pr, "nombre": nom, "explica": exp, "pct": None if v is None else (round(v * 100) if isinstance(v, float) else (100 if v else 0)),
                     "faltan": [], "n_faltan": 0, "rem": rem})
    c_("NB1", "Disponibilidad", "Conexión verificada", "AAP lee la nube con su credencial y su fuente dinámica", x["estado"] == "conectada", "conexion")
    c_("NB2", "Excelencia operativa", "Credencial en AAP", "La credencial de la nube existe en AAP (cifrada)", bool(cred), "conexion")
    c_("NB3", "Excelencia operativa", "Inventario dinámico al día", "La última sincronización de la fuente dinámica terminó bien",
       (fte.get("estado") == "successful") if fte else (False if cred else None), "sincronizar")
    c_("NB4", "Seguridad", "Credencial de la cuenta declarada", "La credencial apunta al mismo proyecto o cuenta declarado en FaceOps",
       (cred.get("proyecto") == cuenta) if (cred and cred.get("proyecto") and cuenta) else None, "conexion")
    c_("NB5", "Excelencia operativa", "Máquinas de la nube en FaceOps", "Cada máquina que AAP ve en la nube está en el inventario de FaceOps",
       (sum(1 for m in en_ff if m.get("equipo")) / len(en_ff)) if en_ff else None, "sincronizar")
    c_("NB6", "Costo", "Sin IPs públicas ociosas", "Ninguna IP pública reservada en máquinas apagadas (se cobra igual)",
       all(p_["on"] for p_ in publicas) if publicas else None, "publicas")
    r = _armar(crit, max(1, len(propias)), g.cliente)
    r["remediables"] = [c for c in crit if c["pct"] is not None and c["pct"] < 100]
    return r


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
        x["estado"], x["mensaje"] = "sin verificar", f"No se pudo consultar AAP: {err}"
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
    cuenta = x["detalle_d"].get(cuenta_k) or ((con or {}).get("cred") or {}).get("proyecto") or ""
    from .infraestructuras import ayudas_contexto
    radar_n = _radar_nube(x, con or {}, maqs, propias, publicas, cuenta)
    _, energia_no = _accion_lista(g.cliente["id"], ENERGIA_NUBE)
    if not energia_no and g.perfil["modo"] != "escritura":
        energia_no = "Tu perfil es de solo lectura"
    if not energia_no and not any(m.get("equipo") for m in maqs):
        energia_no = "La nube aún no tiene máquinas en FaceOps"
    acciones = q("SELECT * FROM automatizaciones WHERE cliente_id=? AND (nombre_aap LIKE 'Nube ·%' OR nombre_aap LIKE 'Migración a GCP ·%') ORDER BY nombre", g.cliente["id"])
    for a in acciones:
        a["bloqueo"] = _accion_lista(g.cliente["id"], a["nombre_aap"])[1]
    validar_no = _accion_lista(g.cliente["id"], VALIDAR_NUBE)[1]
    return render_template("nube.html", validar_no=validar_no, radar_n=radar_n, energia_no=energia_no, acciones=acciones, prov_ctx=ayudas_contexto(x, con), x=x, con=con or {}, err=err, maqs=maqs, zonas=zonas, redes=redes, discos=discos, publicas=publicas,
                           clusters=clusters, ocp=ocp, res=_resumen_grupo(propias) if propias else None, fuente_tipo=FUENTE_NUBE.get(x["tipo"], ""),
                           cuenta=cuenta,
                           cuenta_t=cuenta_t, disco_gb=round(sum(float(d.get("gb") or 0) for d in discos)), costo_total=sum(m["costo_mes"] or 0 for m in maqs), con_costo=any(m["costo_mes"] is not None for m in maqs))


@bp.route("/nubes/<int:iid>/energia", methods=["POST"])
@requiere_login
def nube_energia(iid):
    """Encender o apagar las máquinas de la nube: un ticket por máquina con la acción certificada; la aprobación vive en AAP.
    FaceOps nunca enciende ni apaga directo."""
    from .aap import ErrorAAP
    from .infraestructuras import lista
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    accion = request.form.get("accion", "")
    eqs_ = equipos_visibles()
    x = next((i for i in lista(g.cliente["id"], eqs_) if i["id"] == iid and i["ambito"] == "nube"), None)
    if not x or accion not in ("encender", "apagar"):
        return {"error": "Nube o acción no válidas"}, 400
    a, motivo = _accion_lista(g.cliente["id"], ENERGIA_NUBE)
    if motivo:
        return {"error": motivo}, 400
    on_ = accion == "apagar"
    dest = [e for e in eqs_ if (e.get("ubicacion") or "") == x["ubicacion"] and (e.get("energia") == "encendido") == on_]
    if not dest:
        return {"error": f"No hay máquinas para {accion} en {x['nombre']}"}, 400
    t = None
    try:
        for e in dest:
            t = solicitar(g.usuario, g.cliente, a, (e.get("segmentos") or [""])[0], {"en_equipo": e["nombre"], "en_accion": accion, "nube": x["nombre"]},
                          f"{accion.capitalize()} {e['nombre']} (nube {x['nombre']})", filtros={"ambientes": [e["ambiente"]]} if e.get("ambiente") else None)
    except (PermissionError, ErrorAAP, ValueError) as err:
        return {"error": str(err)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"], "nombre_aap": a["nombre_aap"], "n": len(dest)}


VALIDAR_NUBE = "Nube · Validar conexión e inventariar"


@bp.route("/nubes/<int:iid>/conexion", methods=["POST"])
@requiere_login
def nube_conexion(iid):
    """«Editar conexión» de la nube: guarda los datos y la credencial cifrada en AAP (los secretos nunca vuelven al HTML) y
    abre el ticket con aprobación «Validar conexión e inventariar nube», que prueba la API del proveedor y trae sus VM.
    Si esa acción no está publicada o certificada, lo dice: la conexión queda guardada y sin validar."""
    from flask import flash, redirect
    from .aap import ErrorAAP
    from .infraestructuras import conectar, editar
    if g.perfil["modo"] != "escritura":
        abort(403)
    try:
        n = (editar if request.form.get("modo") == "editar" else conectar)(g.usuario, g.cliente, iid, request.form)
    except (ValueError, ErrorAAP) as e:
        flash(f"No se guardó la conexión: {e}", "error")
        return redirect(url_for("consola.nube", iid=iid) + "?editar=1")
    return _abrir_validacion(iid, f"Conexión de «{n}» guardada; la credencial queda cifrada en AAP.")


@bp.route("/nubes/<int:iid>/validar", methods=["POST"])
@requiere_login
def nube_validar(iid):
    """Vuelve a validar la conexión guardada (sin cambiarla): el mismo ticket con aprobación."""
    if g.perfil["modo"] != "escritura":
        abort(403)
    return _abrir_validacion(iid, "")


def _abrir_validacion(iid, guardado):
    from flask import flash, redirect
    from .aap import AAP, ErrorAAP
    from .bd import ej
    from .infraestructuras import _CACHE_NUBE, _fuente, estado_nube, lista
    from .operacion import solicitar
    volver = url_for("consola.nube", iid=iid)
    cid = g.cliente["id"]
    pre = (guardado + " ") if guardado else ""
    x = next((i for i in lista(cid, equipos_visibles()) if i["id"] == iid and i["ambito"] == "nube"), None)
    if not x:
        abort(404)
    try:
        aap = AAP.de_cliente(g.cliente)
        cred = (aap.get("credentials/", name=x["credencial"]).get("results") or [None])[0] if x.get("credencial") else None
        if not cred:
            flash(pre + "AAP no tiene la credencial de la nube: no se pudo abrir la validación.", "error")
            return redirect(volver)
        inv_nom = x.get("inventario") or f"Infra · {x['nombre']}"
        inv = (aap.get("inventories/", name=inv_nom).get("results") or [None])[0]
        if not inv:   # la nube se registró sin inventario en AAP: se crea con su fuente dinámica
            org = (aap.get("organizations/", name="FaceOps").get("results") or [{}])[0].get("id")
            inv = aap.post("inventories/", {"name": inv_nom, "organization": org, "description": f"Máquinas de {x['nombre']} ({x['prov']['nombre']})"})
            ej("UPDATE infraestructuras SET inventario=? WHERE id=?", inv_nom, iid)
        _fuente(aap, inv, x["nombre"], x["tipo"], cred, dict(x["detalle_d"]))
        _CACHE_NUBE.pop((cid, iid), None)
        con = estado_nube(aap, dict(x, inventario=inv_nom), cid, guardar=False)
    except ErrorAAP as e:
        flash(pre + f"No se pudo preparar la validación en AAP: {str(e)[:200]}", "error")
        return redirect(volver)
    if not con.get("fuente") or not con.get("inv"):
        flash(pre + "Falta la fuente dinámica o el inventario en AAP: no se pudo abrir la validación.", "error")
        return redirect(volver)
    a, motivo = _accion_lista(cid, VALIDAR_NUBE)
    if motivo:
        flash(pre + f"No se creó el ticket de validación. {motivo}.", "error")
        return redirect(volver)
    try:
        t = solicitar(g.usuario, g.cliente, a, "", {"nv_nube": x["nombre"], "nv_fuente_id": con["fuente"]["id"], "nv_inventario_id": con["inv"]["id"]},
                      f"Validar conexión e inventariar {x['nombre']}")
    except (PermissionError, ErrorAAP, ValueError) as e:
        flash(pre + f"No se creó el ticket de validación: {e}", "error")
        return redirect(volver)
    flash(pre + f"Ticket {t['numero']} creado: espera aprobación en AAP; al aprobarse prueba la API del proveedor y trae las VM.", "ok")
    return redirect(volver)
