"""FaceOps · módulo Hipervisores: VMware, KVM, Nutanix y OpenShift Virtualization. Vive en su propio repositorio (faceops-invoke/mod-hipervisores); usa el núcleo (vistas, permisos, inventario)."""
import os

from flask import abort, g, render_template
from . import requiere_login
from .hora_local import texto_utc
from .vistas import bp, inventario, TIPOS_HIPERVISOR, _resumen_grupo, equipos_visibles, j


@bp.route("/hipervisores", methods=["GET", "POST"])
@requiere_login
def hipervisores():
    return inventario(seccion="hipervisores")


# Acción que enciende o apaga el anfitrión (por su gestión fuera de banda, con ticket y aprobación en AAP). Mientras no esté
# publicada y certificada, la caja Encendido muestra sus botones deshabilitados con el motivo.
ENERGIA_HIP = "Infra · Encender o apagar anfitrión"
# Acciones del catálogo del hipervisor (datos/hipervisor-automatizaciones.yml) que ya existen en AAP: nombre → plantilla y
# parámetros fijos. «por_maquina»: se lanza desde la ficha de cada máquina (necesita elegirla), no desde aquí.
ACCION_AAP = {"Encendido automático controlado": ("Infra · Encendido automático de máquinas", {"ia_encender": "nodo-vault"}),
              "Mover máquina entre centros de datos": ("Infra · Mover máquina entre centros de datos", "por_maquina")}


def _accion_aap(cliente_id, nombre_aap):
    """(automatización, motivo): motivo vacío si se puede lanzar (publicada en AAP y certificada, o con excepción autorizada)."""
    from .bd import q1
    from .pruebas import estado, exceptuada
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, nombre_aap) if nombre_aap else None
    if not a:
        return None, "Aún no disponible: no está publicada en AAP"
    ep = estado(dict(a, cliente_id=cliente_id))["estado"]
    if ep != "certificada" and not exceptuada(nombre_aap):
        return a, ("Bloqueada por la regla de prueba: " + ("cambió desde su última prueba y debe probarse de nuevo" if ep == "cambiada" else
                   "su última prueba falló y debe pasar de nuevo" if ep == "fallida" else "aún no tiene su plan de prueba ejecutado y aprobado"))
    return a, ""


def _ticket_hv(cliente_id, aid, nombre):
    """Último ticket de la acción pedido sobre este hipervisor (parámetro hv_infra): abierto = «En curso», exitoso = «Aplicada»."""
    from .bd import q
    from .salud_acciones import ABIERTOS
    for t in q("SELECT id, numero, estado, parametros FROM tickets WHERE cliente_id=? AND automatizacion_id=? ORDER BY id DESC LIMIT 40", cliente_id, aid):
        if (j(t["parametros"], {}) or {}).get("hv_infra") == nombre:
            return dict(t, abierto=t["estado"] in ABIERTOS) if (t["estado"] in ABIERTOS or t["estado"] == "exitoso") else None
    return None


def _accion(x, nombre, escritura):
    """Estado de una acción del hipervisor para pintarla: ticket en curso o aplicado, bloqueada con motivo, o lista para pedir."""
    nom_aap, extra = ACCION_AAP.get(nombre, ("", None))
    a, motivo = _accion_aap(g.cliente["id"], nom_aap)
    if not motivo and extra == "por_maquina":
        motivo = "Se pide desde la ficha de cada máquina (botón «Mover a otra infraestructura»)"
    if not motivo and x["tipo"] != "kvm":
        motivo = f"La acción corre sobre anfitriones KVM; este es {x['prov'].get('nombre', x['tipo'])}"
    if not motivo and x["estado"] != "conectada":
        motivo = "El hipervisor no está conectado: primero verifica su conexión"
    ac = {"nombre": nombre, "auto": (a or {}).get("nombre") or nom_aap, "aid": (a or {}).get("id"), "bloqueo": motivo, "escritura": escritura,
          "ticket": _ticket_hv(g.cliente["id"], a["id"], x["nombre"]) if a else None}
    return ac


