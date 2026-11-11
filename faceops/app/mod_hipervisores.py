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


# ------------------------------------------------------------------ mover una máquina a otra infraestructura (ficha de la máquina)
MOVER = "Infra · Mover máquina entre centros de datos"
NO_SE_MUEVEN = ("nodo-vault", "sno")   # la bóveda y OpenShift: el playbook también los rechaza


def _cap_anfitrion(hips, x):
    """Capacidad medida del anfitrión de un hipervisor («Probar acceso»). Un centro emulado que comparte el anfitrión con
    otro (misma IP de anfitrión) usa la medición de ese otro: es el mismo equipo físico."""
    d_ = x["detalle_d"]
    if d_.get("capacidad"):
        return d_["capacidad"]
    o = next((i for i in hips if i["tipo"] == x["tipo"] and d_.get("anfitrion") and i["detalle_d"].get("anfitrion") == d_["anfitrion"]
              and i["detalle_d"].get("capacidad")), None)
    return (o or {}).get("detalle_d", {}).get("capacidad") or {}


def _emulado(x):
    d_ = x["detalle_d"]
    if d_.get("emulado"):
        return d_["emulado"] if isinstance(d_["emulado"], str) else "sí"
    if x["tipo"] == "vmware" and str(d_.get("host") or "").endswith(":8989"):
        return "vCenter emulado (vcsim): inventario simulado, no aloja discos reales"
    return ""


def traslado_pendiente(cliente_id, nombre, hips):
    """Traslado sin confirmar: el último ticket exitoso de «Mover máquina» sobre la máquina fue un «mover» (la de origen
    sigue apagada como vuelta atrás hasta confirmar o revertir). Se deduce de los tickets: no depende de la recolección."""
    from .bd import q
    for t in q("SELECT t.numero, t.parametros, t.fin FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
               "WHERE t.cliente_id=? AND a.nombre_aap=? AND t.estado='exitoso' AND t.parametros LIKE ? ORDER BY t.id DESC LIMIT 20",
               cliente_id, MOVER, f'%{nombre}%'):
        p_ = j(t["parametros"], {})
        if p_.get("mv_maquina") != nombre or p_.get("mv_modo") not in ("mover", "confirmar", "revertir"):
            continue   # evaluar no cambia nada
        if p_["mv_modo"] != "mover":
            return None   # confirmar o revertir cerraron el último traslado
        dst = next((x for x in hips if x["tipo"] == "kvm" and (x["detalle_d"].get("red_libvirt") or "default") == p_.get("mv_destino")), None)
        return {"ticket": t["numero"], "fecha": (t["fin"] or "")[:16].replace("T", " "), "hacia": dst["nombre"] if dst else p_.get("mv_destino")}
    return None


def destinos_traslado(cliente_id, e, eqs):
    """A qué hipervisores se puede llevar la máquina: los demás hipervisores, cada uno con sus datos (centro de datos,
    libres, red) y, si no es elegible, por qué. Elegible = conectado, mismo tipo (KVM: la automatización no convierte
    formatos), mismo anfitrión libvirt (copia entre pools y redes) y con CPU, memoria y disco suficientes."""
    from .infraestructuras import lista
    d_e = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    hips = [x for x in lista(cliente_id, eqs) if x["tipo"] in TIPOS_HIPERVISOR]
    origen = next((x for x in hips if x["ubicacion"] == (e.get("ubicacion") or "")), None)
    cpu, ram_gb = int(e.get("cpu") or 0), round((e.get("ram_mb") or 0) / 1024, 1)
    disco = round(sum(dd.get("gb") or 0 for dd in j(e.get("discos"), [])))
    bloqueo = ("Solo se mueven máquinas virtuales" if (e.get("forma") or "") != "virtual" else
               "La bóveda y los nodos de OpenShift no se mueven" if e["nombre"] in NO_SE_MUEVEN or (e.get("categoria") or "") == "clúster openshift" or d_e.get("nodo_ocp") else
               "No se sabe en qué hipervisor vive la máquina" if not origen else
               f"La automatización mueve máquinas que viven en KVM; esta vive en {origen['nombre']}" if origen["tipo"] != "kvm" else "")
    pend = traslado_pendiente(cliente_id, e["nombre"], hips)
    out = []
    for x in hips:
        if origen and x["id"] == origen["id"]:
            continue
        d_ = x["detalle_d"]
        cap = _cap_anfitrion(hips, x)
        mismo = bool(origen and d_.get("anfitrion") and d_.get("anfitrion") == origen["detalle_d"].get("anfitrion"))
        vms = [v for v in eqs if (v.get("ubicacion") or "") == x["ubicacion"] and (v.get("forma") or "") == "virtual"]
        motivos = []
        if x["estado"] != "conectada":
            motivos.append(f"no está operativo (estado: {x['estado']})")
        if x["tipo"] != "kvm":
            motivos.append(f"la automatización no convierte máquinas KVM a {x['prov'].get('nombre', x['tipo'])}")
        elif origen and origen["tipo"] == "kvm" and not mismo:
            motivos.append("está en otro anfitrión KVM: el traslado entre anfitriones distintos aún no está soportado")
        if x["tipo"] == "kvm" and not cap:
            motivos.append("sin capacidad medida: usa «Probar acceso» en el hipervisor")
        if cap:
            if cap.get("cores") and cpu > cap["cores"]:
                motivos.append(f"la máquina tiene {cpu} vCPU y el anfitrión {cap['cores']} cores físicos")
            if not mismo and cap.get("ram_disp_gb") is not None and ram_gb > cap["ram_disp_gb"]:
                motivos.append(f"memoria libre {cap['ram_disp_gb']} GB < {ram_gb} GB de la máquina")
            if cap.get("disco_disp_gb") is not None and disco + 5 > cap["disco_disp_gb"]:
                motivos.append(f"disco libre {cap['disco_disp_gb']} GB < {disco + 5} GB (discos de la máquina + 5 GB de margen)")
        out.append({"id": x["id"], "nombre": x["nombre"], "tipo": x["prov"].get("nombre", x["tipo"]), "centro": x["centro"],
                    "region": x.get("region") or "", "red": d_.get("red") or (f"datacenter vSphere {d_['datacenter']}" if d_.get("datacenter") else "—"),
                    "red_libvirt": d_.get("red_libvirt") or "default", "estado": x["estado"], "vms": len(vms),
                    "cores": cap.get("cores"), "ram_libre": cap.get("ram_disp_gb"), "disco_libre": cap.get("disco_disp_gb"),
                    "medido": (cap.get("medido") or "")[:10], "mismo_anfitrion": mismo, "emulado": _emulado(x),
                    "elegible": not motivos and not bloqueo, "motivos": motivos})
    out.sort(key=lambda y: (not y["elegible"], y["nombre"]))
    return {"maquina": e["nombre"], "cpu": cpu, "ram_gb": ram_gb, "disco_gb": disco, "ambiente": e.get("ambiente") or "",
            "origen": {"id": origen["id"], "nombre": origen["nombre"], "centro": origen["centro"], "red": origen["detalle_d"].get("red") or "—",
                       "red_libvirt": origen["detalle_d"].get("red_libvirt") or "default"} if origen else None,
            "bloqueo": bloqueo, "pendiente": pend, "destinos": out}