def radar_hipervisor(x, cap, asig, info, escritura):
    """Salud del hipervisor con los 6 pilares (los de todo FaceOps). Cada criterio no cumplido lleva su acción («acc»):
    un ticket en AAP (con su estado), o la conexión (editarla o volver a probarla)."""
    from .radar import _armar
    crit = []

    def c_(cid, pr, nom, exp, v, acc):
        crit.append({"id": cid, "principio": pr, "nombre": nom, "explica": exp, "faltan": [], "n_faltan": 0, "acc": acc,
                     "pct": None if v is None else (max(0, min(100, round(v))) if not isinstance(v, bool) else (100 if v else 0))})
    con_ = {"tipo": "ui", "ui": "conexion", "boton": "Editar conexión"}
    c_("HV1", "Disponibilidad", "Conexión verificada", "FaceOps llega al hipervisor por AAP con su credencial", x["estado"] == "conectada", con_)
    c_("HV2", "Seguridad", "Credencial cifrada en AAP", "El acceso vive en una credencial cifrada de AAP, no en FaceOps", bool(x.get("credencial")), con_)
    c_("HV3", "Excelencia operativa", f"Capacidad medida en las últimas {MEDICION_VIGENTE_H} h", "CPU, memoria y disco libre leídos con «Probar acceso»",
       (not _medicion_vieja(cap)) if cap else (False if x["estado"] == "conectada" else None), {"tipo": "ui", "ui": "probar", "boton": "Probar acceso"})
    cores = cap.get("cores")
    c_("HV4", "Rendimiento", "Sobreasignación de CPU bajo 2×", "vCPU asignadas frente a 2 por core físico",
       (100 if asig["cpu"] <= cores * 2 else cores * 2 * 100 / asig["cpu"]) if cores and asig["cpu"] else None,
       _accion(x, "Informe de capacidad y sobreasignación", escritura))
    if cap.get("ram_gb") and cap.get("ram_disp_gb") is not None:
        lib = cap["ram_disp_gb"] * 100 / cap["ram_gb"]
        c_("HV5", "Rendimiento", "Memoria libre sobre 10 %", f"Hoy {round(lib)} % libre en el anfitrión", min(100, lib * 10), _accion(x, "Alertas de capacidad del anfitrión", escritura))
    if cap.get("disco_gb") and cap.get("disco_disp_gb") is not None:
        lib = cap["disco_disp_gb"] * 100 / cap["disco_gb"]
        c_("HV6", "Recuperación", "Disco libre sobre 15 %", f"Hoy {round(lib)} % libre en el pool de las máquinas (snapshots y traslados)",
           min(100, lib * 100 / 15), _accion(x, "Ampliar un pool de almacenamiento", escritura))
    if x["tipo"] == "kvm" and info.get("medido"):
        auto_ = [v for v in (info.get("autoinicio") or "").split() if v]
        c_("HV7", "Costo", "Encendido automático controlado", "Al reiniciar el anfitrión solo arranca sola la bóveda"
           + (f" (hoy: {', '.join(auto_)})" if auto_ else ""), set(auto_) <= {"nodo-vault"}, _accion(x, "Encendido automático controlado", escritura))
    r = _armar(crit, 1, g.cliente)
    r["remediables"] = sorted([c for c in crit if c["pct"] is not None and c["pct"] < 100], key=lambda c: c["pct"])
    return r


# El anfitrión KVM del laboratorio es también el anfitrión del monitoreo: su node_exporter escucha solo en 127.0.0.1, así que
# Prometheus lo lee por loopback. Otro anfitrión se lee por su IP.
ANFITRION_MONITOREO = os.environ.get("FACEOPS_ANFITRION_MONITOREO", "192.168.122.1")


def objeto_alertas(x):
    """El hipervisor como objeto de la campana de alertas y del botón Skills (app/alertas_objeto.py, app/habilidades.py):
    nombre estable para Prometheus (hv<id>-<nombre>), su tipo (kvm, vmware) y la IP de la que se leen sus métricas. El
    reflejo de lo aplicado vive en el detalle de la infraestructura. Nunca lleva secretos."""
    import json
    import re
    from flask import url_for
    d_ = x["detalle_d"]
    slug = re.sub(r"[^a-z0-9]+", "-", x["nombre"].lower().translate(str.maketrans("áéíóúñü", "aeiounu"))).strip("-")
    an = d_.get("anfitrion") or ""
    ip = ("127.0.0.1" if an == ANFITRION_MONITOREO else an) if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", an) else ""
    sin_ip = "" if ip else ("Falta el exportador de vSphere (vmware_exporter) apuntando al vCenter: sus alertas se podrán activar cuando entregue métricas"
                            if x["tipo"] == "vmware" else "El hipervisor no tiene la IP de su anfitrión")
    return {"id": x["id"], "nombre": f"hv{x['id']}-{slug}"[:63], "categoria": "hipervisor", "hv_tipo": x["tipo"], "hv_nombre": x["nombre"],
            "hv_estado": x["estado"], "so": x["prov"].get("nombre", x["tipo"]), "ip": ip, "sin_ip": sin_ip, "ambiente": "", "segmentos": [],
            "datos": json.dumps({"hostname": x["nombre"], "alertas": d_.get("alertas") or {}}, ensure_ascii=False),
            "url_probar": url_for("consola.hipervisor_alerta_probar", iid=x["id"]), "url_regla": url_for("consola.hipervisor_alerta_regla", iid=x["id"]),
            "url_skill": url_for("consola.hipervisor_habilidad", iid=x["id"])}


def _hipervisor_visible(iid):
    from .infraestructuras import lista
    return next((i for i in lista(g.cliente["id"], equipos_visibles()) if i["id"] == iid and i["tipo"] in TIPOS_HIPERVISOR), None)


@bp.route("/hipervisores/<int:iid>/alertas", methods=["POST"])
@requiere_login
def hipervisor_alertas(iid):
    """Campana del hipervisor: un ticket a «Monitoreo · Configurar alertas del equipo» con las alertas marcadas (Prometheus →
    Alertmanager → EDA → FaceOps), igual que la campana de una máquina."""
    from flask import request, session, url_for
    from . import alertas_objeto as ao
    from .vistas import _alertas_pedir
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    x = _hipervisor_visible(iid)
    e = objeto_alertas(x) if x else None
    if not e or not ao.tipo_de(e):
        return {"error": "Hipervisor sin alertas"}, 404
    ctx = ao.contexto(g.cliente["id"], e, True, "maquina")
    if not ctx["disponible"]:
        return {"error": ctx["por_que"]}, 400
    try:
        t, a = _alertas_pedir(e, "maquina", request.form)
    except (ValueError, PermissionError) as err:
        return {"error": str(err)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}


@bp.route("/hipervisores/<int:iid>/alertas/probar", methods=["POST"])
@requiere_login
def hipervisor_alerta_probar(iid):
    """«Probar» de una alerta activa del hipervisor: alerta sintética por la cadena real (Alertmanager → EDA → acción)."""
    from flask import current_app, request, session, url_for
    from . import alertas_objeto as ao
    from .alerta_prueba import lanzar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    x = _hipervisor_visible(iid)
    e = objeto_alertas(x) if x else None
    if not e or not ao.tipo_de(e):
        abort(404)
    try:
        pid = lanzar(current_app._get_current_object(), g.cliente, e, "maquina", (request.form.get("aid") or "").strip(), g.usuario)
    except ValueError as err:
        return {"error": str(err)}, 400
    return {"id": pid, "url": url_for("consola.hipervisor_alerta_prueba", iid=iid, pid=pid)}


@bp.route("/hipervisores/<int:iid>/alertas/prueba/<int:pid>")
@requiere_login
def hipervisor_alerta_prueba(iid, pid):
    from .alerta_prueba import leer
    x = _hipervisor_visible(iid)
    p_ = leer(pid)
    if not x or not p_ or p_["cliente_id"] != g.cliente["id"] or p_["equipo"] != objeto_alertas(x)["nombre"]:
        abort(404)
    return {"estado": p_["estado"], "pasos": p_["pasos"], "inicio": p_["inicio"]}