def pedir_traslado(usuario, cliente, e, eqs, modo, destino_id=None, prueba=False):
    """Crea el ticket (con aprobación) que lanza «Mover máquina» con el destino elegido; confirmar o revertir un traslado
    pendiente usa el mismo ticket con su paso. Valida en el servidor lo mismo que muestra el flotante."""
    from .bd import q1
    from .operacion import solicitar
    if modo not in ("mover", "confirmar", "revertir"):
        raise ValueError("Paso no válido")
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], MOVER)
    if not a:
        raise ValueError(f"Falta «{MOVER}» en el catálogo (sincroniza con AAP)")
    info = destinos_traslado(cliente["id"], e, eqs)
    if info["bloqueo"]:
        raise PermissionError(info["bloqueo"])
    pend = info["pendiente"]
    if modo == "mover":
        if pend:
            raise PermissionError(f"Hay un traslado sin confirmar (hacia {pend.get('hacia')}): confírmalo o reviértelo primero")
        dst = next((x for x in info["destinos"] if x["id"] == destino_id), None)
        if not dst:
            raise ValueError("Elige un destino de la lista")
        if not dst["elegible"]:
            raise PermissionError(f"{dst['nombre']} no es un destino posible: " + "; ".join(dst["motivos"]))
        red, texto = dst["red_libvirt"], f"Mover {e['nombre']} de {info['origen']['nombre']} a {dst['nombre']}"
    else:
        if not pend:
            raise PermissionError("No hay un traslado pendiente de confirmar")
        red = info["origen"]["red_libvirt"]   # confirmar y revertir no usan el destino (el playbook lo valida igual)
        texto = (f"Confirmar el traslado de {e['nombre']} a {pend.get('hacia')} (retira la copia de origen)" if modo == "confirmar"
                 else f"Revertir el traslado de {e['nombre']}: vuelve a su infraestructura de origen")
    params = {"mv_maquina": e["nombre"], "mv_destino": red, "mv_modo": modo,
              "mv_puerto": 5986 if "windows" in (e.get("so") or "").lower() else 22}
    return solicitar(usuario, cliente, a, ((e.get("segmentos") or [""])[0]), params, texto,
                     filtros={"ambientes": [e["ambiente"]]} if e.get("ambiente") else None, prueba=prueba)


@bp.route("/inventario/<int:eid>/traslado", methods=["GET", "POST"])
@requiere_login
def equipo_traslado(eid):
    """GET: destinos posibles para el flotante «Mover a otra infraestructura». POST: crea el ticket con el destino."""
    from flask import request, session, url_for
    from .aap import ErrorAAP
    from .bd import q1
    from .pruebas import estado as estado_prueba
    eqs = equipos_visibles()
    e = next((x for x in eqs if x["id"] == eid), None)
    if not e:
        return {"error": "Equipo no visible"}, 404
    if request.method == "GET":
        info = destinos_traslado(g.cliente["id"], e, eqs)
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], MOVER)
        info["automatizacion"] = {"nombre": a["nombre"], "estado": estado_prueba(dict(a, cliente_id=g.cliente["id"]))["estado"]} if a else None
        return info
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    try:
        t = pedir_traslado(g.usuario, g.cliente, e, eqs, request.form.get("modo", "mover"), int(request.form.get("destino") or 0) or None)
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    a = q1("SELECT nombre, nombre_aap FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]),
            "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}