@bp.route("/hipervisores/<int:iid>/alertas/regla")
@requiere_login
def hipervisor_alerta_regla(iid):
    """La regla de una alerta del hipervisor tal como está cargada en Prometheus (solo lectura)."""
    from flask import request, url_for
    from . import alertas_objeto as ao
    x = _hipervisor_visible(iid)
    e = objeto_alertas(x) if x else None
    t = ao.tipo_de(e) if e else None
    aid = (request.args.get("aid") or "").strip()
    a = next((y for y in ao.catalogo()["alertas"] if y["id"] == aid), None)
    if not t or not a:
        abort(404)
    mon = ao.reglas_monitoreo(e, "maquina", t)
    return render_template("alerta_regla.html", e=e, a=a, aid=aid, ambito="maquina", mon=mon, d=(mon or {}).get(aid), grafana=ao.url_grafana(e["nombre"], aid, "maquina"),
                           grupo=ao.grupo_monitoreo(e, "maquina"), archivo=f"{ao.REGLAS}/{e['nombre']}.yml",
                           volver=url_for("consola.hipervisor", iid=iid))


@bp.route("/hipervisores/<int:iid>/habilidad", methods=["POST"])
@requiere_login
def hipervisor_habilidad(iid):
    """Botón Skills del hipervisor: ticket con aprobación en AAP; si ya hay uno abierto de esa skill, devuelve ese."""
    from flask import request, session, url_for
    from .aap import ErrorAAP
    from .habilidades import pedir_habilidad_hipervisor
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    x = _hipervisor_visible(iid)
    if not x:
        return {"error": "Hipervisor no válido"}, 404
    try:
        t, a = pedir_habilidad_hipervisor(objeto_alertas(x), request.form.get("hid") or "")
    except (PermissionError, ErrorAAP, ValueError) as err:
        return {"error": str(err)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"],
            "existente": bool(t.get("existente"))}


@bp.route("/hipervisores/<int:iid>")
@requiere_login
def hipervisor(iid):
    """Ficha del hipervisor con el esquema de toda ficha de FaceOps (como OpenShift y las máquinas): cabecera, barra de
    salud (su flotante muestra solo lo remediable, cada ítem con su acción), caja Encendido, datos, secciones y acciones.
    Toda acción va por AAP con ticket y aprobación; «Editar conexión» abre un flotante lateral."""
    import yaml
    from .infraestructuras import ACCESO_CAMPOS, etiqueta_centro, lista
    eqs_ = equipos_visibles()
    infras = lista(g.cliente["id"], eqs_)
    x = next((i for i in infras if i["id"] == iid and i["tipo"] in TIPOS_HIPERVISOR), None)
    if not x:
        abort(404)
    vms = sorted([e for e in eqs_ if (e.get("ubicacion") or "") == x["ubicacion"]], key=lambda e: (e.get("energia") != "encendido", e["nombre"]))
    d_ = x["detalle_d"]
    host = next((e for e in eqs_ if d_.get("anfitrion") and e.get("ip") == d_["anfitrion"] and (e.get("categoria") or "servidor") == "servidor"), None)
    vms = [e for e in vms if not host or e["id"] != host["id"]]
    cap = _cap_anfitrion([i for i in infras if i["tipo"] in TIPOS_HIPERVISOR], x)
    info = d_.get("info") or {}
    fisicos = nodos_fisicos(x, eqs_)
    asig = {"cpu": sum(e.get("cpu") or 0 for e in vms), "ram": round(sum(e.get("ram_mb") or 0 for e in vms) / 1024),
            "ram_on": round(sum(e.get("ram_mb") or 0 for e in vms if e.get("energia") == "encendido") / 1024),
            "disco": round(sum(sum(dd.get("gb") or 0 for dd in j(e.get("discos"), [])) for e in vms))}
    esc = g.perfil["modo"] == "escritura"
    try:
        cat = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "hipervisor-automatizaciones.yml"), encoding="utf-8")) or {}
    except OSError:
        cat = {}
    acciones = [dict(_accion(x, a["nombre"], esc), area=a["area"], texto=a.get("texto", "")) for a in (cat.get("todos") or []) + (cat.get(x["tipo"]) or [])]
    _, energia_no = _accion_aap(g.cliente["id"], ENERGIA_HIP)
    if energia_no and energia_no.startswith("Aún no disponible"):
        energia_no = "Aún no disponible: encender o apagar el anfitrión va por su gestión fuera de banda (BMC) y la acción aún no está publicada en AAP"
    if not energia_no and not esc:
        energia_no = "Tu perfil es de solo lectura"
    # Conexión: lo que el tipo realmente pide. Un hipervisor por API (vSphere, OpenShift Virtualization) no pide credenciales de
    # máquinas individuales; KVM sí pide el acceso SSH a su anfitrión, porque se administra con virsh.
    p_ = dict(x["prov"], campos=[c for c in x["prov"].get("campos", []) if c[0] not in ACCESO_CAMPOS])
    radar_h = radar_hipervisor(x, cap, asig, info, esc)
    on_ = sum(1 for e in vms if e.get("energia") == "encendido")
    from . import alertas_objeto as ao
    e_hv = objeto_alertas(x)
    try:
        alertas_obj = ao.contexto(g.cliente["id"], e_hv, esc)
    except Exception:  # noqa: BLE001 - la campana nunca rompe la ficha
        from flask import current_app
        current_app.logger.exception("alertas del hipervisor")
        alertas_obj = None
    from .infraestructuras import tickets_de
    return render_template("hipervisor.html", tks_infra=tickets_de(g.cliente["id"], x["nombre"]), e=e_hv, alertas_obj=alertas_obj, rep_maq=None, x=x, info=info, cap=cap, asig=asig, vms=vms, vms_on=on_, host=host, fisicos=fisicos,
                           acciones=acciones, areas=list(dict.fromkeys(a["area"] for a in acciones)), radar_h=radar_h, energia_no=energia_no,
                           prov_ctx=p_, esc=esc, emulado=_emulado(x), medicion_vieja=bool(cap) and _medicion_vieja(cap),
                           etiq=etiqueta_centro([i for i in infras if i["centro"] == x["centro"]]))


@bp.route("/hipervisores/<int:iid>/accion", methods=["POST"])
@requiere_login
def hipervisor_accion(iid):
    """Lanza una acción del hipervisor (flotante de salud o sección Acciones): ticket en AAP con su aprobación, en segundo
    plano. FaceOps no toca el anfitrión. Valida en el servidor lo mismo que muestra la ficha (publicada, certificada, aplica)."""
    from flask import request, session, url_for
    from .aap import ErrorAAP
    from .infraestructuras import lista
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    eqs_ = equipos_visibles()
    x = next((i for i in lista(g.cliente["id"], eqs_) if i["id"] == iid and i["tipo"] in TIPOS_HIPERVISOR), None)
    aid = str(request.form.get("aid") or "")
    nombre = None
    for n_, (na_, _) in ACCION_AAP.items():
        a_ = _accion_aap(g.cliente["id"], na_)[0]
        if a_ and str(a_["id"]) == aid:
            nombre = n_
            break
    if not x or not nombre:
        return {"error": "Hipervisor o acción no válidos"}, 400
    ac = _accion(x, nombre, True)
    if ac["ticket"] and ac["ticket"]["abierto"]:
        t = ac["ticket"]
        return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "existente": True}
    if ac["bloqueo"]:
        return {"error": ac["bloqueo"]}, 400
    nom_aap, extra = ACCION_AAP[nombre]
    a, _ = _accion_aap(g.cliente["id"], nom_aap)
    params = dict(extra if isinstance(extra, dict) else {}, hv_infra=x["nombre"])
    try:
        t = solicitar(g.usuario, g.cliente, a, None, params, f"{nombre} en {x['nombre']}")
    except (PermissionError, ErrorAAP, ValueError) as err:
        return {"error": str(err)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"]}


@bp.route("/hipervisores/<int:iid>/energia", methods=["POST"])
@requiere_login
def hipervisor_energia(iid):
    """Encender o apagar el anfitrión: ticket con la acción certificada y aprobación en AAP. FaceOps nunca lo hace directo."""
    from flask import request, session, url_for
    from .aap import ErrorAAP
    from .infraestructuras import lista
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    accion = request.form.get("accion", "")
    x = next((i for i in lista(g.cliente["id"], equipos_visibles()) if i["id"] == iid and i["tipo"] in TIPOS_HIPERVISOR), None)
    if not x or accion not in ("encender", "apagar"):
        return {"error": "Hipervisor o acción no válidos"}, 400
    a, motivo = _accion_aap(g.cliente["id"], ENERGIA_HIP)
    if motivo:
        return {"error": motivo}, 400
    try:
        t = solicitar(g.usuario, g.cliente, a, None, {"hv_infra": x["nombre"], "hv_accion": accion, "hv_anfitrion": x["detalle_d"].get("anfitrion") or ""},
                      f"{accion.capitalize()} el anfitrión de {x['nombre']}")
    except (PermissionError, ErrorAAP, ValueError) as err:
        return {"error": str(err)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"]}


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
    cap = dict((o or {}).get("detalle_d", {}).get("capacidad") or {})
    if cap and d_.get("pool") and d_["pool"] != o["detalle_d"].get("pool"):   # pool propio: el disco del otro no vale para este
        cap.update(disco_gb=None, disco_disp_gb=None)
    return cap


MEDICION_VIGENTE_H = 12   # una medición más vieja (o anterior a un reinicio del laboratorio) no sirve para decidir un traslado


def _medicion_vieja(cap):
    from datetime import datetime, timezone
    try:
        m_ = datetime.fromisoformat(cap["medido"])
    except (KeyError, TypeError, ValueError):
        return True
    return (datetime.now(timezone.utc) - m_).total_seconds() > MEDICION_VIGENTE_H * 3600


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
        return {"ticket": t["numero"], "fecha": texto_utc(t["fin"], vacio=""), "hacia": dst["nombre"] if dst else p_.get("mv_destino")}
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
        vieja = bool(cap) and _medicion_vieja(cap)
        if x["tipo"] == "kvm" and not cap:
            motivos.append("sin capacidad medida: re-mide el destino")
        elif x["tipo"] == "kvm" and vieja:
            motivos.append(f"medición del {(cap.get('medido') or '?')[:10]}, anterior a las últimas {MEDICION_VIGENTE_H} h: re-mide el destino")
        elif x["tipo"] == "kvm" and cap.get("disco_disp_gb") is None:
            motivos.append("disco libre sin medición: re-mide el destino")
        if cap and not vieja:
            if cap.get("cores") and cpu > cap["cores"]:
                motivos.append(f"la máquina tiene {cpu} vCPU y el anfitrión {cap['cores']} cores físicos")
            if not mismo and cap.get("ram_disp_gb") is not None and ram_gb > cap["ram_disp_gb"]:
                motivos.append(f"memoria libre {cap['ram_disp_gb']} GB < {ram_gb} GB de la máquina")
            if cap.get("disco_disp_gb") is not None and disco + 5 > cap["disco_disp_gb"]:
                motivos.append(f"disco libre {cap['disco_disp_gb']} GB < {disco + 5} GB (discos de la máquina + 5 GB de margen)")
        out.append({"id": x["id"], "nombre": x["nombre"], "tipo": x["prov"].get("nombre", x["tipo"]), "centro": x["centro"],
                    "region": x.get("region") or "", "red": d_.get("red") or (f"datacenter vSphere {d_['datacenter']}" if d_.get("datacenter") else "—"),
                    "red_libvirt": d_.get("red_libvirt") or "default", "estado": x["estado"], "vms": len(vms),
                    "cores": cap.get("cores"), "ram_libre": None if vieja else cap.get("ram_disp_gb"), "disco_libre": None if vieja else cap.get("disco_disp_gb"),
                    "medido": texto_utc(cap.get("medido"), vacio=""), "vieja": vieja, "remedir": x["tipo"] == "kvm" and x["estado"] == "conectada",
                    "mismo_anfitrion": mismo, "emulado": _emulado(x),
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


@bp.route("/inventario/<int:eid>/traslado/medir", methods=["POST"])
@requiere_login
def equipo_traslado_medir(eid):
    """Re-mide la capacidad de un destino KVM (cores, memoria y disco libre de su pool) con el «Probar acceso» del hipervisor,
    que va por AAP (FaceOps no toca el anfitrión), y devuelve los destinos actualizados."""
    from flask import request, session
    from .aap import ErrorAAP
    from .infraestructuras import probar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    eqs = equipos_visibles()
    e = next((x for x in eqs if x["id"] == eid), None)
    if not e:
        return {"error": "Equipo no visible"}, 404
    try:
        st, msg = probar(g.usuario, g.cliente, int(request.form.get("destino") or 0))
    except (ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    if st != "conectada":
        return {"error": f"No se pudo medir: {msg}"}, 400
    return destinos_traslado(g.cliente["id"], e, equipos_visibles())


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
