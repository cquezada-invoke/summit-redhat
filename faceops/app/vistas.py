"""Consola FaceOps: panel, buscador, acciones, tickets, inventario, segmentos y mapa."""
import os
from urllib.parse import quote
import json
import re
import requests
from werkzeug.security import generate_password_hash
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, flash, g, make_response, redirect, render_template, request, session, url_for

from . import requiere_login
from .aap import AAP, ErrorAAP
from .bd import ahora, auditar, ej, j, q, q1
from .buscador import buscar
from .inventario import cumplimiento, normal, politicas_de, recolectar, segmentos_de, sincronizar, skills_de, sistema_de
from .operacion import resolver_aprobacion, segmentos_aplicables, equipos_elegibles, sincronizar_catalogo, solicitar
from .permisos import equipo_visible, puede_aprobar, puede_ejecutar, puede_ver_segmento, segmentos_visibles

bp = Blueprint("consola", __name__)
SEV = {"alta": 3, "media": 2, "baja": 1}


# ------------------------------------------------------------------ tipos de periférico (agrupación del inventario)
PERIFERICOS = [("servidores", "Servidores", "servidor"), ("routers", "Routers", "router"), ("switches", "Switches", "switch"),
               ("firewalls", "Firewalls", "firewall"), ("balanceadores", "Balanceadores", "balanceador"),
               ("storage", "Storage", "almacenamiento"), ("clusters", "Clústeres de contenedores", "clúster openshift"),
               ("estaciones", "Estaciones de trabajo", "estación de trabajo"), ("otros", "Otros", "")]


CLASES = [("maquinas", "Máquinas"), ("redes", "Equipos de red"), ("almacenamiento", "Almacenamiento"), ("plataformas", "Plataformas")]


def clase_eq(e):
    """Máquinas (servidores, VM, estaciones) · equipos de red (routers, switches, firewalls, balanceadores) · almacenamiento."""
    gp = grupo_periferico(e)
    if gp == "clusters":
        return "plataformas"
    return "redes" if gp in ("routers", "switches", "firewalls", "balanceadores") else ("almacenamiento" if gp == "storage" else "maquinas")


def ambito_eq(e, amb_ubi):
    """Nube u on-premise: por el proveedor del equipo (gcp, aws, azure) o por la infraestructura donde vive."""
    if (e.get("proveedor") or "").lower() in ("gcp", "aws", "azure", "nube"):
        return "nube"
    return amb_ubi.get(e.get("ubicacion") or "", "nube" if "cloud" in (e.get("ubicacion") or "").lower() else "on-premise")


def grupo_periferico(e):
    c = (e.get("categoria") or "").lower()
    if c in ("", "servidor") and {"nginx", "haproxy", "f5-bigip"} & set(e.get("skills") or []):
        return "balanceadores"   # un servidor que cumple el rol de balanceador (p. ej. Nginx delante de los JBoss)
    if c.startswith("clúster") or c in ("kubernetes", "openshift"):
        return "clusters"   # OpenShift, Kubernetes y cualquier otro clúster de contenedores
    return next((k for k, _, cat in PERIFERICOS if cat and c == cat), "otros")


# ------------------------------------------------------------------ datos visibles para el usuario actual
def equipos_visibles():
    cid = g.cliente["id"]
    todos_segs = q("SELECT * FROM segmentos WHERE cliente_id=? ORDER BY orden, nombre", cid)
    out = []
    for e in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado' ORDER BY nombre", cid):
        e["segmentos"] = segmentos_de(e, todos_segs)
        e["skills"] = skills_de(e)
        if equipo_visible(g.perfil, e, e["segmentos"]):
            out.append(e)
    return out


def autos_visibles(todas=False):
    """Catálogo visible. Regla de prueba: una automatización sin su plan aprobado completo no está disponible en el sistema;
    solo el módulo Automatizaciones (todas=True) la muestra, en gris."""
    autos = q("SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=1 ORDER BY categoria, nombre", g.cliente["id"])
    from .pruebas import no_certificadas
    bloq = no_certificadas(g.cliente["id"])
    if todas:
        return [dict(a, _bloqueada=a["id"] in bloq) for a in autos]
    return [a for a in autos if a["id"] not in bloq]


CATEGORIAS_RED = ("router", "switch", "firewall", "balanceador")   # equipos de red: se vinculan con el usuario y la clave que se entregan


def tickets_visibles(limite=5000):
    """Los tickets que ve el usuario: por segmento de negocio y, si tiene área, solo los de su área."""
    from .aprobacion_areas import filtrar_tickets
    ts = q("SELECT t.*, a.nombre auto_nombre, a.categoria, u.nombre solicitante FROM tickets t "
           "LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id LEFT JOIN usuarios u ON u.id=t.usuario_id "
           "WHERE t.cliente_id=? ORDER BY t.id DESC LIMIT ?", g.cliente["id"], limite)
    ts = [t for t in ts if not t["segmento"] or puede_ver_segmento(g.usuario, g.cliente["id"], t["segmento"])]
    return filtrar_tickets(ts, g.usuario, g.cliente["id"])


def hallazgos_visibles(equipos):
    ids = {e["id"]: e for e in equipos}
    hs = q("SELECT * FROM hallazgos WHERE cliente_id=?", g.cliente["id"])
    out = []
    for h in hs:
        if h["equipo_id"] in ids:
            h["equipo"] = ids[h["equipo_id"]]["nombre"]
            out.append(h)
    return sorted(out, key=lambda h: (-SEV.get(h["severidad"], 0), h["equipo"]))


def auto_sugerida(sugerencia, autos):
    if not sugerencia or sugerencia in ("recolectar", "apagar"):
        return None
    s = normal({"registrar": "registrar en red hat"}.get(sugerencia, sugerencia))
    return next((a for a in autos if s in normal(a["nombre_aap"]) or s in normal(a["nombre"])), None)


# ------------------------------------------------------------------ sesión
def _clave_ok(u, clave):
    """La clave de un usuario: la local o, si es externo, la que valida su fuente."""
    from .fuentes_usuarios import autenticar_ingreso, es_externo, verificar_clave
    return bool(autenticar_ingreso(u["usuario"], clave)) if es_externo(u) else verificar_clave(u, clave)


def _entrar(u, sig=""):
    from .doble_factor import limpiar
    session.clear()
    session["uid"] = u["id"]
    session.permanent = True
    ej("UPDATE usuarios SET ultimo_ingreso=? WHERE id=?", ahora(), u["id"])
    limpiar(u["usuario"])
    auditar(u["usuario"], None, "ingresar", "sesión", {"ip": request.headers.get("X-Real-IP") or request.remote_addr,
                                                        "acceso": "público" if request.headers.get("X-FaceOps-Acceso") == "publico" else "interno"})
    if sig.startswith("/") and not sig.startswith("//") and sig.rstrip("?") not in ("/", ""):
        return redirect(request.script_root + sig)
    return redirect(url_for("consola.inicio"))


@bp.route("/definir-clave/<tok>", methods=["GET", "POST"])
def definir_clave(tok):
    """Enlace de un solo uso para definir la clave (cada instancia de FaceOps tiene los suyos; se guarda solo la huella)."""
    import hashlib
    import time as _t
    from .activacion import iniciar as _act_iniciar
    _act_iniciar()
    a = q1("SELECT * FROM activaciones WHERE token=? AND usado=0 AND vence>?", hashlib.sha256(tok.encode()).hexdigest(), _t.time())
    if not a:
        flash("El enlace no es válido, ya se usó o venció", "error")
        return redirect(url_for("consola.ingresar"))
    if request.method == "POST":
        c1, c2 = request.form.get("clave", ""), request.form.get("clave2", "")
        if len(c1) < 12 or c1 != c2:
            flash("La clave debe tener al menos 12 caracteres y coincidir en ambos campos", "error")
        else:
            from datetime import timedelta
            uq = q1("SELECT totp_activo, activo FROM usuarios WHERE usuario=?", a["usuario"]) or {}
            if uq.get("totp_activo"):   # recuperación de clave: el doble factor se conserva y se ingresa normal
                ej("UPDATE usuarios SET clave_hash=? WHERE usuario=?", generate_password_hash(c1), a["usuario"])
                ej("UPDATE activaciones SET usado=1 WHERE usuario=?", a["usuario"])
                auditar(a["usuario"], None, "clave cambiada con enlace de un solo uso", a["usuario"], {})
                flash("Clave cambiada. Ingresa con tu usuario, la clave nueva y el código de tu aplicación.", "ok")
                return redirect(url_for("consola.ingresar"))
            # además deja listo el doble factor (obligatorio desde internet): el mismo enlace sigue al enrolamiento del código
            ej("UPDATE usuarios SET clave_hash=?, activo=1, invita_hash=?, invita_expira=? WHERE usuario=?", generate_password_hash(c1),
               hashlib.sha256(tok.encode()).hexdigest(), (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds"), a["usuario"])
            ej("UPDATE activaciones SET usado=1 WHERE usuario=?", a["usuario"])
            auditar(a["usuario"], None, "clave definida con enlace de activación", a["usuario"], {})
            flash("Clave definida. Ahora activa el doble factor: ingresa tu usuario y la clave nueva y escanea el código QR.", "ok")
            return redirect(url_for("consola.activar", tok=tok))
    return render_template("definir_clave.html", usuario_act=a["usuario"])


@bp.route("/ingresar", methods=["GET", "POST"])
def ingresar():
    from .doble_factor import bloqueado, es_publico, fallo, ip_cliente
    if request.method == "GET" and g.get("usuario"):
        return redirect(url_for("consola.inicio"))   # con sesión válida no tiene sentido mostrar el login
    if request.method == "POST":
        nombre = request.form.get("usuario", "").strip().lower()
        ip = ip_cliente(request)
        if bloqueado(ip, nombre):
            auditar(nombre[:60], None, "ingreso bloqueado", "sesión", {"ip": ip})
            flash("Demasiados intentos fallidos. Espera 15 minutos.", "error")
            return render_template("ingresar.html"), 429
        from .fuentes_usuarios import autenticar_ingreso   # locales (FaceOps/HTPasswd) o externos (AD, LDAP, midPoint: los valida su fuente)
        u = autenticar_ingreso(nombre, request.form.get("clave", ""))
        if u and u.get("expira") and u["expira"] < ahora():
            auditar(nombre[:60], None, "ingreso rechazado", "sesión", {"ip": ip, "motivo": "usuario temporal vencido"})
            flash("Tu acceso temporal venció. Pide uno nuevo a un administrador.", "error")
            return render_template("ingresar.html"), 403
        if u and (g.get("bloqueo") or {}).get("bloqueado"):
            auditar(u["usuario"], None, "ingreso rechazado", "sesión", {"ip": ip, "motivo": "acceso bloqueado desde FaceAdmin"})
            return render_template("ingresar.html"), 403
        if u:
            if u.get("totp_activo"):
                session.clear()
                session["pre_uid"], session["pre_t"] = u["id"], datetime.now(timezone.utc).timestamp()
                return redirect(url_for("consola.ingresar_codigo", siguiente=request.args.get("siguiente", "")))
            if es_publico(request):
                auditar(u["usuario"], None, "ingreso rechazado", "sesión", {"ip": ip, "motivo": "acceso público sin doble factor"})
                flash("Para entrar desde internet necesitas el doble factor. Actívalo una vez desde la red interna (tu usuario → Doble factor).", "error")
                return render_template("ingresar.html"), 403
            return _entrar(u, request.args.get("siguiente", ""))
        fallo(ip, nombre)
        auditar(nombre[:60], None, "ingreso fallido", "sesión", {"ip": ip})
        flash("Usuario o clave incorrectos", "error")
    return render_template("ingresar.html")


@bp.route("/ingresar/codigo", methods=["GET", "POST"])
def ingresar_codigo():
    from .doble_factor import bloqueado, fallo, ip_cliente, verificar
    uid, t0 = session.get("pre_uid"), session.get("pre_t", 0)
    if not uid or datetime.now(timezone.utc).timestamp() - t0 > 300:
        session.clear()
        flash("Vuelve a ingresar tu usuario y clave", "error")
        return redirect(url_for("consola.ingresar"))
    u = q1("SELECT * FROM usuarios WHERE id=? AND activo=1", uid)
    if request.method == "POST" and u:
        ip = ip_cliente(request)
        if bloqueado(ip, u["usuario"]):
            session.clear()
            flash("Demasiados intentos fallidos. Espera 15 minutos.", "error")
            return redirect(url_for("consola.ingresar"))
        if verificar(u["totp_secreto"], request.form.get("codigo", "")):
            return _entrar(u, request.args.get("siguiente", ""))
        fallo(ip, u["usuario"])
        auditar(u["usuario"], None, "código de doble factor incorrecto", "sesión", {"ip": ip})
        flash("Código incorrecto", "error")
    return render_template("ingresar_codigo.html")


@bp.route("/ingresar/activar/<tok>", methods=["GET", "POST"])
def activar(tok):
    """Primer ingreso desde internet con el enlace de activación: usuario + clave, luego escanea su QR y confirma un código."""
    import hashlib
    from .doble_factor import bloqueado, fallo, ip_cliente, nuevo_secreto, uri, verificar
    h = hashlib.sha256(tok.encode()).hexdigest()
    u = q1("SELECT * FROM usuarios WHERE invita_hash=? AND activo=1", h)
    if not u or (u.get("invita_expira") or "") < ahora() or (u.get("expira") and u["expira"] < ahora()):
        flash("El enlace de activación no es válido o venció. Pide uno nuevo a un administrador.", "error")
        return redirect(url_for("consola.ingresar"))
    ip = ip_cliente(request)
    enrolar = None
    if request.method == "POST":
        if bloqueado(ip, u["usuario"]):
            flash("Demasiados intentos fallidos. Espera 15 minutos.", "error")
            return render_template("activar.html"), 429
        if request.form.get("accion") == "credenciales":
            if request.form.get("usuario", "").strip().lower() == u["usuario"] and _clave_ok(u, request.form.get("clave", "")):
                session["act_uid"], session["act_sec"] = u["id"], nuevo_secreto()
            else:
                fallo(ip, u["usuario"])
                flash("Usuario o clave incorrectos", "error")
        elif request.form.get("accion") == "codigo" and session.get("act_uid") == u["id"]:
            if verificar(session["act_sec"], request.form.get("codigo", "")):
                ej("UPDATE usuarios SET totp_secreto=?, totp_activo=1, invita_hash='', invita_expira='' WHERE id=?", session["act_sec"], u["id"])
                auditar(u["usuario"], None, "doble factor activado con enlace", "sesión", {"ip": ip})
                flash("Doble factor activado. Desde ahora FaceOps te pedirá el código de tu aplicación.", "ok")
                return _entrar(u)
            fallo(ip, u["usuario"])
            flash("El código no coincide. Revisa la hora del celular y prueba con el siguiente.", "error")
    if session.get("act_uid") == u["id"]:
        enrolar = {"secreto": session["act_sec"], "uri": uri(session["act_sec"], u["usuario"])}
    return render_template("activar.html", enrolar=enrolar, nombre=u["nombre"])


@bp.route("/contrato/activar", methods=["POST"])
@requiere_login
def contrato_activar():
    """Aprobación de la activación del contrato (solo cluster-admin): la registra FaceAdmin y desde ahí corren vigencia y cobro."""
    from .bloqueo_admin import aprobar_activacion
    if not (g.get("acceso") or {}).get("admin_total") or g.get("ver_como"):
        abort(403)
    try:
        d = aprobar_activacion(g.usuario["usuario"])
        auditar(g.usuario["usuario"], g.cliente["id"], "contrato activado", d.get("numero", ""), d)
        flash(f"Contrato {d.get('numero', '')} activado: vigencia desde {d.get('inicio', 'hoy')}", "ok")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(request.referrer or url_for("consola.inicio"))


@bp.route("/cuenta/datos")
@requiere_login
def cuenta_datos():
    """Mi perfil: solo la información del usuario (datos, origen, grupos y accesos)."""
    from . import fuentes_usuarios as fu
    from . import rbac
    u = g.usuario
    acc = rbac.acceso(u, g.cliente["id"]) if rbac.activo(g.cliente["id"]) else None
    return render_template("cuenta_datos.html", u=u, fuente=fu.etiqueta_origen(u), grupos=rbac.grupos_de(u["id"], g.cliente["id"]) if acc else [],
                           acc=acc, espacios=rbac.ESPACIOS, roles_esp=rbac.ROLES_ESPACIO)


@bp.route("/cuenta/atajos")
@requiere_login
def cuenta_atajos():
    """Atajos de teclado como página (para la ventana de «Mi perfil»)."""
    return render_template("cuenta_atajos.html")


@bp.route("/cuenta/foto", methods=["POST"])
@requiere_login
def cuenta_foto():
    """Foto del perfil: la manda el navegador ya recortada y achicada (JPEG o PNG en data URL, máx. ~200 KB)."""
    d = request.get_json(silent=True) or {}
    if d.get("_csrf") != session.get("csrf"):
        return {"error": "Sesión vencida: recarga la página"}, 400
    f = str(d.get("foto") or "")
    if f and (not re.match(r"^data:image/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+$", f) or len(f) > 280_000):
        return {"error": "Imagen no válida o muy grande"}, 400
    ej("UPDATE usuarios SET foto=? WHERE id=?", f, g.usuario["id"])
    return {"ok": True}


@bp.route("/cuenta/doble-factor", methods=["GET", "POST"])
def doble_factor():
    from .doble_factor import es_publico, nuevo_secreto, uri, verificar
    if not g.get("usuario"):
        return redirect(url_for("consola.ingresar"))
    u = q1("SELECT * FROM usuarios WHERE id=?", g.usuario["id"])
    if request.method == "POST":
        accion = request.form.get("accion")
        if accion == "activar" and session.get("totp_nuevo"):
            if verificar(session["totp_nuevo"], request.form.get("codigo", "")):
                ej("UPDATE usuarios SET totp_secreto=?, totp_activo=1 WHERE id=?", session.pop("totp_nuevo"), u["id"])
                auditar(u["usuario"], None, "doble factor activado", "sesión")
                flash("Doble factor activado: desde ahora FaceOps te pedirá el código de tu aplicación.", "ok")
                return redirect(url_for("consola.doble_factor"))
            flash("El código no coincide. Revisa la hora del celular y prueba con el código siguiente.", "error")
        elif accion == "desactivar" and u["totp_activo"]:
            if es_publico(request):
                flash("El doble factor solo se desactiva desde la red interna.", "error")
            elif verificar(u["totp_secreto"], request.form.get("codigo", "")):
                ej("UPDATE usuarios SET totp_secreto='', totp_activo=0 WHERE id=?", u["id"])
                auditar(u["usuario"], None, "doble factor desactivado", "sesión")
                flash("Doble factor desactivado.", "ok")
                return redirect(url_for("consola.doble_factor"))
            else:
                flash("Código incorrecto", "error")
    enrolar = None
    if not u["totp_activo"]:
        if es_publico(request):
            flash("El doble factor se activa desde la red interna.", "error")
        else:
            session.setdefault("totp_nuevo", nuevo_secreto())
            enrolar = {"secreto": session["totp_nuevo"], "uri": uri(session["totp_nuevo"], u["usuario"])}
    return render_template("doble_factor.html", activo=bool(u["totp_activo"]), enrolar=enrolar)


@bp.route("/salir")
def salir():
    session.clear()
    return redirect(url_for("consola.ingresar"))


@bp.route("/cliente/<int:cid>")
@requiere_login
def cambiar_cliente(cid):
    nuevo = next((c for c in g.clientes if c["id"] == cid), None)
    if nuevo:
        session["cliente_id"] = cid
    ir = request.args.get("ir", "")
    ir = ir if ir.startswith("/") and not ir.startswith("//") else url_for("consola.inicio")
    if nuevo:   # la URL lleva el código del cliente: se cambia el prefijo, no solo la sesión
        raiz = request.environ.get("faceops.raiz", request.script_root)
        resto = ir[len(raiz):] if raiz and ir.startswith(raiz + "/") else ir
        seg = resto.split("/", 2)
        if len(seg) > 1 and seg[1].lower() in current_app.extensions["prefijo_cliente"].codigos():
            resto = "/" + (seg[2] if len(seg) > 2 else "")
        ir = raiz + "/" + (nuevo["codigo"] or "").lower() + resto if (nuevo["codigo"] or "").lower() in current_app.extensions["prefijo_cliente"].codigos() else raiz + resto
    return redirect(ir)


# ------------------------------------------------------------------ panel
def valor_y_efectividad(ts, autos, dias=14):
    """Datos de los gráficos del panel: horas ahorradas por día, ahorro por servicio, manual vs automatizado y resultados."""
    from datetime import timedelta
    por_auto = {a["id"]: a for a in autos}
    hoy = datetime.now(timezone.utc).date()
    ok = [t for t in ts if t["estado"] == "exitoso"]
    serie = []
    for i in range(dias - 1, -1, -1):
        d = (hoy - timedelta(days=i)).isoformat()
        h = sum((t["valor_min"] or 0) for t in ok if (t["creado"] or "").startswith(d)) / 60
        serie.append({"dia": d[8:10] + "/" + d[5:7], "h": round(h, 1)})
    maxh = max([s["h"] for s in serie] + [0.1])
    servicios = defaultdict(lambda: {"h": 0.0, "n": 0})
    for t in ok:
        a = por_auto.get(t["automatizacion_id"]) or {}
        k = (a.get("demo") or a.get("categoria") or "Otras").split(" · ", 1)[-1]
        servicios[k]["h"] += (t["valor_min"] or 0) / 60
        servicios[k]["n"] += 1
    servicios = sorted(({"nombre": k, "h": round(v["h"], 1), "n": v["n"]} for k, v in servicios.items()), key=lambda x: -x["h"])
    efect = defaultdict(lambda: {"n": 0, "eje": 0, "apr": 0})
    for t in ts:
        if t["dur_ejecucion_s"] is None:
            continue
        e = efect[t["automatizacion_id"]]
        e["n"] += 1
        e["eje"] += t["dur_ejecucion_s"] or 0
        e["apr"] += t["dur_aprobacion_s"] or 0
    filas = []
    for aid, v in efect.items():
        a = por_auto.get(aid)
        if not a:
            continue
        filas.append({"nombre": a["nombre"], "n": v["n"], "manual": (a["minutos_manual"] or 0) * 60,
                      "eje": v["eje"] // v["n"], "apr": v["apr"] // v["n"]})
    filas.sort(key=lambda x: -x["manual"])
    escala = max([max(f["manual"], f["eje"] + f["apr"]) for f in filas] + [1])
    est = Counter(t["estado"] for t in ts if t["estado"] not in ("en_cola", "en_ejecucion", "pendiente_aprobacion"))
    total = sum(est.values()) or 1
    # productividad: del tiempo que tomaría a mano lo ejecutado con éxito, cuánto se eliminó (manual − ejecución en AAP)
    man_tot = eje_tot = 0
    for t in ok:
        a = por_auto.get(t["automatizacion_id"]) or {}
        m = (a.get("minutos_manual") or 0) * 60
        if m:
            man_tot += m
            eje_tot += min(t["dur_ejecucion_s"] or 0, m)
    prod = round(100 * (man_tot - eje_tot) / man_tot) if man_tot else 0
    horas_total = round(sum((t["valor_min"] or 0) for t in ok) / 60, 1)
    return {"productividad": prod, "ejec_total": len(ok), "horas_total": horas_total,
            "valor_total": round(horas_total * (g.cliente["costo_hora"] or 0)),
            "serie": serie, "maxh": maxh, "servicios": servicios[:8], "maxs": max([s["h"] for s in servicios] + [0.1]),
            "efect": filas[:8], "escala": escala, "resultados": est, "total": total,
            "exito": round(100 * est.get("exitoso", 0) / total)}


@bp.route("/")
@requiere_login
def inicio():
    eqs = equipos_visibles()
    hs = hallazgos_visibles(eqs)
    ts = tickets_visibles()
    autos = autos_visibles()
    mes = datetime.now(timezone.utc).strftime("%Y-%m")
    del_mes = [t for t in ts if (t["creado"] or "").startswith(mes)]
    ok_mes = [t for t in del_mes if t["estado"] == "exitoso"]
    pendientes = [t for t in ts if t["estado"] == "pendiente_aprobacion"] if puede_aprobar(g.usuario, g.cliente["id"]) else []
    sugerencias = []
    vistos = set()
    for h in hs:
        a = auto_sugerida(h["sugerencia"], autos)
        if a and (a["id"], h["titulo"]) not in vistos:
            vistos.add((a["id"], h["titulo"]))
            sugerencias.append({"hallazgo": h, "auto": a, "n": sum(1 for x in hs if x["titulo"] == h["titulo"])})
    segs = []
    for s in segmentos_visibles(g.usuario, g.cliente["id"]):
        es = [e for e in eqs if s["nombre"] in e["segmentos"]]
        segs.append({"s": s, "equipos": len(es), "hallazgos": sum(1 for h in hs if h["equipo_id"] in {e["id"] for e in es})})
    graficos = valor_y_efectividad(ts, autos)
    # Hallazgos como mapa: un recuadro por infraestructura; al hacer clic, el inventario con sus equipos con hallazgos
    peor = {}
    for h in hs:
        peor[h["equipo_id"]] = max(peor.get(h["equipo_id"], 0), SEV.get(h["severidad"], 0))
    mapa = []
    grupos = [(u, "", [e for e in eqs if (e["ubicacion"] or "Sin centro de datos") == u])
              for u in sorted({e["ubicacion"] or "Sin centro de datos" for e in eqs})]
    for n, d, es in grupos:
        if not es:
            continue
        con = [e for e in es if peor.get(e["id"])]
        mapa.append({"nombre": n, "desc": d, "n": len(es), "alta": sum(1 for e in es if peor.get(e["id"]) == 3),
                     "media": sum(1 for e in es if peor.get(e["id"]) == 2), "baja": sum(1 for e in es if peor.get(e["id"]) == 1),
                     "ok": len(es) - len(con), "q": (n if n != "Sin centro de datos" else "") + " con hallazgos",
                     "titulos": Counter(h["titulo"] for h in hs if h["equipo_id"] in {e["id"] for e in es}).most_common(3),
                     "skills": Counter(k for e in es for k in e["skills"]).most_common(6),
                     "ambientes": sorted({e["ambiente"] for e in es if e["ambiente"]}),
                     "segmentos": len({s for e in es for s in e["segmentos"]})})
    mapa.sort(key=lambda m: (-m["alta"], -m["media"], m["nombre"]))
    from .arquitectura import costo, vigentes
    visibles = {x["nombre"] for x in segmentos_visibles(g.usuario, g.cliente["id"])}
    costos_seg = sorted(({"nombre": n, **costo(r["m"], eqs)} for n, r in vigentes(g.cliente["id"]).items() if n in visibles),
                        key=lambda x: -x["mensual"])
    from .seguridad_panel import ESTADOS_POL, SEVS, completitud
    try:
        seg_ = completitud(g.cliente["id"], eqs)
    except Exception as e_:  # noqa: BLE001 - el panel no se cae si una fuente de seguridad falla
        current_app.logger.warning("completitud de seguridad: %s", e_)
        seg_ = None
    from .panel_extra import creditos as creditos_panel, productividad as productividad_area
    pa = productividad_area(g.cliente["id"], ts, eqs, autos)
    from .panel_extra import aprobaciones_pendientes
    try:
        apr = aprobaciones_pendientes(g.cliente["id"], ts)
    except Exception as e_:  # noqa: BLE001
        current_app.logger.warning("aprobaciones pendientes del panel: %s", e_)
        apr = None
    try:
        cr = creditos_panel(g.cliente, eqs)
    except Exception as e_:  # noqa: BLE001
        current_app.logger.warning("créditos del panel: %s", e_)
        cr = None
    try:
        from .topologia import estrella_parque
        estrella = estrella_parque(g.cliente, eqs, hs)
    except Exception as e_:  # noqa: BLE001
        current_app.logger.warning("radar de salud del panel: %s", e_)
        estrella = None
    return render_template("inicio.html", estrella=estrella, apr=apr, pa=pa, cr=cr, seg=seg_, estados_pol=ESTADOS_POL, sevs=SEVS, costos_seg=costos_seg, equipos=eqs, hallazgos=hs, tickets=ts[:8], pendientes=pendientes, g_=graficos, mapa=mapa,
                           conteo=dict(Counter(h["severidad"] for h in hs)), ejecuciones_mes=len(del_mes),
                           horas_mes=round(sum(t["valor_min"] or 0 for t in ok_mes) / 60, 1),
                           valor_mes=sum((t["valor_min"] or 0) / 60 * (g.cliente["costo_hora"] or 0) for t in ok_mes),
                           autos=autos, sugerencias=sugerencias[:6], segmentos=segs)


@bp.route("/buscar")
@requiere_login
def buscar_vista():
    # El buscador vive solo en Automatizaciones
    return redirect(url_for("consola.automatizaciones", q=request.args.get("q", "")))


# ------------------------------------------------------------------ automatizaciones
@bp.route("/automatizaciones")
@requiere_login
def automatizaciones():
    # Administración › Automatizaciones (admin=1) muestra todo el catálogo, con las no certificadas en gris; el resto del sistema solo las disponibles
    modo_admin = bool(g.usuario.get("es_admin") and request.args.get("admin"))
    autos = autos_visibles(todas=modo_admin)
    tipo = request.args.get("tipo")
    texto = request.args.get("q", "").strip()
    encontradas, filtros_q = None, {}
    if texto:
        filtros_q, res = buscar(texto, g.cliente["id"], autos, [], [], [])
        encontradas = res["automatizaciones"]
    if tipo in ("lectura", "escritura"):
        autos = [a for a in autos if a["tipo"] == tipo]
    usos = Counter(t["automatizacion_id"] for t in q("SELECT automatizacion_id FROM tickets WHERE cliente_id=?", g.cliente["id"]))
    from .areas import SECCIONES as AREAS, seccion_de   # agrupadas como el menú de la izquierda
    from .economia import estadisticas, metricas
    est_autos = estadisticas(g.cliente["id"])
    por_area = defaultdict(list)
    probadas_ = {r["automatizacion_id"] for r in q("SELECT DISTINCT automatizacion_id FROM tickets WHERE cliente_id=? AND estado='exitoso'", g.cliente["id"])}
    for a in sorted(autos, key=lambda x: (x.get("demo") or "~", x["orden"] if x.get("orden") is not None else 999, x["nombre"])):
        a["usos"] = usos.get(a["id"], 0)
        a["puede"], a["por_que"] = puede_ejecutar(g.usuario, g.cliente["id"], a)
        a["proposito"] = j(a.get("guia"), {}).get("proposito", "")
        a["area"] = seccion_de(a)
        a["met"] = metricas(a, est_autos)
        a["probada"] = _certificada(a)
        por_area[a["area"]].append(a)
    area = request.args.get("area", "")
    areas = [{"clave": k, "nombre": n, "desc": d_, "autos": por_area[k]} for k, n, d_ in AREAS if (por_area.get(k) or k == "nube") and (not area or k == area)]
    for ar_ in areas:   # dentro de cada categoría, agrupadas por tecnología (lo que va antes de « · » en su nombre de AAP)
        tec_ = defaultdict(list)
        for x_ in ar_["autos"]:
            t_ = (x_.get("nombre_aap") or "").split(" · ")[0].strip() if " · " in (x_.get("nombre_aap") or "") else "General"
            tec_[{"DC": "Plataformas", "FaceOps": "FaceOps", "Sistema": "Sistema operativo", "Nube": "Google Cloud (GCP)", "GCP": "Google Cloud (GCP)"}.get(t_, t_)].append(x_)
        ar_["tecnologias"] = sorted(tec_.items(), key=lambda kv: (kv[0] == "General", kv[0].lower()))
    # automatizaciones planificadas (cajas grises «por implementar»), en su grupo dentro de cada categoría
    import yaml as _yaml
    try:
        plan_ = _yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "automatizaciones-planificadas.yml"), encoding="utf-8")) or []
    except OSError:
        plan_ = []
    for ar_ in areas:
        grupos_ = [{"nombre": t_, "autos": xs_, "plan": []} for t_, xs_ in ar_["tecnologias"]]
        for p_ in plan_:
            if p_.get("area") != ar_["clave"] or tipo or texto:
                continue
            gr_ = next((x for x in grupos_ if x["nombre"] == p_.get("grupo")), None)
            if not gr_:
                gr_ = {"nombre": p_.get("grupo") or "Planificadas", "autos": [], "plan": []}
                grupos_.append(gr_)
            gr_["plan"].append(p_)
        ar_["grupos"] = grupos_
    if encontradas is not None:
        por_id = {a["id"]: a for v in por_area.values() for a in v}
        encontradas = [por_id[a["id"]] for a in encontradas if a["id"] in por_id]
    return render_template("automatizaciones.html", areas=areas, area=area, tipo=tipo, modo_admin=modo_admin, total=len(autos), bloqueadas_n=sum(1 for x in autos if x.get("_bloqueada")), texto=texto,
                           encontradas=encontradas, fq=filtros_q, conteo={k: len(v) for k, v in por_area.items()}, AREAS=AREAS)


def __met(a):
    from .economia import estadisticas, metricas
    return metricas(a, estadisticas(g.cliente["id"]))


def _cves_equipo(e):
    from .cves import por_equipo
    return por_equipo(g.cliente["id"], [e]).get(e["id"])


def _politicas_equipo(e):
    from .politicas_seg import tablero
    _, por = tablero(g.cliente["id"], [e])
    return por.get(e["id"], [])


def _autos_compatibles(e):
    """Automatizaciones que se pueden ejecutar sobre este equipo, por área (compatibilidad: sistema, tipo, red, producto)."""
    from .areas import AREAS, area_de
    from .compatibilidad import compatible
    por = {}
    for a in autos_visibles():
        if (a["usa_limite"] or a.get("por_inventario")) and compatible(a, e)[0]:
            por.setdefault(area_de(a), []).append(a)
    return [(n, por[k]) for k, n, _ in AREAS if por.get(k)]


def _compatibles(a):
    """Máquinas del inventario (que el usuario ve) sobre las que actúa la automatización. Vacío si crea máquinas nuevas
    o si no opera sobre equipos del inventario (sin criterios de compatibilidad ni selección de equipos)."""
    from .compatibilidad import compatible, de
    c = de(a)
    if c.get("crea") or not (a.get("usa_limite") or any(c.get(k) for k in ("sistemas", "tipos", "red", "skills", "proveedor"))
                             or any(x.get("tipo") in ("equipo", "equipos") for x in j(a.get("campos"), []))):
        return []
    return [e for e in equipos_visibles() if e["estado"] != "retirado" and compatible(a, e)[0]]


def _prod_auto(a):
    """Productividad de la acción en este cliente: factor (a mano ÷ espera de aprobación + ejecución), tiempos medios y áreas."""
    from .aprobacion_areas import area_de, grupos_de, nombre
    ts = q("SELECT estado, unidades, dur_ejecucion_s, dur_aprobacion_s FROM tickets WHERE cliente_id=? AND automatizacion_id=?", g.cliente["id"], a["id"])
    ok = [t for t in ts if t["estado"] == "exitoso"]
    man = sum((a["minutos_manual"] or 0) * 60 * (t["unidades"] or 1) for t in ok)
    proc = sum((t["dur_ejecucion_s"] or 0) + (t["dur_aprobacion_s"] or 0) for t in ok)
    aps = [t["dur_aprobacion_s"] for t in ts if t["dur_aprobacion_s"] is not None]
    med = sum(aps) / len(aps) if aps else None
    gs = grupos_de(a)
    cadena = " → ".join(" + ".join(nombre(x) for x in g_) for g_ in gs)
    return {"factor": round(man / proc, 1) if proc else None, "n_aprob": len(aps),
            "aprob_txt": "—" if med is None else (f"{med / 60:.0f} min" if med < 3600 else (f"{med / 3600:.1f} h" if med < 86400 else f"{med / 86400:.1f} días")),
            "exito": round(100 * len(ok) / len([t for t in ts if t["estado"] in ("exitoso", "fallido")])) if any(t["estado"] in ("exitoso", "fallido") for t in ts) else None,
            "area": nombre(area_de(a)), "cadena": cadena, "areas": list(dict.fromkeys([nombre(area_de(a))] + [nombre(x) for g_ in gs for x in g_]))}


def _refs_auto(a):
    """Enlaces a la acción en AAP y a su repositorio Git (y commit) para la ficha."""
    tec = j(a.get("tecnico"), {})
    out = {"aap": None, "git": None, "commit": None, "playbook": None, "inventario": None,
           "readme": git_publico(f"http://192.168.122.1:3000/invoke/faceops-automatizaciones/src/branch/main/{a['readme_ruta']}") if a.get("readme_ruta") else None}
    try:
        from .aap import AAP
        pub = AAP.de_cliente(g.cliente).publica
        if a.get("aap_id") and a["aap_tipo"] in ("job", "workflow"):
            out["aap"] = f"{pub}/execution/templates/{'job-template' if a['aap_tipo'] == 'job' else 'workflow-job-template'}/{a['aap_id']}/details"
    except Exception:  # noqa: BLE001
        pass
    out["partes"] = _partes_auto(a, tec)
    repo = (tec.get("repositorio") or "").strip()
    if not repo.startswith("http"):   # workflow o compuesta: el repositorio de las plantillas que la forman
        repo = next((p_["repo"] for p_ in out["partes"] if p_.get("repo")), "")
    out["repo_txt"] = repo
    if repo.startswith("http"):
        base = git_publico(repo[:-4] if repo.endswith(".git") else repo)
        out["git"] = base
        if tec.get("commit"):
            out["commit"] = f"{base}/commit/{tec['commit']}"
        if tec.get("playbook"):   # el playbook mismo, en la versión que usa AAP
            rama = tec.get("rama") if tec.get("rama") and tec.get("rama") != "por defecto" else "main"
            out["playbook"] = f"{base}/src/{'commit/' + tec['commit'] if tec.get('commit') else 'branch/' + rama}/{tec['playbook']}"
    inv = tec.get("inventario") or a.get("inventario")
    if inv and out["aap"]:
        try:
            from .aap import AAP
            cli = AAP.de_cliente(g.cliente)
            r = cli.get("inventories/", name=inv).get("results") or []
            if r:
                out["inventario"] = f"{cli.publica}/execution/infrastructure/inventories/inventory/{r[0]['id']}/hosts"
        except Exception:  # noqa: BLE001
            pass
    return out


def _partes_auto(a, tec, prof=0):
    """Plantillas de AAP que forman un workflow o una acción compuesta de FaceOps, con su playbook y repositorio en Git."""
    nombres = []
    if tec.get("compuesta"):
        nombres = [(v.get("automatizacion"), None, k) for k, v in tec["compuesta"].items() if v.get("automatizacion")]
    elif a.get("aap_tipo") == "workflow":
        nombres = [(n["nombre"], n.get("ujt_id"), None) for n in tec.get("nodos") or [] if n.get("tipo") == "plantilla"]
    out, vistos = [], set()
    try:
        from .aap import AAP
        pub = AAP.de_cliente(g.cliente).publica
    except Exception:  # noqa: BLE001
        pub = ""
    for nombre, ujt, accion in nombres:
        if nombre in vistos:
            continue
        vistos.add(nombre)
        b = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], nombre)
        bt = j(b.get("tecnico"), {}) if b else {}
        if b and (bt.get("compuesta") or b["aap_tipo"] == "workflow") and prof < 2:
            out.append({"nombre": nombre, "url": f"{pub}/execution/templates/workflow-job-template/{b['aap_id']}/details" if pub and b["aap_id"] else None,
                        "tipo": "workflow", "auto_id": b["id"], "accion": accion, "nivel": prof})
            out += _partes_auto(b, bt, prof + 1)
            continue
        pid = (b or {}).get("aap_id") or ujt
        repo = (bt.get("repositorio") or "").strip()
        url_pb = None
        if repo.startswith("http") and bt.get("playbook"):
            base = git_publico(repo[:-4] if repo.endswith(".git") else repo)
            url_pb = f"{base}/src/{'commit/' + bt['commit'] if bt.get('commit') else 'branch/main'}/{bt['playbook']}"
        out.append({"nombre": nombre, "tipo": "plantilla", "auto_id": b["id"] if b else None, "repo": repo, "accion": accion, "nivel": prof,
                    "url": f"{pub}/execution/templates/job-template/{pid}/details" if pub and pid else None,
                    "playbook": bt.get("playbook"), "url_playbook": url_pb})
    return out


def _origen_links(o):
    """Agrega los enlaces que abre el navegador: el repositorio y su rama en Gitea."""
    web = git_publico(re.sub(r"\.git$", "", o.get("repo") or ""))
    return dict(o, web=web, rama_web=f"{web}/src/branch/{o.get('rama') or 'main'}" if web else "")


def git_publico(url):
    """URL de Gitea que abre el navegador del usuario (la interna de la VM se publica por el HTTPS de FaceOps)."""
    pub = os.environ.get("FACEOPS_GIT_PUBLICA", "").rstrip("/")
    if not pub:
        return url
    return re.sub(r"^https?://[^/]+(:\d+)?(/git(?=/))?", pub, url)


@bp.route("/automatizaciones/<int:aid>", methods=["GET", "POST"])
@requiere_login
def automatizacion(aid):
    a = q1("SELECT * FROM automatizaciones WHERE id=? AND cliente_id=?", aid, g.cliente["id"])
    if not a:
        abort(404)
    if a["nombre_aap"] == "FaceOps · Nuevo segmento de negocio":  # se pide adjuntando la arquitectura
        return redirect(url_for("consola.arquitectura_nueva"))
    from .pruebas import no_certificadas
    if a["id"] in no_certificadas(g.cliente["id"]) and not g.usuario.get("es_admin"):   # sin plan aprobado no existe para el resto
        abort(404)
    puede, por_que = puede_ejecutar(g.usuario, g.cliente["id"], a)
    segs = segmentos_aplicables(g.usuario, g.cliente["id"], a) if a["usa_limite"] else []
    campos = j(a["campos"], [])
    if request.method == "GET":  # prellenado desde otra pantalla (p. ej. el mapa): ?v_<variable>=valor
        for c in campos:
            if request.args.get("v_" + c["var"]):
                c["defecto"] = request.args.get("v_" + c["var"])
    elegibles = equipos_elegibles(g.usuario, g.cliente["id"], a) if any(c["tipo"] in ("equipo", "equipos") for c in campos) else []
    jboss_eqs = []
    if any(c["tipo"] in ("jboss_origen", "jboss_destino") for c in campos):   # servidores con JBoss (inventario recolectado)
        from .identidad import codigo as _cod
        from .salud import encendido as _enc
        for e_ in equipos_visibles():
            jb_ = j(e_["datos"], {}).get("jboss") or []
            if jb_ and _enc(e_) == "encendido":   # solo servidores encendidos: la evaluación y la migración entran a ellos
                jboss_eqs.append({"id": e_["id"], "nombre": e_["nombre"], "cod": _cod(e_), "ip": e_.get("ip") or "", "ambiente": e_.get("ambiente") or "",
                                  "origen": [x for x in jb_ if x.get("familia") != "eap8"], "eap8": [x for x in jb_ if x.get("familia") == "eap8"]})
    if request.method == "POST":
        params, secretos = {}, {}
        validos = {e["nombre"] for e in elegibles}
        for c in campos:
            v = request.form.get("v_" + c["var"], "").strip()
            if c["tipo"] in ("multiselect", "equipos", "jboss_apps"):
                v = [x for x in request.form.getlist("v_" + c["var"]) if x]
            if c["tipo"] == "jboss_origen" and v and v not in {x["nombre"] for x in jboss_eqs if x["origen"]}:
                flash(f"«{c['etiqueta']}»: elige un servidor con JBoss de tu inventario", "error")
                return redirect(request.url)
            if c["tipo"] == "jboss_destino" and v and v not in {x["nombre"] for x in jboss_eqs if x["eap8"]}:
                flash(f"«{c['etiqueta']}»: el destino debe tener JBoss EAP 8", "error")
                return redirect(request.url)
            if c["tipo"] == "jboss_destino" and not v and request.form.get("v_mj_modo") == "migrar":
                flash("Para migrar elige el servidor de destino (JBoss EAP 8)", "error")
                return redirect(request.url)
            if c["tipo"] in ("equipo", "equipos") and any(x not in validos for x in ([v] if isinstance(v, str) and v else v or [])):
                flash(f"«{c['etiqueta']}»: elige equipos de tus segmentos", "error")
                return redirect(request.url)
            if c["tipo"] == "segmento" and v:
                s_ok = {s["nombre"]: s for s in segmentos_visibles(g.usuario, g.cliente["id"])}
                fam = "windows" if str(request.form.get("v_hab_so", "")).startswith("windows") else "linux"
                if v not in s_ok or (s_ok[v].get("sistema") and s_ok[v]["sistema"] != fam):
                    flash(f"«{c['etiqueta']}»: elige un segmento tuyo y compatible con el sistema", "error")
                    return redirect(request.url)
            if c["tipo"] == "ip" and v and not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}(/\d{1,2})?", v):
                flash(f"«{c['etiqueta']}» debe ser una IP (ej. 10.10.10.10)", "error")
                return redirect(request.url)
            if c["tipo"] == "integer" and v != "":
                v = int(v)
            if c["tipo"] == "infraestructura" and v and not q1("SELECT 1 FROM infraestructuras WHERE cliente_id=? AND nombre=?", g.cliente["id"], v):
                flash(f"«{c['etiqueta']}»: elige una infraestructura incorporada", "error")
                return redirect(request.url)
            if c["tipo"] == "manifiesto" and v:
                import yaml as _yaml
                try:
                    list(_yaml.safe_load_all(v))
                except _yaml.YAMLError as e:
                    flash(f"«{c['etiqueta']}»: el YAML no es válido ({str(e).splitlines()[0][:160]})", "error")
                    return redirect(request.url)
                if len(v) > 200_000 or re.search(r'"auths"|pullSecret\s*:\s*\S|BEGIN [A-Z ]*PRIVATE KEY|client_secret|clientSecret', v):
                    flash(f"«{c['etiqueta']}»: no incluya el pull secret, llaves ni claves (vienen de credenciales cifradas de AAP) y que pese menos de 200 KB", "error")
                    return redirect(request.url)
            if c["tipo"] == "archivo":   # un archivo (p. ej. el manifiesto de Satellite): viaja solo a AAP, en base64, como secreto
                fa = request.files.get("v_" + c["var"])
                if fa and fa.filename:
                    import base64 as _b64
                    datos_ = fa.read(5 * 1024 * 1024 + 1)
                    if len(datos_) > 5 * 1024 * 1024:
                        flash(f"«{c['etiqueta']}»: el archivo pesa más de 5 MB", "error")
                        return redirect(request.url)
                    if c.get("acepta") and not fa.filename.lower().endswith(tuple(x.strip() for x in c["acepta"].split(","))):
                        flash(f"«{c['etiqueta']}»: debe ser {c['acepta']}", "error")
                        return redirect(request.url)
                    secretos[c["var"]] = _b64.b64encode(datos_).decode()
                    params[c["var"]] = f"{fa.filename[:80]} ({round(len(datos_) / 1024)} KB)"
                elif c.get("requerido"):
                    flash(f"Falta «{c['etiqueta']}»", "error")
                    return redirect(request.url)
                continue
            if v not in ("", []) and c["tipo"] == "password":
                secretos[c["var"]] = v
                params[c["var"]] = "••••••"
            elif v not in ("", []):
                params[c["var"]] = v
            elif c.get("requerido"):
                flash(f"Falta «{c['etiqueta']}»", "error")
                return redirect(request.url)
        if params.get("hab_infraestructura"):   # crear máquinas: la infraestructura elegida define el destino; nace sin segmento
            inf_ = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND nombre=?", g.cliente["id"], params["hab_infraestructura"])
            if not inf_ or inf_["tipo"] not in ("kvm", "gcp", "vmware"):
                flash("En esa infraestructura FaceOps aún no crea máquinas (hoy: KVM, VMware vSphere y Cloud GCP)", "error")
                return redirect(request.url)
            params["hab_destino"] = {"gcp": "gcp", "vmware": "vmware"}.get(inf_["tipo"], "onpremise")
            params["hab_ubicacion"] = inf_["ubicacion"]
            if inf_["tipo"] == "vmware":   # el puerto del vCenter vive en la infraestructura (la credencial de AAP solo trae la dirección)
                from .infraestructuras import _vc
                params["hab_vc_puerto"] = str(_vc(j(inf_.get("detalle"), {}).get("host", ""))[1])
            params.pop("hab_segmento", None)
        try:
            t = solicitar(g.usuario, g.cliente, a, request.form.get("segmento"), params, request.form.get("motivo", "").strip(), secretos,
                          {"ambientes": [x for x in request.form.getlist("f_ambiente") if x][:1], "infraestructura": request.form.getlist("f_infra")})
            from .operacion import requiere_aprobacion
            amb = (j(t["filtros"], {}).get("ambientes") or [None])[0]
            flash(f"{t['numero']} creado y enviado a AAP" + (" (esperará la aprobación en AAP)" if requiere_aprobacion(a, amb) else ""), "ok")
            return redirect(url_for("consola.ticket", tid=t["id"]))
        except PermissionError as e:
            flash(str(e), "error")
        except ErrorAAP as e:
            flash(f"AAP rechazó la ejecución: {e}", "error")
    from .operacion import conf_servicenow
    sn = conf_servicenow(a["nombre_aap"]) if g.cliente.get("tickets_tipo") == "servicenow" else None
    from .economia import beneficio
    from .flujo import plan as plan_flujo, uso_mensual
    from .objetos_aap import de as objetos_aap
    from .pruebas import estado as estado_prueba, plan_de, calidad as calidad_de
    return render_template("automatizacion.html", prueba=estado_prueba(dict(a, cliente_id=g.cliente["id"])), plan_prueba=plan_de(a["nombre_aap"]),
                           calidad=calidad_de(dict(a, cliente_id=g.cliente["id"])), jboss_eqs=jboss_eqs, objetos=objetos_aap(g.cliente, a), flujo_a=plan_flujo(a, g.cliente["id"]), prod=_prod_auto(a), uso=uso_mensual(g.cliente["id"], aid), refs=_refs_auto(a),
                           compat_eqs=_compatibles(a), met=__met(a), ben=beneficio(a), a=a, campos=campos, puede=puede, por_que=por_que, elegibles=elegibles,
                           segs_negocio=segmentos_visibles(g.usuario, g.cliente["id"]),
                           infras_cli=q("SELECT nombre, tipo, region, ubicacion FROM infraestructuras WHERE cliente_id=? ORDER BY id", g.cliente["id"]) if any(c["tipo"] == "infraestructura" for c in campos) else [],
                           guia=j(a.get("guia"), {}), tec=j(a.get("tecnico"), {}), sn=sn,
                           segmentos=segs)


# ------------------------------------------------------------------ tickets
@bp.route("/tickets")
@requiere_login
def tickets():
    ts = tickets_visibles(500)
    est = request.args.get("estado")
    if est:
        ts = [t for t in ts if t["estado"] == est]
    ids = {int(x) for x in request.args.get("ids", "").split(",") if x.strip().isdigit()}
    if ids:   # desde el control de deuda: los tickets de una actividad
        ts = [t for t in ts if t["id"] in ids]
    texto = request.args.get("q", "").strip()
    ent = {}
    if texto:
        from .buscador import buscar_tickets
        ent, ts = buscar_tickets(texto, ts)
    if request.headers.get("X-Requested-With") == "fetch":
        return render_template("tickets.html", tickets=ts, estado=est, texto=texto, ent=ent, solo_tabla=True)
    return render_template("tickets.html", tickets=ts, estado=est, texto=texto, ent=ent)


def _maquinas_ticket(t):
    """Máquinas del inventario que toca el ticket (para enlazarlas): las del límite y las que nombran sus parámetros
    o su resultado (p. ej. las que creó «Habilitar servidor»)."""
    eqs = q("SELECT id, nombre, ip, datos, estado FROM equipos WHERE cliente_id=?", g.cliente["id"])
    lim = [x for x in (t.get("limite") or "").split(":") if x and x != "*"]
    texto = " ".join(str(t.get(k) or "") for k in ("parametros", "resultado", "motivo"))
    out = []
    for e in eqs:
        if e["nombre"] in lim or (len(e["nombre"]) > 3 and re.search(r"(?<![\w-])" + re.escape(e["nombre"]) + r"(?![\w-])", texto)):
            out.append(e)
    orden = {n: i for i, n in enumerate(lim)}
    return sorted(out, key=lambda e: (orden.get(e["nombre"], 999), e["nombre"]))


def _flujo_ticket(t, a, detalle=False):
    if not a:
        return None
    from .flujo import en_vivo, plan
    cli, avance, vig = None, None, None
    if t.get("aap_job_id") and (t["estado"] in ("en_ejecucion", "pendiente_aprobacion", "en_cola") or (detalle and t["estado"] in ("fallido", "exitoso", "cancelado"))):
        try:
            from .aap import AAP
            from .avance import repartir, vigilancia
            cli = AAP.de_cliente(g.cliente)
            if t["estado"] in ("en_ejecucion", "fallido", "exitoso", "cancelado"):
                avance = repartir(cli, t, a, plan(a))
            if t["estado"] == "en_ejecucion":
                vig = vigilancia(cli, t)
        except Exception:  # noqa: BLE001
            cli = cli or None
    return en_vivo(t, a, cli, avance, vig)


@bp.route("/atajos", methods=["POST"])
@requiere_login
def atajos_guardar():
    """Guarda los atajos de teclado del usuario (o los restablece)."""
    if request.form.get("_csrf") != session.get("csrf"):
        abort(400)
    from .atajos import validar
    if request.form.get("restablecer"):
        ej("UPDATE usuarios SET atajos='{}' WHERE id=?", g.usuario["id"])
        return {"ok": True}
    try:
        d = validar(json.loads(request.form.get("atajos") or "{}"))
    except (ValueError, TypeError) as x:
        return {"error": str(x)}, 400
    ej("UPDATE usuarios SET atajos=? WHERE id=?", json.dumps(d), g.usuario["id"])
    return {"ok": True}


@bp.route("/tickets/<int:tid>/flujo")
@requiere_login
def ticket_flujo(tid):
    """Flujo del ticket en vivo (lo pide la página del ticket cada pocos segundos)."""
    t = q1("SELECT * FROM tickets WHERE id=? AND cliente_id=?", tid, g.cliente["id"])
    from .aprobacion_areas import filtrar_tickets
    if not t or not filtrar_tickets([t], g.usuario, g.cliente["id"]):
        abort(404)
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    mac = current_app.jinja_env.get_template("_macros.html").module
    from .aprobacion_areas import puede_aprobar as puede_aprobar_etapa
    aprobable = bool(a) and t["estado"] == "pendiente_aprobacion" and bool(puede_aprobar_etapa(g.usuario, g.cliente["id"], t, a))
    return {"estado": t["estado"], "aprobable": aprobable, "html": str(mac.flujo(_flujo_ticket(t, a) or [], True))}


@bp.route("/tickets/<int:tid>/caja/<pos>")
@requiere_login
def ticket_caja(tid, pos):
    """Log de una caja del flujo del ticket (panel flotante a la derecha; se refresca mientras el ticket avanza)."""
    t = q1("SELECT t.*, u.nombre solicitante FROM tickets t LEFT JOIN usuarios u ON u.id=t.usuario_id WHERE t.id=? AND t.cliente_id=?", tid, g.cliente["id"])
    from .aprobacion_areas import filtrar_tickets
    if not t or not filtrar_tickets([t], g.usuario, g.cliente["id"]):
        abort(404)
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    cols = _flujo_ticket(t, a, detalle=True) or []
    n = next((x for c_ in cols for x in c_ if x.get("pos") == pos), None)
    if not n:
        abort(404)
    pasos, info, vig, web_aap, jobs = [], [], {}, "", []
    from .aap import AAP
    try:
        cli = AAP.de_cliente(g.cliente)
        web_aap = cli.publica
    except Exception:  # noqa: BLE001
        cli = None
    if n["tipo"] == "inicio":
        info = [("Ticket", t["numero"]), ("Creado", (t["creado"] or "")[:19].replace("T", " ")), ("Solicitante", t.get("solicitante") or "—"),
                ("Motivo", t["motivo"] or "—"), ("Equipos", (t["limite"] or "los que define la acción").replace(":", ", "))]
    elif n["tipo"] == "aprobacion":
        from .aprobacion_areas import dias, etapas
        f = next((x for x in etapas(t, a) if x["area"] == n.get("area")), None)
        if f:
            info = [("Área", n["titulo"].replace("Aprueba ", "")), ("Estado", f["estado"]), ("Quién", f.get("quien") or "aún nadie"),
                    ("Desde", (f.get("inicio") or "")[:19].replace("T", " ")), ("Espera", f"{dias(f) * 24:.1f} h")]
        info.append(("Quiénes pueden aprobar", n.get("roles") or "—"))
        if t["estado"] == "pendiente_aprobacion":
            info.append(("Nota", "El ticket espera solo esta aprobación: es lo único que puede detenerlo. Las aprobaciones no vencen."))
    elif n["tipo"] == "tarea" and cli and t.get("aap_job_id"):
        from .avance import repartir, vigilancia
        from .flujo import plan
        av = repartir(cli, t, a, plan(a))
        if av.get("por_nodo"):
            x = av["por_nodo"].get(n.get("nodo_aap") or "", {})
            pasos, jobs = x.get("pasos", []), [x["job"]] if x.get("job") else []
        elif av.get("tramos"):
            tr = av["tramos"][min(n.get("_i", 0), len(av["tramos"]) - 1)]
            pasos, jobs = tr["pasos"], av.get("jobs", [])
        if t["estado"] == "en_ejecucion" and n.get("estado") == "curso":
            vig = vigilancia(cli, t)
    elif n["tipo"] == "fin":
        info = [("Estado", t["estado"].replace("_", " ")), ("Terminó", (t["fin"] or "—")[:19].replace("T", " ")), ("Resultado", t["resultado"] or "—")]
    resp = make_response(render_template("_caja.html", n=n, t=t, pasos=pasos, info=info, vig=vig, web_aap=web_aap, jobs=jobs))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/tickets/<int:tid>", methods=["GET", "POST"])
@requiere_login
def ticket(tid):
    t = q1("SELECT t.*, u.nombre solicitante, u.usuario solicitante_usuario, ap.nombre aprobador FROM tickets t "
           "LEFT JOIN usuarios u ON u.id=t.usuario_id LEFT JOIN usuarios ap ON ap.id=t.aprobado_por "
           "WHERE t.id=? AND t.cliente_id=?", tid, g.cliente["id"])
    if not t or (t["segmento"] and not puede_ver_segmento(g.usuario, g.cliente["id"], t["segmento"])):
        abort(404)
    from .aprobacion_areas import filtrar_tickets
    if not filtrar_tickets([t], g.usuario, g.cliente["id"]):   # de otra área
        abort(404)
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    from .solicitud_segmento import SEGMENTO_NUEVO
    es_segmento = bool(a) and a["nombre_aap"] == SEGMENTO_NUEVO
    if request.method == "POST":
        if request.form.get("_csrf") != session.get("csrf"):
            abort(400)
        aprobar_ = request.form.get("accion") == "aprobar"
        if es_segmento:  # doble aprobación: cada etapa la responde su rol; Infraestructura deja su decisión de recursos
            from .aap import AAP
            from .permisos import tiene_rol
            from .solicitud_segmento import de_ticket, decision_de_formulario, etapa
            et = etapa(AAP.de_cliente(g.cliente), t)
            if not et or not tiene_rol(g.usuario, g.cliente["id"], et):
                abort(403)
            if not aprobar_ and not request.form.get("comentario", "").strip():
                flash("Al rechazar, deja el motivo en el comentario", "error")
                return redirect(url_for("consola.ticket", tid=tid))
            if et == "infraestructura" and aprobar_:
                r = de_ticket(t)
                d = decision_de_formulario(r["m"], request.form)
                ej("UPDATE arquitecturas SET decision=? WHERE id=?", json.dumps(d, ensure_ascii=False), r["id"])
                auditar(g.usuario["usuario"], g.cliente["id"], "decisión de infraestructura", t["numero"], d)
        else:
            from .aprobacion_areas import puede_aprobar as puede_aprobar_etapa
            if not puede_aprobar_etapa(g.usuario, g.cliente["id"], t, a):
                abort(403)
            if not aprobar_ and not request.form.get("comentario", "").strip():
                flash("Al rechazar, deja el motivo en el comentario", "error")
                return redirect(url_for("consola.ticket", tid=tid))
        try:
            resolver_aprobacion(g.usuario, tid, aprobar_, request.form.get("comentario", ""))
            flash("Respuesta registrada", "ok")
        except ErrorAAP as e:
            flash(f"AAP no aceptó la respuesta: {e}", "error")
        return redirect(url_for("consola.ticket", tid=tid))
    url_aap = url_aprob = None
    bitacora, web_aap = [], ""
    if t["aap_job_id"]:
        from .aap import AAP
        from .bitacora import construir
        try:
            cli = AAP.de_cliente(g.cliente)
            url_aap = cli.url_trabajo(t["aap_job_tipo"], t["aap_job_id"])
            url_aprob = cli.url_trabajo(t["aap_job_tipo"], t["aap_job_id"])
            web_aap = cli.publica
            bitacora = construir(cli, t)
        except ErrorAAP:
            pass
    from .operacion import conector
    url_sn = conector(g.cliente).enlace(t["externo_ref"])
    from .operacion import Odoo as _Odoo
    url_odoo = _Odoo(g.cliente).enlace(t.get("odoo_ref")) if (j(g.cliente.get("tickets_conf"), {}).get("odoo") or {}).get("url") else None
    traza = q("SELECT * FROM auditoria WHERE cliente_id=? AND objeto=? ORDER BY id", g.cliente["id"], t["numero"])
    sol = None
    from .aprobacion_areas import actual as etapa_actual, dias as dias_etapa, nombre as nombre_area, puede_aprobar as puede_aprobar_etapa, etapas as etapas_ticket
    aprobar_ok = t["estado"] == "pendiente_aprobacion" and puede_aprobar_etapa(g.usuario, g.cliente["id"], t, a)
    et_act = etapa_actual(t, a) if t["estado"] == "pendiente_aprobacion" else None
    et_todas = [dict(x, area_n=nombre_area(x["area"]), dias=int(dias_etapa(x))) for x in etapas_ticket(t, a)]
    from .grupos_dueno import avisos as _avisos_ap, grupo as _grupo_dueno
    avisos_t = _avisos_ap(t["id"])
    grupo_et = _grupo_dueno(g.cliente["id"], et_act["area"]) if et_act else None
    if es_segmento:
        from .aap import AAP
        from .arquitectura import costo, plan
        from .permisos import tiene_rol
        from .solicitud_segmento import ETAPAS, aplicar_decision, candidatos, de_ticket, etapa
        r = de_ticket(t)
        if r:
            eqs = equipos_visibles()
            et = etapa(AAP.de_cliente(g.cliente), t) if t["estado"] == "pendiente_aprobacion" else None
            todos_segs = q("SELECT * FROM segmentos WHERE cliente_id=?", g.cliente["id"])
            final = r["m"] if r["estado"] == "aprobada" else aplicar_decision(r["m"], r["decision_d"], eqs, r["segmento"])
            pasadas = [x for x in traza if x["accion"] in ("aprobado en AAP", "rechazado en AAP", "aprobar en AAP", "rechazar en AAP")]
            sol = {"r": r, "m": r["m"], "k": costo(final, eqs), "etapa": et, "ETAPAS": ETAPAS,
                   "puede": bool(et) and tiene_rol(g.usuario, g.cliente["id"], et),
                   "cand": candidatos(r["m"], eqs, lambda e: segmentos_de(e, todos_segs)) if et == "infraestructura" else {},
                   "plan": plan(g.cliente, final, eqs), "aprobaciones": pasadas}
        aprobar_ok = False  # la respuesta va en el panel de la solicitud, por etapa y rol
    from .confianza import URL as URL_VALIDADOR, de_ticket as validacion_de
    return render_template("ticket.html", avisos_t=avisos_t, grupo_et=grupo_et, t=t, a=a, url_aap=url_aap, url_aprob=url_aprob, url_sn=url_sn, url_odoo=url_odoo, traza=traza,
                           maquinas_t=_maquinas_ticket(t), bitacora=bitacora, web_aap=web_aap, sol=sol, flujo_t=_flujo_ticket(t, a), aprobar=aprobar_ok, et_act=dict(et_act, area_n=nombre_area(et_act["area"])) if et_act else None, et_todas=et_todas, validacion=validacion_de(t["id"]),
                           url_validador=URL_VALIDADOR)


# ------------------------------------------------------------------ inventario
RED_CATS = ("router", "switch", "firewall", "balanceador")


def seccion_eq(e):
    """Dónde vive el equipo en el menú: «redes» (routers, switches, firewalls, balanceadores), «storage» o «inventario»."""
    c = (e.get("categoria") or "").lower()
    if c in RED_CATS:
        return "redes"
    if c == "almacenamiento":
        return "storage"
    if c.startswith("clúster") or c in ("kubernetes", "openshift"):
        return "contenedores"
    if c == "estación de trabajo":
        return "estaciones"
    sk = set(e.get("skills") or []) | set(j(e.get("skills_auto"), []) if isinstance(e.get("skills_auto"), str) else []) \
        | set(j(e.get("skills_manual"), []) if isinstance(e.get("skills_manual"), str) else [])
    if c in ("hipervisor", "anfitrión") or sk & {"kvm", "esxi", "hyper-v", "proxmox", "nutanix-ahv"}:
        return "hipervisores"
    if c in ("", "servidor") and sk & {"nginx", "haproxy", "f5-bigip"}:
        return "redes"   # un servidor que cumple el rol de balanceador se gestiona con la red
    if ambito_eq(e, _amb_ubi()) == "nube":
        return "nubes"
    return "inventario"


def _amb_ubi():
    """{ubicación: on-premise | nube} de las infraestructuras del cliente (una vez por petición)."""
    if "_amb_ubi" not in g:
        from .infraestructuras import ambito as ambito_infra
        g._amb_ubi = {x["ubicacion"]: ambito_infra(x["tipo"]) for x in q("SELECT ubicacion, tipo FROM infraestructuras WHERE cliente_id=?", g.cliente["id"])}
    return g._amb_ubi


SECCION_TIPOS = {"redes": {"routers", "switches", "firewalls", "balanceadores"}, "storage": {"storage"},
                 "contenedores": {"clusters"}, "estaciones": {"estaciones"}, "hipervisores": set(), "nubes": set()}
SECCION_EP = {"redes": "consola.redes", "storage": "consola.storage", "contenedores": "consola.contenedores", "estaciones": "consola.estaciones",
              "hipervisores": "consola.hipervisores", "nubes": "consola.nubes"}
SECCION_TIT = {"redes": ("Redes", "Routers, switches, firewalls y balanceadores que AAP administra."),
               "storage": ("Storage", "Equipos de almacenamiento que AAP administra."),
               "contenedores": ("Contenedores", "Clústeres OpenShift y Kubernetes: sus nodos, operadores y namespaces."),
               "estaciones": ("Estaciones de trabajo", "Puestos de trabajo de los colaboradores que AAP administra."),
               "hipervisores": ("Hipervisores", "Anfitriones de virtualización (KVM, ESXi, Hyper-V…) donde viven las máquinas virtuales."),
               "nubes": ("Nubes", "Máquinas en nubes públicas (Google Cloud, AWS, Azure).")}


LISTA_ARGS = ("tipo", "infra", "q", "ambito", "clase", "sin_segmento", "error", "forma", "p", "n", "segmento", "skill", "estado", "ambiente", "ver", "so")


def _con_salud(eqs):
    """Agrega a cada equipo su salud (% y nivel), como en Servidores."""
    try:
        from .riesgo import evaluar as evaluar_riesgo
        from .salud import de_equipo as salud_de
        from . import kb as kb_
        prods_ = kb_.q("SELECT codigo, nombre, skill, detecta FROM productos") if kb_.disponible() else []
        for e in eqs:
            r_ = evaluar_riesgo(g.cliente["id"], e) if e.get("estado") == "con datos" else None
            s_ = salud_de(e, r_, prods_) if r_ else None
            e["salud"] = {"salud": s_["salud"], "nivel": s_["nivel"]} if s_ and s_.get("salud") is not None else None
    except Exception:  # noqa: BLE001
        pass
    return eqs


def _resumen_grupo(eqs):
    """Indicadores globales de un grupo de equipos para su caja: encendidos, con datos, hallazgos y salud promedio."""
    out = {"n": len(eqs), "on": sum(1 for e in eqs if e.get("energia") == "encendido"),
           "datos": sum(1 for e in eqs if e.get("estado") == "con datos"), "hallazgos": 0, "salud": None, "sin_salud": 0}
    if not eqs:
        return out
    try:
        out["hallazgos"] = len(hallazgos_visibles(eqs))
    except Exception:  # noqa: BLE001
        pass
    try:
        from .riesgo import evaluar as evaluar_riesgo
        from .salud import de_equipo as salud_de
        from . import kb as kb_
        prods_ = kb_.q("SELECT codigo, nombre, skill, detecta FROM productos") if kb_.disponible() else []
        vals = []
        for e in eqs:
            r_ = evaluar_riesgo(g.cliente["id"], e) if e.get("estado") == "con datos" else None
            s_ = salud_de(e, r_, prods_) if r_ else None
            if s_ and s_.get("salud") is not None:
                vals.append(s_["salud"])
        out["salud"] = round(sum(vals) / len(vals)) if vals else None
        out["sin_salud"] = len(eqs) - len(vals)
    except Exception:  # noqa: BLE001
        pass
    out["radar"] = _radar_de(eqs)
    return out


def _instancias(e):
    """La máquina y sus réplicas: otras máquinas del mismo segmento y ambiente que sirven las mismas plataformas (o, sin
    plataformas, con el mismo nombre base: nodo-jboss, nodo-jboss2…)."""
    from .inventario import SOPORTE
    neg = lambda x: set(x.get("skills") or []) - SOPORTE
    base = lambda n: re.sub(r"[-_]?\d+$", "", (n or "").split(".")[0])
    sk, segs = neg(e), set(e.get("segmentos") or [])
    out = [{"id": e["id"], "nombre": e["nombre"], "energia": e.get("energia"), "ip": e.get("ip"), "esta": True}]
    for x in equipos_visibles():
        if x["id"] == e["id"] or x.get("estado") == "retirado" or (x.get("ambiente") or "") != (e.get("ambiente") or ""):
            continue
        mismo = (sk and neg(x) == sk and (not segs or segs & set(x.get("segmentos") or []))) or (not sk and base(x["nombre"]) == base(e["nombre"]) and base(e["nombre"]) != e["nombre"])
        if mismo:
            out.append({"id": x["id"], "nombre": x["nombre"], "energia": x.get("energia"), "ip": x.get("ip"), "esta": False})
    return out


def _radar_maquina(e, componentes, puntos_r):
    try:
        from .radar import radar_maquina
        d_ = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
        f_ = (d_.get("inventario") or {}).get("fecha") or e.get("ultima_recoleccion") or ""
        horas = None
        if f_:
            from datetime import datetime as _d, timezone as _tz
            try:
                horas = (_d.now(_tz.utc) - _d.fromisoformat(str(f_).replace("Z", "+00:00"))).total_seconds() / 3600
            except ValueError:
                pass
        return radar_maquina(_radar_de([e]), e, uso_equipo(e, d_, j(e.get("discos"), []) if isinstance(e.get("discos"), str) else (e.get("discos") or [])),
                             componentes, puntos_r[0] if puntos_r else None, _instancias(e), horas)
    except Exception as x:  # noqa: BLE001
        current_app.logger.warning("radar máquina: %s", x)
        return _radar_de([e])


def _radar_de(eqs):
    """Radar de salud (los 5 principios con sus criterios) de un conjunto de equipos, para cualquier vista."""
    if not eqs:
        return None
    try:
        from .radar import radar_equipos
        if "_hs_todos" not in g:
            g._hs_todos = q("SELECT * FROM hallazgos WHERE cliente_id=?", g.cliente["id"])
        ids = {e["id"] for e in eqs}
        return radar_equipos([dict(e, _datos=j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})) for e in eqs],
                             [h for h in g._hs_todos if h["equipo_id"] in ids])
    except Exception as e_:  # noqa: BLE001
        current_app.logger.warning("radar: %s", e_)
        return None


def _portada_inv(seccion, todos, tipos_todos, ep_inv):
    """Cajas con las que arranca cada sección (Servidores, Redes, Storage…). Sin filtros se muestran solo las cajas;
    al elegir una se pasa a la lista (o al detalle, si la caja es un equipo)."""
    if any(request.args.get(k) for k in LISTA_ARGS):
        return None
    cajas = []
    if seccion == "inventario":
        fis = sum(1 for e in todos if (e.get("forma") or "") != "virtual")
        vir = sum(1 for e in todos if (e.get("forma") or "") == "virtual")
        sin = sum(1 for e in todos if not e.get("segmentos"))
        cajas = [{"t": "Servidores virtuales", "d": "Máquinas virtuales en hipervisores y nubes", "n": vir, "u": "servidor", "url": url_for(ep_inv, forma="virtual"), "ic": "virtual",
                  "res": _resumen_grupo([e for e in todos if (e.get("forma") or "") == "virtual"])}]
        if True:   # la caja de físicos está siempre: desde ahí se agregan
            cajas.insert(0, {"t": "Servidores físicos", "d": "Equipos bare metal", "n": fis, "u": "servidor", "url": url_for(ep_inv, forma="fisico"), "ic": "fisico",
                             "res": _resumen_grupo([e for e in todos if (e.get("forma") or "") != "virtual"])})
    elif seccion in ("redes", "storage", "contenedores", "estaciones"):   # lista directa con la franja de salud arriba (sin cajas)
        return None
    elif seccion == "redes-cajas":
        cajas = [{"t": n, "d": d, "n": tipos_todos.get(k, 0), "u": "equipo", "url": url_for(ep_inv, tipo=k), "ic": k,
                  "res": _resumen_grupo([e for e in todos if grupo_periferico(e) == k])}
                 for k, n, d in (("routers", "Routers", "Enrutamiento y BGP"), ("switches", "Switches", "Conmutación y VLAN"),
                                 ("firewalls", "Firewalls", "Reglas, NAT y zonas"), ("balanceadores", "Balanceadores", "Reparto de carga hacia las aplicaciones"))]
    elif seccion in ("contenedores", "estaciones"):
        cajas = [{"t": e["nombre"], "d": " · ".join(x for x in ((e.get("so") or ""), (e.get("ubicacion") or ""), (e.get("ambiente") or "")) if x),
                  "estado": e.get("energia") or "", "url": url_for("consola.equipo", eid=e["id"]), "ic": seccion, "detalle": True,
                  "no_probado": e.get("estado") != "con datos"} for e in todos]
    elif seccion in ("nubes", "hipervisores"):
        return {"infra": True}
    return {"cajas": cajas, "total": len(todos)}


def _familia_so(so):
    """«Red Hat Enterprise Linux 9.6 (Plow)» → «RHEL 9» · «Ubuntu 22.04.3 LTS» → «Ubuntu 22.04» · Windows Server 2022…"""
    t = (so or "").strip()
    if not t:
        return "Sin sistema detectado"
    m = re.search(r"(red ?hat( enterprise linux)?|rhel)\D*(\d+)", t, re.I)
    if m:
        return f"RHEL {m.group(3)}"
    m = re.search(r"ubuntu\D*(\d+\.\d+)", t, re.I)
    if m:
        return f"Ubuntu {m.group(1)}"
    m = re.search(r"windows server\D*(\d{4})", t, re.I)
    if m:
        return f"Windows Server {m.group(1)}"
    m = re.search(r"(centos|rocky|alma|debian|fedora|oracle linux|suse)\D*(\d+)?", t, re.I)
    if m:
        return f"{m.group(1).title()} {m.group(2) or ''}".strip()
    if t.lower().split("(")[0].strip() in ("linux", "gnu/linux"):
        return "Linux sin distribución"
    return t.split("(")[0].strip()[:40]


MOTORES_BD = ("postgresql", "sqlserver", "oracle", "mysql", "mariadb", "mongodb", "redis")


def _propia_faceops(e):
    d = e.get("datos")
    return '"propia_faceops": true' in d if isinstance(d, str) else bool((d or {}).get("propia_faceops"))


@bp.route("/bases-de-datos")
@requiere_login
def bases_datos():
    """Bases de datos del parque (PostgreSQL, SQL Server, Redis…): una caja por motor y, al elegirla, sus instancias."""
    from .asistente import PLATAFORMAS
    nombre = {c: n for c, n, _ in PLATAFORMAS}
    grupos = defaultdict(list)
    for e, r in _riesgo_parque():
        if _propia_faceops(e):   # las bases de datos internas de FaceOps y AAP no son bases de datos del cliente
            continue
        for c in set(_plataformas_de(e, r)):
            if c in MOTORES_BD:
                grupos[c].append(e)
    sel = request.args.get("p", "")
    if any(seccion_eq(e) == "inventario" and not _propia_faceops(e) for e in equipos_visibles()):   # con servidores del cliente: los motores que FaceOps instala aparecen siempre
        for c in ("sqlserver", "mongodb", "redis", "postgresql", "mysql"):
            grupos.setdefault(c, [])
    cajas = [{"clave": c, "t": nombre.get(c, c), "n": len(grupos[c]), "res": _resumen_grupo(grupos[c]) if grupos[c] else None, "inst": True,
              "instalar": {"sqlserver": "SQL Server", "mongodb": "MongoDB", "redis": "Redis", "postgresql": "PostgreSQL", "mysql": "MySQL"}.get(c)}
             for c in sorted(grupos, key=lambda c: (not grupos[c], nombre.get(c, c).lower()))]
    insts = []
    if sel in grupos:
        riesgos = {e["id"]: r for e, r in _riesgo_parque()}
        insts = sorted((_instancia(e, riesgos.get(e["id"]) or {}, sel, corto=True) for e in grupos[sel]), key=lambda x: (x["estado"] != "encendido", x["id"]))
    _salud_cajas(cajas, grupos)
    srv_inc = sorted([e for e in equipos_visibles() if seccion_eq(e) in ("inventario", "nubes") and not _propia_faceops(e)], key=lambda e: e["nombre"])
    return render_template("plataformas.html", servidores_inc=srv_inc, titulo="Bases de datos", desc="Motores de base de datos y caché del parque: cada instalación es una instancia con su ficha.",
                           cajas=cajas, sel=sel if sel in grupos else "", sel_t=nombre.get(sel, sel), equipos=[], instancias=insts, ep="consola.bases_datos",
                           radar_sec=_radar_de(grupos.get(sel, []) if sel else list({e["id"]: e for v in grupos.values() for e in v}.values())))


def _salud_cajas(cajas, grupos):
    """Salud consolidada de cada caja: la línea que resume los 5 puntos del radar de salud de sus máquinas."""
    for c in cajas:
        es_ = grupos.get(c["clave"]) or []
        r_ = _radar_de(es_) if es_ else None
        c["salud"] = r_.get("pct") if r_ else None


def _nombre_plat(c):
    """Nombre legible de una plataforma que no está en la lista base (vault, keycloak, jenkins…)."""
    return {"faceops": "FaceOps (consola)", "aap-gateway": "AAP · Gateway", "aap-controller": "AAP · Automation Controller",
            "automation-hub": "AAP · Automation Hub", "eda": "AAP · Event-Driven Ansible", "aap-metricas": "AAP · Métricas",
            "podman": "Podman", "redis": "Redis", "postgresql": "PostgreSQL", "vault": "HashiCorp Vault", "keycloak": "Keycloak", "jenkins": "Jenkins", "gitea": "Gitea", "grafana": "Grafana",
            "prometheus": "Prometheus", "trino": "Trino", "minio": "MinIO", "haproxy": "HAProxy", "bind": "DNS (BIND)",
            "samba-ad": "Active Directory (Samba)", "ad": "Active Directory"}.get(c, c.replace("-", " ").title())


@bp.route("/plataformas")
@requiere_login
def plataformas_vista():
    """Plataformas detectadas en el parque (JBoss, Redis, PostgreSQL…): cajas y, al elegir una, sus máquinas."""
    from .asistente import PLATAFORMAS
    nombre = {c: n for c, n, _ in PLATAFORMAS}
    sos = {"rhel", "so:ubuntu", "windows-server", "so:windows", "so:linux"}
    grupos = defaultdict(list)
    for e, r in _riesgo_parque():
        for c in set(_plataformas_de(e, r)):
            if c not in sos:
                grupos[c].append(e)
    sel = request.args.get("p", "")
    nombre.update({c: _nombre_plat(c) for c in grupos if c not in nombre})
    cajas = [{"clave": c, "t": nombre.get(c, c), "n": len(grupos[c]), "res": _resumen_grupo(grupos[c]), "inst": True}
             for c in sorted(grupos, key=lambda c: nombre.get(c, c).lower())]
    try:   # manifiesto de arquitectura: lo instalado que no está aceptado queda marcado «fuera del manifiesto»
        from .manifiesto import ESTADOS as _mfe, por_skill as _mfps
        ps_ = _mfps(g.cliente["id"])
        inv_ = {"postgresql": "postgres", "jboss-eap": "jboss", "wireguard": "vpn", "redis-insight": "redisinsight", "datalake": "trino", "data-lake": "trino", "activemq": "artemis"}
        for c_ in cajas:
            t_ = ps_.get(c_["clave"]) or ps_.get(inv_.get(c_["clave"], ""))
            c_["mf"] = (t_ or {}).get("estado") or "fuera"
            c_["mf_t"] = _mfe.get(c_["mf"], "Fuera del manifiesto")
    except Exception:  # noqa: BLE001
        pass
    insts = []
    if sel:   # cada instalación de la plataforma es una instancia con su propio ID (la máquina se ve en su detalle)
        riesgos = {e["id"]: r for e, r in _riesgo_parque()}
        insts = sorted((_instancia(e, riesgos.get(e["id"]) or {}, sel, corto=True) for e in grupos.get(sel, [])),
                       key=lambda x: (x["estado"] != "encendido", x["id"]))
    _salud_cajas(cajas, grupos)
    srv_inc = sorted([e for e in equipos_visibles() if seccion_eq(e) in ("inventario", "nubes") and not _propia_faceops(e)], key=lambda e: e["nombre"])
    return render_template("plataformas.html", servidores_inc=srv_inc, titulo="Plataformas", desc="Tecnologías instaladas en el parque: servidores de aplicación, bases de datos, caché, mensajería, contenedores y gestión.",
                           cajas=cajas, sel=sel, sel_t=nombre.get(sel, sel), equipos=[], instancias=insts, ep="consola.plataformas_vista",
                           radar_sec=_radar_de(grupos.get(sel, []) if sel else list({e["id"]: e for v in grupos.values() for e in v}.values())))


def id_plataforma(cod, eid):
    """ID estable de una instancia de plataforma: código del producto + máquina que la corre (PL-SATELLITE-0077)."""
    return f"PL-{re.sub(r'[^A-Z0-9]+', '-', cod.upper())}-{int(eid):04d}"


def _instancia(e, r, cod, corto=False):
    """Atributos estándar de una instancia de plataforma (los mismos para cualquier producto)."""
    from . import kb as kb_
    from .asistente import PLATAFORMAS
    from .salud import componentes as comps_salud
    from .versiones import de as version_de
    prods = kb_.q("SELECT codigo, nombre, fabricante, descripcion, skill, detecta FROM productos") if kb_.disponible() else []
    p = next((x for x in prods if x["codigo"] == cod), {})
    nom = p.get("nombre") or {c: n for c, n, _ in PLATAFORMAS}.get(cod, cod)
    try:
        ver = version_de(e, cod) if cod != "rhel" else None
    except Exception:  # noqa: BLE001
        ver = None
    sal = next((c for c in (comps_salud(e, r, prods) if r else []) if c["codigo"] == cod), None)
    if e.get("energia") == "apagado":
        sal = {"salud": 0, "nivel": "rojo", "motivos": [{"t": "la máquina que la corre está apagada"}]}
    grupo = next((n for k, n, cs in GRUPOS_PLATAFORMA if cod in cs), "Otras plataformas")
    x = {"id": id_plataforma(cod, e["id"]), "codigo": cod, "nombre": nom, "grupo": grupo, "fabricante": p.get("fabricante") or "",
         "descripcion": p.get("descripcion") or "", "version": (ver or {}).get("version") or "", "ciclo": (ver or {}).get("ciclo"),
         "salud": (sal or {}).get("salud"), "nivel": (sal or {}).get("nivel") or "sin datos",
         "motivos": [m.get("t") or m.get("texto") or "" for m in (sal or {}).get("motivos") or []],
         "estado": e.get("energia") or "sin dato", "ambiente": e.get("ambiente") or "", "ubicacion": e.get("ubicacion") or "",
         "segmentos": e.get("segmentos") or [], "equipo": {"id": e["id"], "nombre": e["nombre"], "ip": e.get("ip") or "", "so": e.get("so") or ""}}
    try:   # radar de salud de la instancia: los 5 principios (servicio, remediaciones y CVEs propios; el resto, de su máquina)
        from .radar import radar_componente
        x["radar"] = radar_componente(_radar_de([e]), {"nombre": nom, "secciones": (sal or {}).get("secciones") or []})
    except Exception:  # noqa: BLE001
        x["radar"] = None
    if corto:
        return x
    from .inventario import COMPONENTES_INTERNOS
    from .boveda import web as web_b, disponible as boveda_ok
    errs = [f for f in (r or {}).get("errores", []) if f["producto"] == nom]
    entrar = None
    if g.perfil["modo"] == "escritura":
        if cod == "satellite":
            entrar = {"url": url_for("consola.consola_satellite", eid=e["id"]), "t": "Consola de Satellite (admin)"}
        else:
            cw = consola_plataforma(e, cod)
            if cw:
                entrar = {"url": url_for("consola.consola_web_plataforma", eid=e["id"], cod=cod, sub=""), "t": cw["producto"]}
    try:
        cred = bool(boveda_ok() and web_b(e["nombre"], cod))
    except Exception:  # noqa: BLE001
        cred = False
    d_ = j(e.get("datos"), {})
    fab_ = (p.get("fabricante") or "").lower()
    reg_, sat_ = d_.get("registrado"), (d_.get("satellite") or {}).get("servidor")
    so_rh_ = bool(re.match(r"(red ?hat|rhel)", (e.get("so") or "").lower()))
    if "red hat" in fab_ or "paquete de rhel" in fab_ or cod in ("rhel", "satellite"):
        if not so_rh_:
            lic_ = {"estado": "no atachada", "t": "El servidor no es RHEL: la suscripción de Red Hat no cubre esta plataforma"}
        elif reg_ is False:
            lic_ = {"estado": "no atachada", "t": "El servidor no está registrado: sin suscripción no recibe parches"}
        elif reg_ or sat_:
            lic_ = {"estado": "atachada", "t": ("Suscripción de Red Hat del servidor" if "paquete de rhel" in fab_ else "Suscripción de Red Hat")
                    + (f" · registrado en Satellite {sat_}" if sat_ else " · registrado en Red Hat") + (" · el manifiesto de Satellite se cargó al instalar" if cod == "satellite" else "")}
        else:
            lic_ = {"estado": "sin dato", "t": "Aún no se sabe si el servidor está registrado (falta inventariarlo)"}
    elif "microsoft" in fab_ or cod in ("sqlserver", "iis", "windows-server"):
        lic_ = {"estado": "sin dato", "t": "Licencia de Microsoft: no registrada en FaceOps"}
    else:
        lic_ = {"estado": "no aplica", "t": "Software libre sin suscripción" if fab_ else "Sin licencia registrada en FaceOps"}
    certs_ = []
    try:
        from .certificados_panel import leer as leer_certs, de_equipo as certs_de
        certs_ = [{"cn": f["cn"], "servicio": f.get("servicio") or "", "estado": f["estado"], "dias": f.get("dias"), "nivel": f.get("nivel"),
                   "url": url_for("consola.certificado", serie=f["serie"])} for f in certs_de(leer_certs(g.cliente, equipos_visibles()), e["nombre"])]
    except Exception:  # noqa: BLE001
        pass
    x.update(licencia=lic_, certificados=certs_)
    x.update(entrar=entrar, credencial=cred, internos=COMPONENTES_INTERNOS.get(cod, []),
             errores=len(errs), activos=sum(1 for f in errs if f["activa"]),
             prevenidos=sum(1 for f in errs if (f.get("cobertura") or {}).get("estado") == "cubierto"),
             recoleccion=e.get("ultima_recoleccion") or "")
    return x


# Acciones exclusivas de cada plataforma (automatizaciones que solo se lanzan desde su ficha)
ACCIONES_PLATAFORMA = {
    "satellite": [{"clave": "incorporar-rhel", "auto": "Satellite · Incorporar RHEL", "t": "Incorporar RHEL",
                   "d": "Registra uno o más servidores RHEL en este Satellite: desde aquí reciben contenido, parches y el reporte de vulnerabilidades.",
                   "elige": "rhel"},
                  {"clave": "convertir-rhel", "auto": "Satellite · Convertir a RHEL", "t": "Convertir a RHEL",
                   "d": "Convierte CentOS, Rocky Linux, AlmaLinux u Oracle Linux a RHEL de la misma versión (Convert2RHEL, con snapshot previo y vuelta atrás) y la deja registrada en este Satellite. Fedora no se puede convertir.",
                   "elige": "convertible"},
                  {"clave": "actualizar", "auto": "Satellite · Actualizar", "t": "Actualizar Satellite",
                   "d": "Lleva este Satellite a una versión más nueva con satellite-maintain: chequeos previos, snapshot y vuelta atrás si algo falla.",
                   "elige": None, "campos": [{"var": "sat_version_destino", "t": "Versión de destino", "opciones": ["6.18"]}]}],
    "wireguard": [
        {"clave": "crear-cliente", "auto": "VPN · Gestionar acceso", "t": "Crear cliente", "elige": None, "fijos": {"vpn_accion": "crear-cliente"},
         "d": "Da de alta una empresa en la VPN con su propio rango de IPs dentro del túnel.",
         "campos": [{"var": "vpn_cliente", "t": "Nombre del cliente (empresa)", "tipo": "texto"},
                    {"var": "vpn_nota", "t": "Nota", "tipo": "texto", "opcional": True}]},
        {"clave": "crear-usuario", "auto": "VPN · Gestionar acceso", "t": "Crear usuario", "elige": None, "fijos": {"vpn_accion": "crear-usuario"},
         "d": "Crea un usuario de un cliente con acceso solo a los segmentos elegidos y por un período: al vencer, el servidor lo corta solo.",
         "campos": [{"var": "vpn_cliente", "t": "Cliente", "tipo": "opciones", "fuente": "clientes"},
                    {"var": "vpn_usuario", "t": "Usuario (minúsculas, sin espacios)", "tipo": "texto"},
                    {"var": "vpn_accesos", "t": "Segmentos de negocio a los que tendrá acceso", "tipo": "accesos", "fuente": "segmentos_ips"},
                    {"var": "vpn_desde", "t": "Desde", "tipo": "fecha", "opcional": True},
                    {"var": "vpn_hasta", "t": "Hasta", "tipo": "fecha"},
                    {"var": "vpn_nota", "t": "Nota", "tipo": "texto", "opcional": True}]},
        {"clave": "extender", "auto": "VPN · Gestionar acceso", "t": "Extender vigencia", "elige": None, "fijos": {"vpn_accion": "extender"},
         "d": "Cambia la fecha de término de un usuario (también reactiva uno vencido).",
         "campos": [{"var": "vpn_usuario", "t": "Usuario", "tipo": "opciones", "fuente": "usuarios"},
                    {"var": "vpn_hasta", "t": "Nueva fecha de término", "tipo": "fecha"}]},
        {"clave": "revocar", "auto": "VPN · Gestionar acceso", "t": "Revocar usuario", "elige": None, "fijos": {"vpn_accion": "revocar"},
         "d": "Corta el acceso de un usuario de inmediato, aunque su vigencia no haya terminado.",
         "campos": [{"var": "vpn_usuario", "t": "Usuario", "tipo": "opciones", "fuente": "usuarios_activos"}]},
    ],
}
CONVERTIBLES = r"(centos|rocky|alma|oracle)"


def _opciones_accion(e_plat, fuente):
    """[(valor, etiqueta)] para los campos de una acción de plataforma."""
    vpn_ = j(e_plat.get("datos"), {}).get("vpn") or {}
    if fuente == "clientes":
        return [(k, f"{k} · {v.get('rango', '')}") for k, v in sorted((vpn_.get("clientes") or {}).items())]
    if fuente in ("usuarios", "usuarios_activos"):
        return [(k, f"{k} · {v.get('cliente', '')}") for k, v in sorted((vpn_.get("usuarios") or {}).items())
                if fuente == "usuarios" or not v.get("revocado")]
    if fuente == "segmentos_ips":   # cada segmento de negocio con sus máquinas (para elegir todo el segmento o algunas IPs)
        eqs = equipos_visibles()
        segs = sorted({sg for x in eqs for sg in (x.get("segmentos") or [])})
        return [(sg, [(x["ip"], x["nombre"]) for x in sorted(eqs, key=lambda y: y["nombre"]) if sg in (x.get("segmentos") or []) and x.get("ip")]) for sg in segs]
    if fuente == "segmentos":   # segmentos de negocio (sus máquinas) y redes del inventario
        eqs = equipos_visibles()
        segs = sorted({sg for x in eqs for sg in (x.get("segmentos") or [])})
        out = [(f"seg:{sg}", f"Segmento de negocio {sg} ({sum(1 for x in eqs if sg in (x.get('segmentos') or []) and x.get('ip'))} máquinas)") for sg in segs]
        redes = sorted({re.sub(r"\.\d+$", ".0/24", x["ip"]) for x in eqs if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", x.get("ip") or "")})
        return out + [(f"red:{r_}", f"Red {r_} completa") for r_ in redes]
    return []


def _vpn_usuarios(e_plat):
    """Usuarios de la VPN con su estado: vigente, por vencer (menos de 3 días), programado, vencido o revocado."""
    from datetime import timedelta
    vpn_ = j(e_plat.get("datos"), {}).get("vpn") or {}
    desc_ = j(e_plat.get("datos"), {}).get("vpn_descargas") or {}
    ahora_ = datetime.now(timezone.utc)
    def f_(x):
        try:
            t_ = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
            return t_ if t_.tzinfo else t_.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    out = []
    for u, x in sorted((vpn_.get("usuarios") or {}).items()):
        d_, h_ = f_(x.get("desde")), f_(x.get("hasta"))
        est = ("revocado" if x.get("revocado") else "programado" if d_ and ahora_ < d_ else "vencido" if h_ and ahora_ >= h_
               else "por vencer" if h_ and h_ - ahora_ < timedelta(days=3) else "vigente")
        out.append(dict(x, usuario=u, estado=est, descargada=desc_.get(u), desde_l=d_, hasta_l=h_))
    return out


def _candidatos_accion(acc, e_plat):
    """Máquinas que se pueden elegir en la acción, con el motivo si no se puede (ya incorporada, apagada, sin datos)."""
    out = []
    for x in equipos_visibles():
        so_ = (x.get("so") or "").lower()
        es_rhel = bool(re.match(r"(red ?hat|rhel)", so_)) and "coreos" not in so_
        if x["id"] == e_plat["id"] or acc["elige"] == "rhel" and not es_rhel or acc["elige"] == "convertible" and not re.search(CONVERTIBLES, so_):
            continue
        ya_ = acc["elige"] == "rhel" and (j(x.get("datos"), {}).get("satellite") or {}).get("servidor") == e_plat["nombre"]
        no_ = ("ya incorporada" if ya_ else "apagada" if x.get("energia") == "apagado"
               else "sin datos de inventario" if x.get("estado") != "con datos" else "")
        out.append({"nombre": x["nombre"], "so": x.get("so") or "", "ambiente": x.get("ambiente") or "", "no": no_})
    return sorted(out, key=lambda c: (bool(c["no"]), c["nombre"]))


def _plataforma_de_id(pid):
    m = re.fullmatch(r"PL-([A-Z0-9-]+)-(\d{4,})", pid or "")
    e = next((x for x in equipos_visibles() if x["id"] == int(m.group(2))), None) if m else None
    if not e:
        abort(404)
    r = next((r_ for e_, r_ in _riesgo_parque() if e_["id"] == e["id"]), {}) or {}
    cod = next((c for c in _plataformas_de(e, r) if id_plataforma(c, e["id"]) == pid), None)
    if not cod:
        abort(404)
    return e, r, cod


@bp.route("/plataformas/<pid>/accion/<clave>", methods=["POST"])
@requiere_login
def plataforma_accion(pid, clave):
    """Lanza una acción de la plataforma sobre las máquinas elegidas: un ticket con aprobación en AAP."""
    from .operacion import solicitar
    _escritura()
    e, r, cod = _plataforma_de_id(pid)
    acc = next((x for x in ACCIONES_PLATAFORMA.get(cod, []) if x["clave"] == clave), None)
    if not acc:
        abort(404)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], acc["auto"])
    ok_ = {c["nombre"] for c in _candidatos_accion(acc, e) if not c["no"]} if acc["elige"] else {e["nombre"]}
    elegidas = [n for n in dict.fromkeys(request.form.getlist("equipos")) if n] if acc["elige"] else [e["nombre"]]
    try:
        if not a:
            raise ValueError(f"Falta «{acc['auto']}» en el catálogo (sincroniza con AAP)")
        if not _certificada(a):
            raise ValueError(f"«{acc['t']}» aún no está probada: queda bloqueada hasta probarla")
        if not elegidas:
            raise ValueError("Elige al menos un servidor")
        malas = [n for n in elegidas if n not in ok_]
        if malas:
            raise ValueError(f"No se pueden elegir: {', '.join(malas)}")
        eqs = [x for x in equipos_visibles() if x["nombre"] in elegidas]
        params = {"sat_equipo": e["nombre"], "sat_ip": e.get("ip") or ""} if cod == "satellite" else {}
        params.update(acc.get("fijos") or {})
        try:
            tz_ = max(-840, min(840, int(request.form.get("_tz") or 0)))   # minutos de diferencia del navegador con UTC
        except ValueError:
            tz_ = 0
        for c_ in acc.get("campos", []):
            tipo_ = c_.get("tipo", "opciones")
            vals_ = [v for v in request.form.getlist(c_["var"]) if v.strip()]
            v_ = (vals_[0] if vals_ else "").strip()
            if not vals_ and c_.get("opcional"):
                continue
            if not vals_:
                raise ValueError(f"Falta: {c_['t']}")
            if tipo_ == "texto":
                params[c_["var"]] = v_[:120]
            elif tipo_ == "fecha":
                from datetime import timedelta
                try:
                    loc_ = datetime.fromisoformat(v_)
                except ValueError:
                    raise ValueError(f"Fecha no válida: {c_['t']}")
                params[c_["var"]] = (loc_ + timedelta(minutes=tz_)).replace(tzinfo=timezone.utc).isoformat(timespec="minutes")
            elif tipo_ == "accesos":   # «seg:<segmento>» = todo el segmento · «ip:<segmento>|<ip>» = IPs puntuales
                mapa_ = dict(_opciones_accion(e, c_["fuente"]))
                accesos_ = {}
                for v in vals_:
                    if v.startswith("seg:") and v[4:] in mapa_:
                        accesos_[v[4:]] = {"segmento": v[4:], "todo": True, "ips": [ip for ip, _ in mapa_[v[4:]]]}
                for v in vals_:
                    sg_, _, ip_ = v[3:].partition("|")
                    if v.startswith("ip:") and sg_ in mapa_ and ip_ in [ip for ip, _ in mapa_[sg_]] and not accesos_.get(sg_, {}).get("todo"):
                        accesos_.setdefault(sg_, {"segmento": sg_, "todo": False, "ips": []})["ips"].append(ip_)
                if len(accesos_) != len({v[4:] if v.startswith("seg:") else v[3:].partition("|")[0] for v in vals_}):
                    raise ValueError("Hay segmentos o IPs que no corresponden")
                params["vpn_accesos"] = [x for x in accesos_.values() if x["ips"]]
                params["vpn_segmentos"] = sorted({f"{ip}/32" for x in params["vpn_accesos"] for ip in x["ips"]})
                if not params["vpn_segmentos"]:
                    raise ValueError("Elige al menos un segmento o una IP")
            else:
                validos_ = [o[0] for o in _opciones_accion(e, c_["fuente"])] if c_.get("fuente") else c_["opciones"]
                if any(v not in validos_ for v in vals_):
                    raise ValueError(f"Elige {c_['t'].lower()}")
                params[c_["var"]] = vals_ if tipo_ == "multi" else v_
        if "vpn_segmentos" in params and "vpn_accesos" not in params:   # segmentos de negocio → IPs de sus máquinas; redes → su rango
            redes_ = []
            for v in params["vpn_segmentos"]:
                if v.startswith("red:"):
                    redes_.append(v[4:])
                else:
                    redes_ += [f"{x['ip']}/32" for x in equipos_visibles() if v[4:] in (x.get("segmentos") or []) and x.get("ip")]
            params["vpn_segmentos"] = sorted(set(redes_))
            if not params["vpn_segmentos"]:
                raise ValueError("Los segmentos elegidos no tienen máquinas con IP")
        detalle_ = " · ".join(str(params[k]) for k in ("vpn_cliente", "vpn_usuario") if params.get(k))
        motivo = (request.form.get("motivo") or "").strip()[:200] or f"{acc['t']}{' ' + detalle_ if detalle_ else ''} · {pid}"
        t = solicitar(g.usuario, g.cliente, a, None, params, f"{motivo} · {', '.join(elegidas)}", equipos=eqs)
        flash(f"{acc['t']}: {t['numero']} creado para {len(eqs)} servidor{'es' if len(eqs) != 1 else ''} (espera su aprobación)", "ok")
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.plataforma_detalle", pid=pid) + "#acciones")


@bp.route("/plataformas/<pid>")
@requiere_login
def plataforma_detalle(pid):
    """Detalle de una instancia de plataforma por su ID."""
    e, r, cod = _plataforma_de_id(pid)
    acciones = []
    for x in ACCIONES_PLATAFORMA.get(cod, []):
        a_ = q1("SELECT id, nombre_aap FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], x["auto"])
        probada_ = bool(a_) and _certificada(a_)
        campos_ = [dict(c_, opciones=_opciones_accion(e, c_["fuente"]) if c_.get("fuente") else [(o, o) for o in c_.get("opciones", [])])
                   for c_ in x.get("campos", [])]
        acciones.append(dict(x, disponible=bool(a_), probada=probada_, campos=campos_, candidatos=_candidatos_accion(x, e) if x["elige"] else []))
    vpn_ = (j(e.get("datos"), {}).get("vpn") or {}) if cod == "wireguard" else None
    from .duenos import de as dueno_de, candidatos as dueno_cand
    return render_template("plataforma.html", dueno=dueno_de(g.cliente["id"], "plataforma", pid), dueno_maq=dueno_de(g.cliente["id"], "equipo", e["id"]),
                           dueno_cand=dueno_cand(g.cliente["id"]), p=_instancia(e, r, cod), acciones=acciones, vpn=vpn_,
                           vpn_usuarios=_vpn_usuarios(e) if cod == "wireguard" else [])


@bp.route("/buenas-practicas")
@requiere_login
def buenas_practicas():
    """Buenas prácticas por tecnología: cajas con su aprobación y cumplimiento sobre la infraestructura."""
    from . import buenas_practicas as bp_
    return render_template("buenas_practicas.html", cajas=bp_.resumen(g.cliente["id"], _riesgo_parque()))


@bp.route("/buenas-practicas/<tec>", methods=["GET", "POST"])
@requiere_login
def buenas_practicas_tec(tec):
    """Las buenas prácticas de una tecnología: Arquitectura aprueba; aprobadas, se evalúan y se recomiendan al dueño."""
    from . import buenas_practicas as bp_
    cat = bp_.catalogo()
    if tec not in cat.get("tecnologias", {}):
        abort(404)
    if request.method == "POST":
        _escritura()
        try:
            if request.form.get("accion") == "recomendar":
                n, sin = bp_.enviar_recomendaciones(g.cliente, _riesgo_parque(), request.form.get("codigo", ""), g.usuario["usuario"])
                flash(f"{n} recomendación{'es' if n != 1 else ''} enviada{'s' if n != 1 else ''} a los dueños" + (f" · sin dueño: {', '.join(sin)}" if sin else ""), "ok" if n or not sin else "error")
            else:
                if not (g.usuario.get("es_admin") or puede_aprobar(g.usuario, g.cliente["id"])):
                    raise ValueError("Solo el equipo de Arquitectura (aprobadores) decide las buenas prácticas")
                bp_.aprobar(g.cliente["id"], request.form.get("codigo", ""), request.form.get("decision", ""), g.usuario["usuario"], request.form.get("nota", ""))
                flash(f"{request.form.get('codigo')}: {request.form.get('decision')}", "ok")
        except ValueError as x:
            flash(str(x), "error")
        return redirect(url_for("consola.buenas_practicas_tec", tec=tec) + "#" + (request.form.get("codigo") or ""))
    return render_template("buenas_practicas_tec.html", tec=tec, t=cat["tecnologias"][tec], practicas=bp_.evaluar(g.cliente["id"], _riesgo_parque(), tec),
                           aprueba=bool(g.usuario.get("es_admin") or puede_aprobar(g.usuario, g.cliente["id"])))


@bp.route("/disponibilidad")
@requiere_login
def disponibilidad():
    """Gobierno › Disponibilidad: por segmento de negocio, cada función (sistema o plataforma) con sus instancias encendidas.
    Una función con una sola instancia es un punto único de falla; con dos o más (y balanceo, si atiende tráfico) es alta disponibilidad."""
    from .asistente import PLATAFORMAS
    from .grupos_segmento import NOMBRE as NOM_GRUPO, grupo_de
    nombre = {c: n for c, n, _ in PLATAFORMAS}
    amb = request.args.get("ambiente", "produccion")
    riesgos = {e["id"]: r for e, r in _riesgo_parque()}
    segs = segmentos_visibles(g.usuario, g.cliente["id"])
    a_rep = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "FaceOps · Replicar con alta disponibilidad")
    filas, tot_f, tot_ha = [], 0, 0
    for sg in segs:
        es = [e for e in equipos_visibles() if sg["nombre"] in (e.get("segmentos") or []) and (not amb or (e.get("ambiente") or "") == amb)]
        if not es:
            continue
        funciones = defaultdict(list)
        for e in es:
            ps_ = [c for c in _plataformas_de(e, riesgos.get(e["id"]) or {}) if not c.startswith("so:") and c not in ("rhel", "windows-server")]
            for c in ps_ or ["so:" + _familia_so(e.get("so"))]:
                funciones[c].append(e)
        fx = []
        for c, lst in sorted(funciones.items()):
            on = [e for e in lst if e.get("energia") == "encendido"]
            estado = "alta disponibilidad" if len(on) >= 2 else ("punto único de falla" if len(on) == 1 else "caída")
            fx.append({"funcion": nombre.get(c, c[3:] if c.startswith("so:") else c), "n": len(lst), "on": len(on), "estado": estado,
                       "equipos": [(e["id"], e["nombre"], e.get("energia")) for e in lst],
                       "uptime": max((j(e.get("datos"), {}).get("uptime_dias") or 0) for e in on) if on else None})
        balanceo = any(c in ("nginx", "haproxy") for c in funciones) or any(grupo_periferico(e) == "balanceadores" for e in es)
        ha_ = sum(1 for x in fx if x["estado"] == "alta disponibilidad")
        tot_f += len(fx); tot_ha += ha_
        filas.append({"segmento": sg["nombre"], "grupo": NOM_GRUPO.get(grupo_de(sg), ""), "funciones": fx, "balanceo": balanceo,
                      "pct": round(100 * ha_ / len(fx)) if fx else None, "spof": sum(1 for x in fx if x["estado"] != "alta disponibilidad")})
    filas.sort(key=lambda x: (x["pct"] if x["pct"] is not None else 101, x["segmento"]))
    return render_template("disponibilidad.html", filas=filas, amb=amb, global_=round(100 * tot_ha / tot_f) if tot_f else None,
                           a_rep=a_rep)


@bp.route("/configuracion/pruebas")
@requiere_login
def pruebas_automatizaciones():
    """Administración › Pruebas: estado de prueba de cada acción (certificada, cambiada o sin probar)."""
    from .pruebas import resumen
    _solo_admin()
    from .pruebas import plan_de
    filas = resumen(g.cliente["id"])
    for x in filas:   # el detalle caso a caso vive en la ficha de cada automatización
        x["plan_d"] = plan_de(x["nombre_aap"])
    return render_template("pruebas.html", filas=filas, cuenta=Counter(x["estado"] for x in filas))


@bp.route("/seguridad/vpn")
@requiere_login
def vpn_seguridad():
    """Gobierno › Seguridad › VPN: los servidores VPN del parque con sus clientes, usuarios y vigencias; cada uno abre su ficha."""
    servidores = []
    for e, r in _riesgo_parque():
        if "wireguard" in set(_plataformas_de(e, r)):
            us_ = _vpn_usuarios(e)
            vpn_ = j(e.get("datos"), {}).get("vpn") or {}
            servidores.append({"pid": id_plataforma("wireguard", e["id"]), "equipo": e["nombre"], "endpoint": vpn_.get("endpoint") or e.get("ip"),
                               "clientes": vpn_.get("clientes") or {}, "usuarios": us_, "estado": e.get("energia") or "",
                               "cuenta": Counter(u["estado"] for u in us_)})
    return render_template("vpn_seguridad.html", servidores=servidores)


@bp.route("/configuracion")
@requiere_login
def configuracion():
    """Administración de FaceOps para el cliente: integraciones, usuarios y acciones (se suman otras de a poco)."""
    _solo_admin()
    return render_template("configuracion.html")


def _solo_admin():
    """Administración: administradores de Invoke y cluster-admin; cluster-viewer la ve sin cambiar nada."""
    acc = g.get("acceso") or {}
    if g.usuario.get("es_admin") or acc.get("admin_total"):
        return
    if acc.get("gobierno") and request.method == "GET":
        return
    abort(403)


def _perfil_form(f):
    return {"modo": f.get("modo", "lectura"), "aprobador": int(bool(f.get("aprobador"))), "segmentos": f.getlist("segmentos") or ["*"]}


@bp.route("/configuracion/usuarios", methods=["GET", "POST"])
@requiere_login
def configuracion_usuarios():
    """Administración › Usuarios y perfiles: locales (se administran aquí; también se cargan desde HTPasswd) y externos
    (Active Directory, LDAP o el IGA midPoint: los administra su fuente). Cada fila dice de qué fuente viene."""
    from . import fuentes_usuarios as fu
    _solo_admin()
    cid = g.cliente["id"]
    from . import rbac
    if rbac.activo(cid) and not request.args.get("clasico"):
        return _usuarios_rbac(cid)
    resultado = None
    if request.method == "POST" and request.form.get("accion") == "htpasswd":
        arch = request.files.get("archivo")
        texto = (arch.read().decode("utf-8", "replace") if arch and arch.filename else "") or request.form.get("texto", "")
        resultado = fu.cargar_htpasswd(cid, texto, _perfil_form(request.form), bool(request.form.get("reemplazar")))
        auditar(g.usuario["usuario"], cid, "cargar usuarios desde HTPasswd", "usuarios",
                {"creados": resultado["creados"], "actualizados": resultado["actualizados"], "omitidos": [x[0] for x in resultado["omitidos"]]})
    from . import grupos_dueno as GD
    if request.method == "POST" and (request.form.get("accion") or "").startswith("grupo_"):
        f, ac = request.form, request.form.get("accion")
        try:
            if ac == "grupo_correo":
                GD.guardar_grupo(cid, f.get("seccion"), f.get("correo"), f.get("descripcion"), g.usuario["usuario"])
            elif ac == "grupo_poner":
                GD.poner_miembro(cid, f.get("seccion"), int(f.get("usuario_id") or 0), f.get("rol") or "miembro", g.usuario["usuario"])
            elif ac == "grupo_quitar":
                GD.quitar_miembro(cid, f.get("seccion"), int(f.get("usuario_id") or 0), g.usuario["usuario"])
            flash(f"Grupo «Dueño de {GD.nombre(f.get('seccion'))}» actualizado", "ok")
        except ValueError as e:
            flash(str(e), "error")
        return redirect(url_for("consola.configuracion_usuarios") + "#g-" + (f.get("seccion") or ""))
    fs = fu.fuentes(cid)
    fs_id = {f["id"]: f for f in fs}
    usuarios_ = q("SELECT u.id, u.usuario, u.nombre, u.correo, u.activo, u.ultimo_ingreso, u.origen, u.expira, p.modo, p.aprobador, p.segmentos "
                  "FROM perfiles p JOIN usuarios u ON u.id=p.usuario_id WHERE p.cliente_id=? ORDER BY u.nombre", cid)
    for x in usuarios_:
        x["segmentos"] = j(x["segmentos"], [])
        x["externo"] = fu.es_externo(x)
        x["fuente"] = fu.etiqueta_origen(x, fs_id)
    origen = request.args.get("fuente", "")
    if origen:
        usuarios_ = [x for x in usuarios_ if (x["origen"] or "local") == origen or (origen == "local" and x["origen"] in ("local", "htpasswd", None))]
    for f in fs:
        f["n_usuarios"] = sum(1 for x in usuarios_ if x["origen"] == f"fuente:{f['id']}")
    segs = [r["nombre"] for r in q("SELECT nombre FROM segmentos WHERE cliente_id=? ORDER BY orden, nombre", cid)]
    grupos_ = GD.grupos(cid)
    por_u = {}
    for gr in grupos_:
        for m in gr["todos"]:
            por_u.setdefault(m["id"], []).append({"clave": gr["clave"], "nombre": gr["nombre"], "rol": m["rol"]})
    for x in usuarios_:
        x["grupos"] = por_u.get(x["id"], [])
    return render_template("configuracion_usuarios.html", grupos_d=grupos_, roles_g=GD.ROLES, usuarios=usuarios_, fuentes=fs, tipos=fu.TIPOS, segs=segs, resultado=resultado, origen=origen,
                           n_locales=sum(1 for x in usuarios_ if not x["externo"]))


def _usuarios_rbac(cid):
    """Usuarios y grupos al estilo OpenShift: lista de usuarios, pestaña de grupos y de roles. Al elegir un usuario: sus grupos y
    su acceso efectivo por funcionalidad; al elegir un grupo: sus miembros y sus roles."""
    from . import fuentes_usuarios as fu
    from . import rbac
    if request.method == "POST":
        if not (g.usuario.get("es_admin") or (g.get("acceso") or {}).get("admin_total")):
            abort(403)
        f, ac = request.form, request.form.get("accion", "")
        afectados = set()
        try:
            if ac == "grupo_crear":
                gr = rbac.crear_grupo(cid, f.get("nombre"), f.get("descripcion", "")[:200], f.get("correo", "")[:120])
                auditar(g.usuario["usuario"], cid, "crear grupo", gr["nombre"], {})
                return redirect(url_for("consola.configuracion_usuarios", tab="grupos", gid=gr["id"]))
            gid = int(f.get("grupo_id") or 0)
            gr = q1("SELECT * FROM rbac_grupos WHERE id=? AND cliente_id=?", gid, cid) if gid else None
            if ac in ("miembro_poner", "miembro_quitar"):
                uid_ = int(f.get("usuario_id") or 0)
                if not gr or not q1("SELECT id FROM usuarios WHERE id=?", uid_):
                    raise ValueError("Grupo o usuario no válido")
                (rbac.poner if ac == "miembro_poner" else rbac.quitar)(gid, uid_)
                afectados.add(uid_)
                auditar(g.usuario["usuario"], cid, "poner en grupo" if ac == "miembro_poner" else "quitar de grupo", gr["nombre"], {"usuario_id": uid_})
            elif ac == "enlace_crear":
                suj, sid = f.get("sujeto"), int(f.get("sujeto_id") or 0)
                amb, rol = f.get("ambito"), f.get("rol")
                if amb == "cluster":
                    rol = f.get("rol_cluster")
                rbac.enlazar(cid, suj, sid, amb, rol, g.usuario["usuario"])
                afectados |= ({x["usuario_id"] for x in q("SELECT usuario_id FROM rbac_miembros WHERE grupo_id=?", sid)} if suj == "grupo" else {sid})
                auditar(g.usuario["usuario"], cid, "asignar rol", f"{suj}:{sid}", {"ambito": amb, "rol": rol})
            elif ac == "enlace_quitar":
                e_ = q1("SELECT * FROM rbac_enlaces WHERE id=? AND cliente_id=?", int(f.get("enlace_id") or 0), cid)
                if e_:
                    if e_["rol"] == "cluster-admin" and e_["sujeto"] == "grupo" and g.usuario["id"] in {x["usuario_id"] for x in q("SELECT usuario_id FROM rbac_miembros WHERE grupo_id=?", e_["sujeto_id"])} and not g.usuario.get("es_admin"):
                        raise ValueError("No puedes quitarte tu propio cluster-admin")
                    rbac.desenlazar(cid, e_["id"])
                    afectados |= ({x["usuario_id"] for x in q("SELECT usuario_id FROM rbac_miembros WHERE grupo_id=?", e_["sujeto_id"])} if e_["sujeto"] == "grupo" else {e_["sujeto_id"]})
                    auditar(g.usuario["usuario"], cid, "quitar rol", f"{e_['sujeto']}:{e_['sujeto_id']}", {"ambito": e_["ambito"], "rol": e_["rol"]})
            elif ac == "rol_cluster_crear":
                k = rbac.crear_rol_cluster(cid, f.get("clave"), f.get("rol"), f.getlist("espacios"), f.get("descripcion", ""))
                auditar(g.usuario["usuario"], cid, "crear rol de clúster", k, {"rol": f.get("rol"), "espacios": f.getlist("espacios")})
            for u_ in afectados:
                rbac.sincronizar_perfil(u_, cid)
            flash("Accesos actualizados", "ok")
        except ValueError as e:
            flash(str(e), "error")
        return redirect(request.full_path.rstrip("?"))
    fs_id = {x["id"]: x for x in fu.fuentes(cid)}
    grupos_ = q("SELECT * FROM rbac_grupos WHERE cliente_id=? ORDER BY nombre", cid)
    miembros = q("SELECT m.* FROM rbac_miembros m JOIN rbac_grupos g ON g.id=m.grupo_id WHERE g.cliente_id=?", cid)
    enlaces = q("SELECT * FROM rbac_enlaces WHERE cliente_id=? ORDER BY ambito='cluster' DESC, ambito, rol", cid)
    ids_u = {m["usuario_id"] for m in miembros} | {p_["usuario_id"] for p_ in q("SELECT usuario_id FROM perfiles WHERE cliente_id=?", cid)}
    usuarios_ = q(f"SELECT * FROM usuarios WHERE id IN ({','.join('?' * len(ids_u))}) ORDER BY nombre", *ids_u) if ids_u else []
    gn = {x["id"]: x for x in grupos_}
    for gr in grupos_:
        gr["miembros"] = [m["usuario_id"] for m in miembros if m["grupo_id"] == gr["id"]]
        gr["enlaces"] = [dict(e, txt=rbac.enlaces_txt(e, cid)) for e in enlaces if e["sujeto"] == "grupo" and e["sujeto_id"] == gr["id"]]
    for u_ in usuarios_:
        u_["grupos"] = [gn[m["grupo_id"]] for m in miembros if m["usuario_id"] == u_["id"] and m["grupo_id"] in gn]
        u_["fuente"] = fu.etiqueta_origen(u_, fs_id)
        a_ = rbac.acceso(u_, cid)
        u_["cluster"] = sorted(a_["cluster"])
        u_["espacios"] = sorted(a_["ns"], key=lambda k: [x for x, _ in rbac.ESPACIOS].index(k) if k in rbac.NOMBRE_ESPACIO else 99)
        u_["acc"] = a_
    tab = request.args.get("tab", "usuarios")
    sel_u = next((x for x in usuarios_ if str(x["id"]) == request.args.get("uid", "")), None)
    sel_g = next((x for x in grupos_ if str(x["id"]) == request.args.get("gid", "")), None) if tab == "grupos" else None
    if tab == "grupos" and not sel_g and grupos_:
        sel_g = grupos_[0]
    texto = (request.args.get("q") or "").strip().lower()
    if texto:
        usuarios_ = [x for x in usuarios_ if texto in (x["usuario"] + " " + x["nombre"] + " " + " ".join(gg["nombre"] for gg in x["grupos"])).lower()]
    return render_template("usuarios_rbac.html", usuarios=usuarios_, grupos=grupos_, tab=tab, sel_u=sel_u, sel_g=sel_g, por_id={x["id"]: x for x in usuarios_},
                           todos_u=q(f"SELECT id, usuario, nombre FROM usuarios WHERE id IN ({','.join('?' * len(ids_u))}) ORDER BY nombre", *ids_u) if ids_u else [],
                           espacios=rbac.ESPACIOS, roles_esp=rbac.ROLES_ESPACIO, desc_rol=rbac.DESC_ROL, roles_cl=rbac.roles_cluster(cid), texto=texto,
                           menu_esp={v: k for k, v in reversed(list(rbac.MENU_ESPACIO.items()))}, puede=bool(g.usuario.get("es_admin") or (g.get("acceso") or {}).get("admin_total")),
                           enlaces_u=[dict(e, txt=rbac.enlaces_txt(e, cid)) for e in enlaces if e["sujeto"] == "usuario"])


@bp.route("/ver-como/<int:uid>", methods=["POST"])
@requiere_login
def ver_como(uid):
    """cluster-admin: ver FaceOps como otro usuario (solo lectura) para probar sus accesos."""
    if not (g.usuario.get("es_admin") or (g.get("acceso") or {}).get("admin_total")):
        abort(403)
    u_ = q1("SELECT usuario FROM usuarios WHERE id=?", uid) or abort(404)
    session["ver_como"] = uid
    auditar(g.usuario["usuario"], g.cliente["id"], "ver como", u_["usuario"], {})
    return redirect(url_for("consola.inicio"))


@bp.route("/ver-como/salir", methods=["POST", "GET"])
@requiere_login
def ver_como_salir():
    session.pop("ver_como", None)
    return redirect(url_for("consola.configuracion_usuarios"))


@bp.route("/configuracion/usuarios/nuevo", methods=["GET", "POST"])
@bp.route("/configuracion/usuarios/<int:uid>", methods=["GET", "POST"])
@requiere_login
def configuracion_usuario(uid=None):
    """Un usuario local: alta, datos, clave, vigencia, estado y perfil. Un externo se muestra solo para consulta."""
    from . import fuentes_usuarios as fu
    _solo_admin()
    cid = g.cliente["id"]
    u = None
    if uid:
        u = q1("SELECT u.*, p.modo, p.aprobador, p.segmentos FROM usuarios u JOIN perfiles p ON p.usuario_id=u.id AND p.cliente_id=? WHERE u.id=?", cid, uid)
        if not u:
            abort(404)
        u["segmentos_l"] = j(u["segmentos"], [])
    externo = bool(u and fu.es_externo(u))
    if request.method == "POST":
        f = request.form
        if externo:
            flash("Este usuario lo administra su fuente externa: cámbialo allá", "error")
            return redirect(url_for("consola.configuracion_usuario", uid=uid))
        if f.get("accion") == "quitar":
            if uid == g.usuario["id"]:
                flash("No puedes quitarte el acceso a ti mismo", "error")
                return redirect(url_for("consola.configuracion_usuario", uid=uid))
            ej("DELETE FROM perfiles WHERE usuario_id=? AND cliente_id=?", uid, cid)
            if not q1("SELECT 1 AS x FROM perfiles WHERE usuario_id=?", uid):
                ej("UPDATE usuarios SET activo=0 WHERE id=?", uid)
            auditar(g.usuario["usuario"], cid, "quitar acceso de usuario", u["usuario"], {})
            flash(f"{u['usuario']} ya no tiene acceso a {g.cliente['nombre']}", "ok")
            return redirect(url_for("consola.configuracion_usuarios"))
        clave = f.get("clave", "")
        if clave and (len(clave) < 10 or clave != f.get("clave2", "")):
            flash("La clave debe tener al menos 10 caracteres y coincidir en ambos campos", "error")
            return redirect(request.url)
        activo = int(bool(f.get("activo")))
        if uid == g.usuario["id"] and not activo:
            flash("No puedes desactivarte a ti mismo", "error")
            return redirect(request.url)
        expira = (f.get("expira") + "T23:59:59+00:00") if f.get("expira") else ""
        try:
            if u:
                ej("UPDATE usuarios SET nombre=?, correo=?, activo=?, expira=? WHERE id=?", f["nombre"].strip(), f.get("correo", "").strip(), activo, expira, uid)
                if clave:
                    ej("UPDATE usuarios SET clave_hash=?, origen='local' WHERE id=?", generate_password_hash(clave), uid)
            else:
                nom = f.get("usuario", "").strip().lower()
                if not re.fullmatch(r"[a-z0-9._@-]{2,64}", nom):
                    raise ValueError("Usuario inválido: minúsculas, números y . _ - @")
                if not clave:
                    raise ValueError("Define la clave inicial")
                uid = fu.crear_local(cid, {"usuario": nom, "nombre": f["nombre"].strip() or nom, "correo": f.get("correo", "").strip(), "activo": activo,
                                           "expira": expira}, clave=clave)
            fu.guardar_perfil(uid, cid, f.get("modo"), f.get("aprobador"), f.getlist("segmentos") or ["*"])
        except ValueError as e:
            flash(str(e), "error")
            return redirect(request.url)
        auditar(g.usuario["usuario"], cid, "guardar usuario local", f.get("usuario") or u["usuario"],
                {"modo": f.get("modo"), "aprobador": bool(f.get("aprobador")), "segmentos": f.getlist("segmentos"), "activo": activo, "clave": bool(clave)})
        flash("Usuario guardado", "ok")
        return redirect(url_for("consola.configuracion_usuario", uid=uid))
    segs = [r["nombre"] for r in q("SELECT nombre FROM segmentos WHERE cliente_id=? ORDER BY orden, nombre", cid)]
    return render_template("configuracion_usuario.html", u=u, externo=externo, fuente_txt=fu.etiqueta_origen(u) if u else "Local", segs=segs,
                           fuente=fu.fuente_de(u) if externo else None)


@bp.route("/configuracion/fuentes/nueva/<tipo>", methods=["GET", "POST"])
@bp.route("/configuracion/fuentes/<int:fid>", methods=["GET", "POST"])
@requiere_login
def configuracion_fuente(tipo=None, fid=None):
    """Una fuente externa de usuarios (Active Directory, LDAP o el IGA midPoint): conexión, cuenta de servicio (en la bóveda),
    mapeo de grupos/roles a perfiles de FaceOps, prueba y sincronización. Desde aquí y desde Integraciones."""
    from . import fuentes_usuarios as fu
    _solo_admin()
    cid = g.cliente["id"]
    f_ = fu.fuente(fid, cid) if fid else None
    if fid and not f_:
        abort(404)
    tipo = f_["tipo"] if f_ else tipo
    if tipo not in fu.TIPOS:
        abort(404)
    if request.method == "POST":
        fm = request.form
        acc = fm.get("accion", "guardar")
        try:
            if acc == "guardar":
                conf = {k: fm.get(k, "").strip() for k in ("url", "base_dn", "bind_dn", "filtro", "filtro_lista", "attr_usuario", "attr_nombre", "attr_correo", "attr_grupos", "directorio")}
                conf["verificar_tls"] = bool(fm.get("verificar_tls"))
                conf["starttls"] = bool(fm.get("starttls"))
                if not conf["url"].startswith(("ldap://", "ldaps://", "https://", "http://")):
                    raise ValueError("La URL debe empezar con ldaps://, ldap:// o https://")
                mapeo = []
                for i_ in range(int(fm.get("n_mapeo") or 0) + 1):
                    gr = fm.get(f"m_grupo_{i_}", "").strip()
                    if gr:
                        mapeo.append({"grupo": gr, "modo": fm.get(f"m_modo_{i_}", "lectura"), "aprobador": bool(fm.get(f"m_aprobador_{i_}")),
                                      "segmentos": fm.getlist(f"m_segmentos_{i_}") or ["*"]})
                datos = (fm.get("nombre", "").strip() or fu.TIPOS[tipo]["nombre"], json.dumps(conf, ensure_ascii=False), json.dumps(mapeo, ensure_ascii=False),
                         int(bool(fm.get("activo"))))
                nueva = not f_
                if f_:
                    ej("UPDATE fuentes_usuarios SET nombre=?, conf=?, mapeo=?, activo=? WHERE id=?", *datos, fid)
                else:
                    fid = ej("INSERT INTO fuentes_usuarios(cliente_id, nombre, conf, mapeo, activo, tipo, creado) VALUES (?,?,?,?,?,?,?)", cid, *datos, tipo, ahora())
                f_ = fu.fuente(fid, cid)
                aap = AAP.de_cliente(g.cliente)
                fu.credencial_aap(aap, f_, fm.get("clave") or None)   # la clave va solo a la credencial de AAP
                auditar(g.usuario["usuario"], cid, "guardar fuente de usuarios", f_["nombre"], {"tipo": tipo, "mapeo": mapeo, "clave": bool(fm.get("clave"))})
                t = fu.pedir(g.usuario, g.cliente, f_, "conectar", ("Conectar" if nueva else "Actualizar") + f" la fuente de usuarios «{f_['nombre']}» desde Ansible")
                flash(f"Fuente guardada; credencial en AAP. {t['numero']}: Ansible la conecta" + (" (requiere aprobación)" if t["estado"] == "pendiente_aprobacion" else ""), "ok")
            elif acc in ("probar", "sincronizar") and f_:
                t = fu.pedir(g.usuario, g.cliente, f_, acc)
                flash(f"{t['numero']}: Ansible {'prueba la conexión' if acc == 'probar' else 'sincroniza los usuarios'}" + (" (requiere aprobación)" if t["estado"] == "pendiente_aprobacion" else ""), "ok")
            elif acc == "eliminar" and f_:
                t = fu.pedir(g.usuario, g.cliente, f_, "retirar")
                flash(f"{t['numero']}: Ansible retira la fuente y su autenticador; al terminar sus usuarios pierden el acceso", "ok")
        except (ValueError, ConnectionError, PermissionError, ErrorAAP) as e:
            flash(str(e), "error")
        return redirect(url_for("consola.configuracion_fuente", fid=fid) if fid else request.url)
    segs = [r["nombre"] for r in q("SELECT nombre FROM segmentos WHERE cliente_id=? ORDER BY orden, nombre", cid)]
    clave_ok = False
    en_curso = None
    if f_:
        try:
            clave_ok = bool((AAP.de_cliente(g.cliente).get("credentials/", name=fu.nombre_credencial(f_)).get("results") or []))
        except ErrorAAP:
            pass
        a_ = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cid, fu.IDENTIDAD)
        if a_:
            for t_ in q("SELECT id, numero, estado, parametros FROM tickets WHERE cliente_id=? AND automatizacion_id=? ORDER BY id DESC LIMIT 20", cid, a_["id"]):
                if (j(t_["parametros"], {}) or {}).get("fu_id") == f_["id"]:
                    en_curso = t_
                    break
    directorios = [x for x in fu.fuentes(cid) if x["tipo"] in ("ad", "ldap")]
    n_us = q1("SELECT COUNT(*) AS n FROM usuarios WHERE origen=? AND activo=1", f"fuente:{fid}")["n"] if f_ else 0
    return render_template("configuracion_fuente.html", f=f_, tipo=tipo, t=fu.TIPOS[tipo], segs=segs, clave_ok=clave_ok, n_us=n_us, ult_ticket=en_curso, directorios=directorios)


@bp.route("/plataformas/<pid>/vpn/<usuario>.conf")
@requiere_login
def vpn_config(pid, usuario):
    """Configuración del usuario VPN (desde la bóveda). Se entrega una sola vez; otra copia exige revocar y crear de nuevo."""
    from .boveda import leer
    _escritura()
    e, r, cod = _plataforma_de_id(pid)
    if cod != "wireguard" or not re.fullmatch(r"[a-z0-9._-]{2,32}", usuario):
        abort(404)
    d_ = j(e.get("datos"), {})
    if (d_.get("vpn_descargas") or {}).get(usuario):
        flash(f"La configuración de {usuario} ya se descargó ({d_['vpn_descargas'][usuario][:16]}). Para otra copia, revoca el usuario y créalo de nuevo.", "error")
        return redirect(url_for("consola.plataforma_detalle", pid=pid) + "#vpn")
    sec = leer(f"web/{e['nombre']}/vpn-{usuario}") or {}
    if not sec.get("config"):
        abort(404)
    d_.setdefault("vpn_descargas", {})[usuario] = ahora()
    ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d_, ensure_ascii=False), e["id"])
    auditar(g.usuario["usuario"], g.cliente["id"], "descargar configuración VPN", pid, {"usuario": usuario})
    resp = make_response(sec["config"])
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{usuario}.conf"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/sistemas-operativos")
@requiere_login
def sistemas_operativos():
    """Sistemas operativos del parque por familia y versión: cajas y, al elegir una, sus máquinas."""
    grupos = defaultdict(list)
    for e in equipos_visibles():
        if seccion_eq(e) in ("inventario", "hipervisores", "estaciones", "nubes"):
            grupos[_familia_so(e.get("so"))].append(e)
    sel = request.args.get("p", "")
    cajas = [{"clave": c, "t": c, "n": len(v), "res": _resumen_grupo(v)} for c, v in grupos.items()]

    def fam_(t):
        return ("Red Hat Enterprise Linux" if t.startswith("RHEL") else "Windows" if t.lower().startswith("windows")
                else "Sin sistema detectado" if t == "Sin sistema detectado" else "Otras distribuciones Linux")

    def ver_(t):
        return [-int(x) for x in re.findall(r"\d+", t)] or [0]
    orden_ = ["Red Hat Enterprise Linux", "Otras distribuciones Linux", "Windows", "Sin sistema detectado"]
    for c in cajas:   # una sola lista: la familia va en su columna
        c["familia"] = fam_(c["t"])
    cajas.sort(key=lambda c: (orden_.index(c["familia"]), c["t"] == "Linux sin distribución", re.sub(r"[\d. ]+$", "", c["t"]), ver_(c["t"])))
    secciones = []
    eqs = _con_salud(sorted(grupos.get(sel, []), key=lambda e: (e.get("energia") != "encendido", e["nombre"]))) if sel else []
    _salud_cajas(cajas, grupos)
    srv_so = sorted([e for e in equipos_visibles() if seccion_eq(e) in ("inventario", "nubes", "estaciones") and not _propia_faceops(e)],
                    key=lambda e: (bool((e.get("so") or "").strip()), e["nombre"]))   # las máquinas nuevas, sin sistema, primero
    return render_template("plataformas.html", servidores_inc=srv_so, titulo="Sistemas operativos", desc="Sistemas operativos de servidores, hipervisores y estaciones de trabajo, por familia y versión.",
                           cajas=cajas, secciones=secciones, sel=sel, sel_t=sel, equipos=eqs, ep="consola.sistemas_operativos",
                           radar_sec=_radar_de(grupos.get(sel, []) if sel else [e for v in grupos.values() for e in v]))


@bp.route("/equipos/agregar/<seccion>", methods=["POST"])
@requiere_login
def equipo_agregar(seccion):
    """Alta manual de un equipo que ya existe (servidor, periférico de red o storage): bóveda + host en AAP + equipo en FaceOps."""
    from .alta_manual import agregar
    d = request.get_json(silent=True) or {}
    if d.get("_csrf") != session.get("csrf"):
        return {"error": "Sesión vencida: recarga la página"}, 400
    if g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    if seccion not in ("redes", "storage", "inventario", "estaciones"):
        abort(404)
    try:
        eid = agregar(g.usuario, g.cliente, seccion, d)
    except ValueError as x:
        return {"error": str(x)}, 400
    except ErrorAAP as x:
        return {"error": f"AAP no aceptó el registro: {x}"}, 400
    return {"ok": True, "url": url_for("consola.equipo", eid=eid)}


TIPOS_HIPERVISOR = ("kvm", "vmware", "vsphere", "hyperv", "proxmox", "nutanix", "openshift-virt")


@bp.route("/inventario", methods=["GET", "POST"])
@requiere_login
def inventario(seccion="inventario"):
    ep_inv = SECCION_EP.get(seccion, "consola.inventario")
    tipo_q = request.args.get("tipo", "")
    destino_ = next((sec for sec, ts in SECCION_TIPOS.items() if tipo_q in ts), None)
    if request.method == "GET" and tipo_q and destino_ and destino_ != seccion:   # el tipo vive en otra sección del menú
        return redirect(url_for(SECCION_EP[destino_], **request.args.to_dict()))

    def base_inv():
        return [e for e in equipos_visibles() if seccion_eq(e) == seccion]
    if request.method == "POST":
        if g.perfil["modo"] != "escritura":
            abort(403)
        try:
            if request.form.get("accion") == "recolectar":
                n = recolectar(g.cliente, g.usuario["usuario"])
                flash(f"Recolector lanzado en AAP ({len(n)} trabajos). En unos minutos usa «Sincronizar».", "ok")
            else:
                n = sincronizar(g.cliente, g.usuario["usuario"])
                m = sincronizar_catalogo(g.cliente, g.usuario["usuario"])
                flash(f"Inventario sincronizado desde AAP: {n} equipos · catálogo: {m} automatizaciones", "ok")
        except ErrorAAP as e:
            flash(str(e), "error")
        return redirect(url_for(ep_inv))
    eqs = base_inv()
    hs = hallazgos_visibles(eqs)
    por_eq = defaultdict(list)
    for h in hs:
        por_eq[h["equipo_id"]].append(h)
    texto = request.args.get("q", "").strip()
    entendido = {}
    if texto:
        from .buscador import buscar_equipos
        entendido, eqs = buscar_equipos(texto, g.cliente["id"], eqs, por_eq)
    filtros = {k: request.args.get(k, "") for k in ("infra", "ambiente", "segmento", "skill", "estado", "tipo")}
    fi_ = request.args.get("infra", "")
    from .infraestructuras import ambito as ambito_infra
    amb_ubi = {x["ubicacion"]: ambito_infra(x["tipo"]) for x in q("SELECT ubicacion, tipo FROM infraestructuras WHERE cliente_id=?", g.cliente["id"])}
    f_amb, f_clase = request.args.get("ambito", ""), request.args.get("clase", "")
    ocp_infra = {x["ubicacion"]: (x["detalle"] or "") for x in q("SELECT ubicacion, detalle FROM infraestructuras WHERE cliente_id=? AND tipo='openshift'", g.cliente["id"])}
    n_clusters = sum(1 for e in equipos_visibles() if grupo_periferico(e) == "clusters")

    def en_infra(e, u):   # un clúster pertenece a su infraestructura OpenShift aunque viva en un centro de datos
        return e["ubicacion"] == u or (u in ocp_infra and grupo_periferico(e) == "clusters" and (n_clusters == 1 or (e.get("ip") and e["ip"] in ocp_infra[u])))
    tipos_todos = Counter(grupo_periferico(e) for e in base_inv() if (not fi_ or en_infra(e, fi_))
                          and (not f_amb or ambito_eq(e, amb_ubi) == f_amb) and (not f_clase or clase_eq(e) == f_clase))
    eqs = [e for e in eqs if (not f_amb or ambito_eq(e, amb_ubi) == f_amb) and (not f_clase or clase_eq(e) == f_clase)]
    if filtros["tipo"]:
        eqs = [e for e in eqs if grupo_periferico(e) == filtros["tipo"]]
    if filtros["infra"]:
        eqs = [e for e in eqs if en_infra(e, filtros["infra"])]
    if filtros["segmento"]:
        eqs = [e for e in eqs if filtros["segmento"] in e["segmentos"]]
    f_error = request.args.get("error", "")
    if f_error.isdigit():   # máquinas a las que aplica un error conocido (desde Incidentes › Aplicabilidad)
        ids_ = set(_aplicabilidad_errores().get(int(f_error), {}).get("equipos", []))
        eqs = [e for e in eqs if e["id"] in ids_]
    f_forma = request.args.get("forma", "")
    if f_forma in ("fisico", "virtual"):   # servidores físicos o virtuales
        eqs = [e for e in eqs if ((e.get("forma") or "") == "virtual") == (f_forma == "virtual")]
    cajas_marca, marca_sel = [], request.args.get("marca", "")
    if f_forma == "fisico":   # servidores físicos: primero por marca; al entrar a una, sus nodos
        from .hardware_fisico import agrupar, marca_de
        cajas_marca = [dict({k: v for k, v in x.items() if k != "equipos"}, n=len(x["equipos"]), res=_resumen_grupo(x["equipos"])) for x in agrupar(eqs)]
        if marca_sel:
            eqs = [e for e in eqs if marca_de(e)["clave"] == marca_sel]
    cajas_so = []
    if seccion == "inventario":   # cajas por sistema operativo para filtrar la lista de servidores
        cnt_so = Counter(_familia_so(e.get("so")) for e in eqs)
        cajas_so = sorted(cnt_so.items(), key=lambda x: (x[0] == "Sin sistema detectado", x[0]))
        if request.args.get("so"):
            eqs = [e for e in eqs if _familia_so(e.get("so")) == request.args.get("so")]
    sin_segmento = request.args.get("sin_segmento") == "1"
    if sin_segmento:   # máquinas huérfanas: no están en ningún segmento de negocio (se asignan en el mapa)
        eqs = [e for e in eqs if not e["segmentos"]]
    if filtros["skill"]:
        eqs = [e for e in eqs if filtros["skill"] in e["skills"]]
    if filtros["ambiente"]:
        eqs = [e for e in eqs if e["ambiente"] == filtros["ambiente"]]
    if filtros["estado"] == "con-hallazgos":
        eqs = [e for e in eqs if por_eq.get(e["id"])]
    todos = base_inv()
    try:   # paginado: solo se calcula la salud y el acceso de la página visible
        por_pag = max(5, min(200, int(request.args.get("n", 10))))
    except ValueError:
        por_pag = 10
    eqs = sorted(eqs, key=lambda e: (e.get("energia") != "encendido", (e.get("nombre") or "").lower()))   # encendidos primero
    n_filtrados = len(eqs)
    radar_sec = _radar_de(eqs)   # los 5 principios del radar de salud sobre lo que se está viendo
    paginas = max(1, -(-n_filtrados // por_pag))
    try:
        pag = max(1, min(paginas, int(request.args.get("p", 1))))
    except ValueError:
        pag = 1
    eqs = eqs[(pag - 1) * por_pag: pag * por_pag]
    from .consola import disponible, tipo_consola
    from .inventario import sistema_eq
    for e in eqs:
        e["consola"] = tipo_consola(e, sistema_eq(e)) if disponible() and g.perfil["modo"] == "escritura" else None
        if (e.get("categoria") or "") == "clúster openshift":   # acceso: la consola web de OpenShift (la publicada, si la infraestructura la declara)
            inf_ = q1("SELECT detalle FROM infraestructuras WHERE cliente_id=? AND tipo='openshift' AND ubicacion=?", g.cliente["id"], e.get("ubicacion") or "")
            e["ocp_web"] = j((inf_ or {}).get("detalle"), {}).get("consola") or (j(e.get("datos"), {}).get("ocp") or {}).get("consola") or ""
    from .infraestructuras import lista as lista_infra
    infras = lista_infra(g.cliente["id"], todos)
    from .boveda import disponible as bov_ok, maquinas_con_credencial
    bov_conectada = bov_ok()
    vinc = maquinas_con_credencial([e["nombre"] for e in eqs]) if bov_conectada else set()
    from .inventariado import antiguedad as antig_inv
    for e in eqs:
        e["inv"] = antig_inv(e)
    for e in eqs:
        e["ambito"], e["clase"] = ambito_eq(e, amb_ubi), clase_eq(e)
        e["energia_ok"] = energia_posible(e) and g.perfil["modo"] == "escritura"
        e["vinculada"] = e["nombre"] in vinc if bov_conectada else None   # credencial propia en la bóveda conectada a AAP
    # tickets en curso que cambian el estado o el vínculo (bolita amarilla en la lista)
    en_curso = q("SELECT t.id, t.numero, t.parametros, t.motivo, a.nombre_aap FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
                 "WHERE t.cliente_id=? AND t.estado IN ('pendiente_aprobacion','en_cola','en_ejecucion') AND a.nombre_aap IN (?, ?) "
                 "ORDER BY t.estado != 'pendiente_aprobacion', t.id",
                 g.cliente["id"], ENERGIA, "FaceOps · Vincular máquina a la bóveda")
    # último ticket de encender/apagar y de vincular de cada máquina (se muestra al lado de su bolita)
    ultimos = q("SELECT t.id, t.numero, t.estado, t.parametros, t.motivo, t.creado, a.nombre_aap FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
                "WHERE t.cliente_id=? AND a.nombre_aap IN (?, ?) ORDER BY t.id DESC LIMIT 2000", g.cliente["id"], ENERGIA, "FaceOps · Vincular máquina a la bóveda")
    ult_en, ult_vi = {}, {}
    for t in ultimos:
        if t["nombre_aap"] == ENERGIA:
            ult_en.setdefault(j(t["parametros"], {}).get("en_equipo"), t)
        else:
            m_ = re.match(r"Vincular (.+?) a la bóveda", t["motivo"] or "")
            if m_:
                ult_vi.setdefault(m_.group(1), t)
    for e in eqs:
        e["ult_energia"], e["ult_vinculo"] = ult_en.get(e["nombre"]), ult_vi.get(e["nombre"])
        e["vinc_no"] = "Esta máquina no se vincula a la bóveda (anfitrión de la plataforma)" \
            if "kvm" in (e.get("skills") or []) else \
            ("Sin IP todavía: se podrá vincular cuando el hipervisor le asigne una dirección" if not e.get("ip") and (e.get("categoria") or "") != "clúster openshift" else None)
    for e in eqs:
        e["proc_energia"] = next((t for t in en_curso if t["nombre_aap"] == ENERGIA and j(t["parametros"], {}).get("en_equipo") == e["nombre"]), None)
        e["proc_vinculo"] = next((t for t in en_curso if t["nombre_aap"] != ENERGIA and f"Vincular {e['nombre']} a" in (t["motivo"] or "")), None)
    try:   # riesgo ante fallas de cada máquina (errores conocidos de su tecnología, activos y prevenidos)
        from .riesgo import evaluar as evaluar_riesgo
        from .salud import de_equipo as salud_de
        from . import kb as kb_
        prods_ = kb_.q("SELECT codigo, nombre, skill, detecta FROM productos") if kb_.disponible() else []
        try:   # CVE y políticas de la página visible (para la salud)
            from .cves import por_equipo as _cves_por
            from .politicas_seg import tablero as _tablero
            cves_pag = _cves_por(g.cliente["id"], eqs)
            pols_pag = _tablero(g.cliente["id"], eqs)[1]
        except Exception:  # noqa: BLE001
            cves_pag, pols_pag = {}, {}
        for e in eqs:
            r_ = evaluar_riesgo(g.cliente["id"], e) if e["estado"] == "con datos" else None
            e["riesgo"] = {k: r_[k] for k in ("riesgo", "nivel", "activos", "prevenidos", "en_deuda")} | {"n": len(r_["errores"])} if r_ and r_["errores"] else None
            nom_p = {p_["codigo"]: p_["nombre"] for p_ in prods_}
            e["plats"] = [nom_p.get(c_, c_) for c_ in (r_ or {}).get("productos", []) if c_ != "rhel"]   # las mismas que su ficha
            from .identidad import plataformas as _plats
            e["plats"] += [x for x in _plats(e) if not any(x.lower() in y.lower() or y.lower() in x.lower() for y in e["plats"])]
            if r_:
                s_ = salud_de(e, r_, prods_, {"cves": cves_pag.get(e["id"]), "politicas": pols_pag.get(e["id"], [])})
                e["salud"] = {"salud": s_["salud"], "nivel": s_["nivel"], "peor": (s_.get("peor") or {}).get("nombre"),
                              "comps": [(c["nombre"], c["salud"]) for c in s_["componentes"]]}
    except Exception:  # noqa: BLE001
        pass
    cuenta_amb = Counter(ambito_eq(e, amb_ubi) for e in todos)
    cuenta_clase = Counter(clase_eq(e) for e in todos if not f_amb or ambito_eq(e, amb_ubi) == f_amb)
    vault_auto = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Vault · Preparar servidor'", g.cliente["id"])
    auto_crear = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "FaceOps · Habilitar servidor")
    infras_crear = q("SELECT nombre, tipo, region, ubicacion FROM infraestructuras WHERE cliente_id=? AND tipo IN ('kvm','vmware','gcp') ORDER BY id", g.cliente["id"])
    return render_template("inventario.html", cajas_marca=cajas_marca, marca_sel=marca_sel, radar_sec=radar_sec, cajas_so=cajas_so, so_sel=request.args.get("so", ""), auto_crear=auto_crear, infras_crear=infras_crear, sin_segmento=sin_segmento, bov_conectada=bov_conectada, vault_auto=vault_auto, pag=pag, paginas=paginas, por_pag=por_pag, n_filtrados=n_filtrados, infras=infras, f_amb=f_amb, f_clase=f_clase, cuenta_amb=cuenta_amb, cuenta_clase=cuenta_clase,
                           clases=CLASES, seccion=seccion, ep_inv=ep_inv, alta=(__import__("app.alta_manual", fromlist=["catalogo"]).catalogo(seccion) if seccion in ("redes", "storage", "inventario", "estaciones") else None),
                           ubicaciones_alta=sorted({x["centro"] for x in infras if x.get("centro")}), portada=_portada_inv(seccion, todos, tipos_todos, ep_inv), infras_todas=(_infras_con_salud(lista_infra(g.cliente["id"], equipos_visibles())) if seccion in ("nubes", "hipervisores") else []),
                           cajas_eq=(todos if seccion == "storage" else []),
                           tipos=[(k, n, tipos_todos.get(k, 0)) for k, n, _ in PERIFERICOS
                                  if ((k in SECCION_TIPOS[seccion] or (not SECCION_TIPOS[seccion] and tipos_todos.get(k))) if seccion in SECCION_TIPOS else (k not in set().union(*SECCION_TIPOS.values()) or tipos_todos.get(k)))],
                           seccion_tit=SECCION_TIT.get(seccion), tipo_sel=filtros["tipo"], equipos=eqs, por_eq=por_eq, filtros=filtros, texto=texto, ent=entendido, total=len(todos),
                           opciones={"infra": sorted({e["ubicacion"] for e in todos if e["ubicacion"]}),
                                     "segmento": sorted({s for e in todos for s in e["segmentos"]}),
                                     "skill": sorted({s for e in todos for s in e["skills"]}),
                                     "ambiente": sorted({e["ambiente"] for e in todos if e["ambiente"]})},
                           ultima=max([e["ultima_recoleccion"] or "" for e in todos] or [""]))


@bp.route("/inventario/<int:eid>/ejecutar/<int:aid>", methods=["POST"])
@requiere_login
def ejecutar_en_equipo(eid, aid):
    """Lanza una acción sobre este equipo sin salir de su detalle (varias a la vez). Responde JSON."""
    from .operacion import solicitar_en_equipo
    if request.form.get("_csrf") != session.get("csrf"):
        abort(400)
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE id=? AND cliente_id=?", aid, g.cliente["id"])
    if not e or not a:
        abort(404)
    params, secretos = {}, {}
    for c in j(a["campos"], []):
        if c["tipo"] in ("equipo", "equipos"):
            params[c["var"]] = e["nombre"] if c["tipo"] == "equipo" else [e["nombre"]]
            continue
        v = request.form.getlist("v_" + c["var"]) if c["tipo"] == "multiselect" else request.form.get("v_" + c["var"], "").strip()
        if v in ("", []) and ("v_" + c["var"]) not in request.form and c.get("defecto") not in (None, ""):
            v = c["defecto"]  # lanzado desde el mapa o un hallazgo sin formulario: va con el valor por defecto
        if c["tipo"] == "integer" and v != "":
            v = int(v)
        if v in ("", []):
            if c.get("requerido"):
                return {"error": f"Falta «{c['etiqueta']}»"}, 400
            continue
        if c["tipo"] == "password":
            secretos[c["var"]], params[c["var"]] = v, "••••••"
        else:
            params[c["var"]] = v
    try:
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, params, request.form.get("motivo", "").strip() or f"Lanzado desde el detalle de {e['nombre']}", secretos)
    except (PermissionError, ErrorAAP) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}


@bp.route("/tickets/<int:tid>/estado")
@requiere_login
def ticket_estado(tid):
    t = q1("SELECT numero, estado, segmento, dur_ejecucion_s, resultado FROM tickets WHERE id=? AND cliente_id=?", tid, g.cliente["id"])
    if not t or (t["segmento"] and not puede_ver_segmento(g.usuario, g.cliente["id"], t["segmento"])):
        abort(404)
    return {"numero": t["numero"], "estado": t["estado"], "ejecucion_s": t["dur_ejecucion_s"],
            "resultado": (t["resultado"] or "")[:300]}


def _proxy_web(e, w, sub, prefijo, transparente=False, auth=None):
    """Proxy simple a la consola web interna de un equipo (reescribe enlaces y redirecciones salvo en modo transparente,
    cuando la aplicación ya sabe que vive bajo el prefijo, como Redis Insight con RI_PROXY_PATH)."""
    import re
    import requests
    base = w["url"].split("/", 3)
    origen = "/".join(base[:3])                      # esquema://ip:puerto
    destino = origen + (((request.script_root or os.environ.get("FACEOPS_PREFIJO", "/faceops")) + request.path) if transparente else "/" + (sub or (base[3] if len(base) > 3 else "")))
    socket_io = "socket.io" in (sub or "").split("/")[:2]
    if transparente and socket_io:   # Redis Insight atiende socket.io en la raíz (no bajo su ruta de proxy); el long-polling espera ~25 s
        destino = origen + "/" + sub
    if not sub:
        auditar(g.usuario["usuario"], g.cliente["id"], "abrir consola web", e["nombre"], {"url": w["url"]})
    cab = {k: v for k, v in request.headers if k.lower() in ("accept", "accept-language", "content-type", "user-agent")
           or (w.get("vault") and k.lower().startswith("x-vault-"))}   # Vault: su token viaja en X-Vault-Token
    if w.get("host"):
        cab["Host"] = w["host"]
        cab["Referer"] = w.get("referer") or f"{origen.split(':')[0]}://{w['host']}/"
    if w.get("host_publico"):
        cab.update({"Host": request.host, "X-Forwarded-Host": request.host, "X-Forwarded-Proto": request.headers.get("X-Forwarded-Proto", request.scheme)})
    galletas = {k: v for k, v in request.cookies.items() if k != current_app.config.get("SESSION_COOKIE_NAME", "session")}
    try:
        r = requests.request(request.method, destino, params=request.args, data=request.get_data(), headers=cab, cookies=galletas,
                             timeout=60 if socket_io else 10, verify=False, allow_redirects=False, auth=auth)
    except requests.RequestException as x:
        return render_template("app_caida.html", segmento=e["nombre"], url=destino, error=str(x).split(":")[0][:160]), 502
    tipo = r.headers.get("Content-Type", "application/octet-stream")
    cuerpo = r.content
    if not transparente and ("text/html" in tipo or "text/css" in tipo or "javascript" in tipo):
        txt = cuerpo.decode(r.encoding or "utf-8", "replace").replace(origen, prefijo)
        if w.get("host"):
            txt = txt.replace(f"https://{w['host']}", prefijo).replace(f"http://{w['host']}", prefijo)
        txt = re.sub(r'((?:href|src|action)=["\'])/(?!/)', lambda m: m.group(1) + prefijo + "/", txt)
        for ra in w.get("abs") or []:   # rutas absolutas dentro del JavaScript de la aplicación (p. ej. IdM llama a "/ipa/session/json")
            txt = re.sub(r'(["\'])' + re.escape(ra), lambda m: m.group(1) + prefijo + ra, txt)
        if w.get("vault"):
            from urllib.parse import quote as _q
            txt = (txt.replace("rootURL%22%3A%22%2Fui%2F%22", "rootURL%22%3A%22" + _q(prefijo + "/ui/", safe="") + "%22")
                   .replace("%22/ui/", "%22" + prefijo + "/ui/").replace('b.p="/ui/assets/"', f'b.p="{prefijo}/ui/assets/"')
                   .replace('serviceWorker.register("/ui/sw.js"', 'serviceWorker.getRegistration("/ui/sw.js"'))
            if "text/html" in tipo:   # la API (/v1/...) se pide desde la raíz: fetch y XHR pasan por FaceOps
                shim = ("<script>(()=>{const P=" + json.dumps(prefijo) + ";const f=u=>typeof u==='string'&&u.startsWith('/v1/')?P+u:u;"
                        "const of=window.fetch;window.fetch=(u,o)=>of(u instanceof Request&&new URL(u.url).pathname.startsWith('/v1/')?new Request(P+new URL(u.url).pathname+new URL(u.url).search,u):f(u),o);"
                        "const ox=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u,...r){return ox.call(this,m,f(u),...r)};})();</script>")
                i_ = txt.lower().find("<head")
                k_ = txt.find(">", i_) + 1 if i_ >= 0 else 0
                txt = txt[:k_] + shim + txt[k_:]
        if "text/html" in tipo:
            franja = (f'<div style="font:13px Arial,sans-serif;background:#151515;color:#fff;padding:6px 14px;border-bottom:3px solid #ee0000">'
                      f'Consola web de <b>{e["nombre"]}</b> ({w["producto"]}) a través de FaceOps · {w["url"]} · HTTP {r.status_code}</div>')
            i = txt.lower().find("<body")
            j_ = txt.find(">", i) + 1 if i >= 0 else 0
            txt = txt[:j_] + franja + txt[j_:]
        cuerpo, tipo = txt.encode("utf-8"), (tipo.split(";")[0] + "; charset=utf-8")
    resp = make_response(cuerpo, r.status_code)
    resp.headers["Content-Type"] = tipo
    resp.headers["Cache-Control"] = "no-store"
    if r.headers.get("Location"):
        loc = r.headers["Location"].replace(origen, prefijo)
        if w.get("host"):
            loc = loc.replace(f"https://{w['host']}", prefijo).replace(f"http://{w['host']}", prefijo)
        resp.headers["Location"] = loc if transparente else (prefijo + loc if loc.startswith("/") and not loc.startswith(prefijo) else loc)
    for c in r.raw.headers.getlist("Set-Cookie") if hasattr(r.raw.headers, "getlist") else []:
        resp.headers.add("Set-Cookie", c if transparente else re.sub(r"(?i)path=/[^;]*", f"Path={prefijo}/", c))
    return resp


@bp.route("/inventario/<int:eid>/web/", defaults={"sub": ""}, methods=["GET", "POST"])
@bp.route("/inventario/<int:eid>/web/<path:sub>", methods=["GET", "POST"])
@requiere_login
def web_equipo(eid, sub):
    """Consola web del equipo a través de FaceOps."""
    from .consola import web_de
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    w = web_de(e) if e else None
    if not w or w["producto"] == "Redis":
        abort(404)
    if not sub:
        auditar(g.usuario["usuario"], g.cliente["id"], "abrir consola web", e["nombre"], {"url": w["url"]})
    return _proxy_web(e, w, sub, url_for("consola.web_equipo", eid=eid, sub="").rstrip("/"))


# consola web propia de cada plataforma (solo las que tienen una de verdad); las demás muestran que no tienen
CONSOLAS = {"jboss": ("http", 9990, "/console/", "Consola de administración de JBoss"), "vault": ("http", 8200, "/ui/", "Vault"),
            "keycloak": ("http", 8080, "/admin/", "Keycloak"), "redis": ("http", 5540, "", "Redis Insight"),
            "jenkins": ("http", 8080, "", "Jenkins"),
            "red-hat-idm": ("https", 443, "/ipa/ui/", "Red Hat IdM")}


FAMILIAS = [("jboss", "jboss"), ("eap", "jboss"), ("wildfly", "jboss"), ("postgres", "postgresql"), ("redis", "redis"), ("valkey", "redis"),
            ("nginx", "nginx"), ("httpd", "httpd"), ("apache", "httpd"), ("tomcat", "tomcat"), ("jws", "tomcat"), ("keycloak", "keycloak"),
            ("rhbk", "keycloak"), ("kafka", "kafka"), ("streams", "kafka"), ("artemis", "activemq"), ("amq", "activemq"), ("vault", "vault"),
            ("mysql", "mysql"), ("mariadb", "mysql")]


def familia(cod):
    """Código del producto en la base de conocimiento (jboss-eap, postgresql-15…) → familia con la que FaceOps lo maneja."""
    c = (cod or "").lower()
    return next((f for k, f in FAMILIAS if k in c), c)


def consola_plataforma(e, cod):
    """{'url', 'producto', 'transparente'} o None. Redis: solo si Redis Insight está instalado en la máquina."""
    cod = familia(cod)
    c = CONSOLAS.get(cod)
    if not c or not e.get("ip"):
        return None
    if cod == "redis" and "redisinsight" not in (e.get("skills") or []):
        return None
    esq, puerto, ruta, prod = c
    if cod == "jenkins" and "jenkins" not in (e.get("skills") or []):
        return None
    if cod == "keycloak":   # la consola existe solo si Keycloak corre en la máquina (no basta con la skill declarada)
        sv_ = " ".join(j(e.get("servicios"), []) if isinstance(e.get("servicios"), str) else (e.get("servicios") or [])).lower()
        if "keycloak" not in sv_ and "rhbk" not in sv_:
            return None
    w = {"url": f"{esq}://{e['ip']}:{puerto}{ruta}", "producto": prod, "transparente": cod in ("redis", "jenkins")}
    if cod == "jenkins":   # Jenkins vive bajo su ruta de FaceOps (--prefix) y arma sus enlaces con el Host público
        w["host_publico"] = True
    if cod == "red-hat-idm":   # IdM solo responde a su nombre (Host y Referer con su FQDN) y redirige a él
        d_ = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
        fq = d_.get("hostname") or e["nombre"]
        if "." not in fq:
            fq = next((x for x in [e.get("nombre")] if x and "." in x), fq)
        w.update(host=fq, referer=f"https://{fq}/ipa/ui/", abs=["/ipa/"], inicio="ipa/ui/")
    if cod == "vault":   # la interfaz de Vault (Ember) vive en /ui/ y llama a /v1/ desde la raíz: se le da su ruta bajo FaceOps
        w.update(inicio="ui/", vault=True)
    return w


@bp.route("/inventario/<int:eid>/consola-web/<cod>/", defaults={"sub": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@bp.route("/inventario/<int:eid>/consola-web/<cod>/<path:sub>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@requiere_login
def consola_web_plataforma(eid, cod, sub):
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    w = consola_plataforma(e, cod) if e and g.perfil["modo"] == "escritura" else None
    if not w:
        abort(404)
    if not sub and request.method == "GET":
        auditar(g.usuario["usuario"], g.cliente["id"], "abrir consola web", e["nombre"], {"plataforma": cod, "url": w["url"]})
    prefijo = url_for("consola.consola_web_plataforma", eid=eid, cod=cod, sub="").rstrip("/")
    if familia(cod) == "keycloak" and not sub and request.method == "GET":   # Keycloak puede vivir en una ruta propia (p. ej. /sso)
        try:
            loc_ = requests.get(f"http://{e['ip']}:8080/", allow_redirects=False, timeout=5).headers.get("Location", "")
        except requests.RequestException:
            loc_ = ""
        rel_ = re.sub(r"^https?://[^/]+", "", loc_).strip("/")
        try:   # si Keycloak tiene publicado su nombre propio (KC_HOSTNAME), su consola se abre ahí: así la sesión y las redirecciones funcionan
            loc2_ = requests.get(f"http://{e['ip']}:8080/{rel_ + '/' if rel_ and not rel_.startswith('admin') else ''}admin/", allow_redirects=False, timeout=5).headers.get("Location", "")
        except requests.RequestException:
            loc2_ = ""
        if loc2_.startswith("http") and e["ip"] not in loc2_:
            return redirect(loc2_)
        return redirect(f"{prefijo}/{rel_ + '/' if rel_ and not rel_.startswith('admin') else ''}admin/")
    if familia(cod) == "vault" and not sub and request.method == "GET":
        # Entrada sin credenciales: FaceOps toma el token de administración de este Vault desde la bóveda central
        # (web/<máquina>/vault), crea un token hijo de 8 h envuelto (válido 2 min, de un solo uso) y la interfaz lo desenvuelve
        from .boveda import web as cred_web
        cr = cred_web(e["nombre"], "vault") or {}
        if cr.get("clave"):
            try:
                r_ = requests.post(w["url"].split("/ui/")[0] + "/v1/auth/token/create", timeout=8,
                                   headers={"X-Vault-Token": cr["clave"], "X-Vault-Wrap-TTL": "120"},
                                   json={"ttl": "8h", "display_name": f"faceops-{g.usuario['usuario']}", "meta": {"faceops_usuario": g.usuario["usuario"]}})
                wt = (r_.json().get("wrap_info") or {}).get("token") if r_.ok else None
            except (requests.RequestException, ValueError):
                wt = None
            if wt:
                auditar(g.usuario["usuario"], g.cliente["id"], "entrar a Vault con token de la bóveda", e["nombre"], {"ttl": "8h"})
                return redirect(f"{prefijo}/ui/vault/auth?with=token&wrapped_token={wt}")
            flash("No se pudo entrar automáticamente a Vault: revisa que esté desellado y que su credencial en la bóveda siga vigente", "error")
        else:
            flash(f"{e['nombre']}: su Vault no tiene credencial en la bóveda central (web/{e['nombre']}/vault); se abre el ingreso manual", "error")
    if w.get("inicio") and not sub and request.method == "GET":   # la aplicación espera vivir en su ruta (rutas relativas)
        return redirect(f"{prefijo}/{w['inicio']}")
    auth = None
    if familia(cod) == "jboss":
        # La consola de JBoss (HAL) busca la API de administración en /management de su mismo origen: se le indica la de
        # FaceOps con ?connect= y FaceOps entra con la credencial de administración guardada en la bóveda (el usuario no la ve)
        if request.method == "GET" and sub in ("", "console", "console/", "console/index.html") and not request.args.get("connect"):
            return redirect(f"{prefijo}/console/index.html?connect=" + quote(request.host_url.rstrip("/") + prefijo, safe=""))
        from .boveda import web as cred_web
        from requests.auth import HTTPDigestAuth
        cr = cred_web(e["nombre"], "jboss") or {}
        if cr.get("usuario") and cr.get("clave") and sub.startswith(("management", "logout")):
            auth = HTTPDigestAuth(cr["usuario"], cr["clave"])
    elif familia(cod) == "jenkins":   # FaceOps entra con el admin guardado en la bóveda (el usuario no ve la clave)
        from .boveda import web as cred_web
        from requests.auth import HTTPBasicAuth
        cr = cred_web(e["nombre"], "jenkins") or {}
        if cr.get("usuario") and cr.get("clave"):
            auth = HTTPBasicAuth(cr["usuario"], cr["clave"])
    return _proxy_web(e, w, sub, prefijo, w["transparente"], auth)


@bp.route("/dueno/<tipo>/<path:clave>", methods=["POST"])
@requiere_login
def asignar_dueno(tipo, clave):
    """Dueño de un componente: máquina (con su sistema operativo) o plataforma."""
    from .duenos import asignar
    _escritura()
    if tipo == "equipo":
        e = next((x for x in equipos_visibles() if str(x["id"]) == clave), None)
        destino = url_for("consola.equipo", eid=int(clave)) if e else None
    elif tipo == "plataforma":
        _plataforma_de_id(clave)
        destino = url_for("consola.plataforma_detalle", pid=clave)
    else:
        destino = None
    if not destino:
        abort(404)
    try:
        d_ = asignar(g.cliente["id"], tipo, clave, request.form, g.usuario["usuario"])
        flash(f"Dueño: {d_['nombre']}" if d_ else "Dueño quitado", "ok")
    except ValueError as x:
        flash(str(x), "error")
    return redirect(destino)


def _acciones_so(e):
    """Acciones propias del sistema operativo de la máquina (convertir a RHEL, incorporar a Satellite…), con sus destinos."""
    so_ = (e.get("so") or "").lower()
    sats = [x for x in equipos_visibles() if "satellite" in (x.get("skills") or []) and x["id"] != e["id"]]
    out = []
    if re.search(CONVERTIBLES, so_):
        out.append({"clave": "convertir-rhel", "t": "Convertir a RHEL", "auto": "Satellite · Convertir a RHEL",
                    "d": "Convierte este sistema a RHEL de la misma versión (Convert2RHEL, snapshot previo y vuelta atrás) y lo deja registrado en el Satellite elegido.",
                    "destinos": [(x["nombre"], f"Satellite {x['nombre']}") for x in sats]})
    elif re.match(r"(red ?hat|rhel)", so_) and "coreos" not in so_:
        ya_ = (j(e.get("datos"), {}).get("satellite") or {}).get("servidor")
        if sats:
            out.append({"clave": "incorporar-rhel", "t": "Incorporar a Satellite", "auto": "Satellite · Incorporar RHEL",
                        "d": "Registra esta máquina en el Satellite elegido: desde ahí recibe contenido, parches y el reporte de vulnerabilidades." + (f" Hoy está en {ya_}." if ya_ else ""),
                        "destinos": [(x["nombre"], f"Satellite {x['nombre']}") for x in sats if x["nombre"] != ya_]})
    for x in out:
        a_ = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], x["auto"])
        x["disponible"] = bool(a_) and bool(x["destinos"])
        x["probada"] = bool(a_) and _certificada(a_)
    return out


@bp.route("/inventario/<int:eid>/accion-so/<clave>", methods=["POST"])
@requiere_login
def equipo_accion_so(eid, clave):
    from .operacion import solicitar
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    acc = next((x for x in _acciones_so(e) if x["clave"] == clave), None) if e else None
    if not acc:
        abort(404)
    try:
        if not acc["probada"]:
            raise ValueError(f"«{acc['t']}» aún no está probada: queda bloqueada hasta probarla")
        sat = next((x for x in equipos_visibles() if x["nombre"] == request.form.get("destino") and x["nombre"] in [d for d, _ in acc["destinos"]]), None)
        if not sat:
            raise ValueError("Elige el Satellite")
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], acc["auto"])
        t = solicitar(g.usuario, g.cliente, a, None, {"sat_equipo": sat["nombre"], "sat_ip": sat.get("ip") or ""},
                      f"{acc['t']} · {e['nombre']} → {sat['nombre']}", equipos=[e])
        flash(f"{acc['t']}: {t['numero']} creado (espera su aprobación)", "ok")
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.equipo", eid=eid))


@bp.route("/inventario/<int:eid>/recursos", methods=["POST"])
@requiere_login
def equipo_recursos(eid):
    """Cambiar vCPU y memoria de una máquina virtual (KVM): ticket «Infra · Ajustar recursos de máquinas» con aprobación."""
    from .operacion import solicitar
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Infra · Ajustar recursos de máquinas'", g.cliente["id"])
    try:
        if not e or (e.get("forma") or "") != "virtual":
            raise ValueError("Solo se pueden ajustar máquinas virtuales")
        if not a:
            raise ValueError("Falta «Infra · Ajustar recursos de máquinas» en el catálogo (sincroniza con AAP)")
        cpu, ram = int(request.form.get("cpu", 0)), int(request.form.get("ram", 0))
        lim = _limites_recursos(e)
        if not lim:
            raise ValueError("La máquina no está en un anfitrión KVM administrado por FaceOps")
        if not (1 <= cpu <= lim["max_cpu"] and 1 <= ram <= lim["max_ram"]):
            raise ValueError(f"En {lim['infra']} se puede asignar hasta {lim['max_cpu']} vCPU y {lim['max_ram']} GB a esta máquina")
        t = solicitar(g.usuario, g.cliente, a, None, {"ir_maquinas": f"{e['nombre']}:{cpu}:{ram * 1024}"},
                      f"Ajustar {e['nombre']} a {cpu} vCPU y {ram} GB")
        flash(f"{t['numero']}: {e['nombre']} a {cpu} vCPU y {ram} GB (espera su aprobación; si está encendida se reinicia)", "ok")
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.equipo", eid=eid))


@bp.route("/inventario/<int:eid>/redis-insight", methods=["POST"])
@requiere_login
def equipo_redis_insight(eid):
    """Instala Redis Insight en la máquina (ticket en AAP); queda publicado bajo la ruta de su consola en FaceOps."""
    from .operacion import solicitar_en_equipo
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Redis · Instalar Redis Insight'", g.cliente["id"])
    if not e or not a:
        flash("Falta «Redis · Instalar Redis Insight» en el catálogo (sincroniza con AAP)", "error")
        return redirect(url_for("consola.equipo", eid=eid) + "#p-redis")
    pre = request.script_root or os.environ.get("FACEOPS_PREFIJO", "/faceops")   # la ruta pública con la que el navegador la pide
    ruta = pre + url_for("consola.consola_web_plataforma", eid=eid, cod="redis", sub="")[len(request.script_root):].rstrip("/")
    try:
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, {"ri_ruta": ruta}, f"Instalar Redis Insight en {e['nombre']}")
        flash(f"Instalando Redis Insight en {e['nombre']} ({t['numero']}): al terminar, el icono de Redis abre su consola", "ok")
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.equipo", eid=eid) + "#p-redis")


SERVICIOS_PLATAFORMA = {"jboss": ["eap8-standalone", "eap7-standalone", "jboss-eap", "wildfly", "jboss"], "nginx": ["nginx"],
                       "postgresql": ["postgresql", "postgresql-16", "postgresql-15", "postgresql-13"], "redis": ["redis", "valkey"],
                       "httpd": ["httpd"], "tomcat": ["tomcat", "jws6-tomcat", "jws5-tomcat"], "keycloak": ["keycloak", "rhbk"],
                       "kafka": ["kafka", "amq-streams"], "activemq": ["artemis", "amq-broker", "activemq"], "mysql": ["mysqld", "mariadb"],
                       # (una unidad que termina en «@» toma cualquier instancia: dirsrv@slapd-dc1…)
                       "389-directory-server": ["dirsrv@"], "red-hat-idm": ["ipa"], "vault": ["vault"], "midpoint": ["midpoint"],
                       "certificate-system": ["pki-tomcatd@"], "gitea": ["gitea"], "registro-paquetes": ["gitea"],
                       "aap": ["automation-controller-web", "automation-controller"], "automation-hub": ["automation-hub-api", "pulpcore-api"],
                       "frrouting": ["frr"], "vyos-firewall": ["vyos-router"], "rabbitmq": ["rabbitmq-server"], "openshift": ["kubelet"],
                       # Windows (servicios de Windows)
                       "active-directory": ["NTDS"], "iis": ["W3SVC"]}


def servicio_estado(e, cod):
    """En ejecución | detenido | None (no se sabe) según los servicios recolectados del equipo."""
    uds = SERVICIOS_PLATAFORMA.get(familia(cod))
    serv = [x.lower() for x in j(e.get("servicios"), [])] if isinstance(e.get("servicios"), str) else [x.lower() for x in (e.get("servicios") or [])]
    if not uds or not serv:
        return None
    if e.get("energia") == "apagado":   # máquina apagada: sus plataformas no corren, aunque la última recolección diga lo contrario
        return "detenido"
    return "en ejecución" if any(u.lower().rstrip("@") in x for u in uds for x in serv) else "detenido"


@bp.route("/tickets/<int:tid>/bitacora")
@requiere_login
def ticket_bitacora(tid):
    """La bitácora del ticket en vivo (fragmento HTML que la ficha refresca mientras el trabajo corre)."""
    t = q1("SELECT * FROM tickets WHERE id=? AND cliente_id=?", tid, g.cliente["id"])
    if not t:
        abort(404)
    from .aprobacion_areas import filtrar_tickets
    if not filtrar_tickets([t], g.usuario, g.cliente["id"]):
        abort(404)
    bitacora, web_aap = [], ""
    if t["aap_job_id"]:
        from .aap import AAP
        from .bitacora import construir
        try:
            cli = AAP.de_cliente(g.cliente)
            web_aap, bitacora = cli.publica, construir(cli, t)
        except ErrorAAP:
            pass
    resp = make_response(render_template("_bitacora.html", bitacora=bitacora, web_aap=web_aap, t=t))
    resp.headers["X-Ticket-Terminado"] = "0" if t["estado"] in ("en_ejecucion", "en_cola", "pendiente_aprobacion") else "1"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/tickets/<int:tid>/cancelar", methods=["POST"])
@requiere_login
def ticket_cancelar(tid):
    """Cancela una solicitud que aún no termina (esperando aprobación, en cola o en ejecución): la cancela también en AAP."""
    _escritura()
    t = q1("SELECT * FROM tickets WHERE id=? AND cliente_id=?", tid, g.cliente["id"])
    if not t or t["estado"] not in ("pendiente_aprobacion", "en_cola", "en_ejecucion"):
        abort(404)
    if t["usuario_id"] != g.usuario["id"] and not puede_aprobar(g.usuario, g.cliente["id"]):
        abort(403)
    motivo = (request.form.get("motivo") or "").strip()[:200]
    if t.get("aap_job_id"):
        try:
            from .aap import AAP
            AAP.de_cliente(g.cliente).post(f"{'workflow_jobs' if t['aap_job_tipo'] == 'workflow' else 'jobs'}/{t['aap_job_id']}/cancel/")
        except Exception as x:  # noqa: BLE001 - si AAP ya lo terminó, igual se cierra en FaceOps
            print("Cancelar en AAP:", str(x)[:200])
    ej("UPDATE tickets SET estado='cancelado', fin=?, resultado=? WHERE id=?", ahora(),
       f"Cancelado por {g.usuario['usuario']}" + (f": {motivo}" if motivo else ""), tid)
    ej("UPDATE ticket_etapas SET estado='cancelada', fin=? WHERE ticket_id=? AND estado='pendiente'", ahora(), tid)
    auditar(g.usuario["usuario"], g.cliente["id"], "cancelar ticket", t["numero"], {"motivo": motivo})
    flash(f"{t['numero']} cancelado", "ok")
    return redirect(request.form.get("volver") or url_for("consola.ticket", tid=tid))


@bp.route("/inventario/<int:eid>/vincular", methods=["POST"])
@requiere_login
def equipo_vincular(eid):
    """Solicitud para vincular la máquina a la bóveda (credencial propia guardada en la bóveda conectada a AAP)."""
    from .inventario import sistema_eq
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='FaceOps · Vincular máquina a la bóveda'", g.cliente["id"])
    if not e:
        return {"error": "Equipo no encontrado"}, 404
    if not a:
        return {"error": "Falta «FaceOps · Vincular máquina a la bóveda» en el catálogo (sincroniza con AAP)"}, 400
    tipo = "openshift" if (e.get("categoria") or "") == "clúster openshift" else \
        ("windows" if sistema_eq(e) == "windows" else ("red" if (e.get("categoria") or "servidor") in CATEGORIAS_RED else "linux"))
    try:
        # aplica a toda máquina del inventario (servidor, red, Windows, con o sin recolección): no pasa por las reglas
        # de compatibilidad del catálogo, solo por el permiso de ambiente del perfil
        from .permisos import ambiente_permitido
        if not ambiente_permitido(g.perfil, e.get("ambiente")):
            raise PermissionError("Tu perfil no opera el ambiente de esta máquina")
        if "kvm" in (e.get("skills") or []):
            raise ValueError("Esta máquina no se vincula a la bóveda (anfitrión de la plataforma)")
        ya = q1("SELECT numero FROM tickets WHERE cliente_id=? AND automatizacion_id=? AND motivo=? AND estado IN ('pendiente_aprobacion','en_cola','en_ejecucion')",
                g.cliente["id"], a["id"], f"Vincular {e['nombre']} a la bóveda")
        if ya:
            raise ValueError(f"Ya hay una vinculación en curso para {e['nombre']} ({ya['numero']}): apruébala o cancélala desde ese ticket")
        if e.get("energia") == "apagado":
            raise ValueError(f"{e['nombre']} está apagada: enciéndela (bolita de Estado › Encender) y luego vincúlala")
        params, secretos = {"vm_tipo": tipo, "vm_usuario": "faceops"}, None
        if tipo == "red":   # los equipos de red no crean usuario: se guarda en la bóveda el usuario y la clave que se entregan
            usu, cla = (request.form.get("vm_usuario") or "").strip(), request.form.get("vm_clave") or ""
            if not usu or not cla:
                raise ValueError("Para un equipo de red indica el usuario y la clave de administración que se guardarán en la bóveda")
            params, secretos = {"vm_tipo": tipo, "vm_usuario": usu, "vm_clave": "••••••"}, {"vm_clave": cla}
        if tipo == "openshift":   # un token de administrador, una sola vez: crea la cuenta de servicio faceops y en la bóveda queda su token
            tok = (request.form.get("vm_clave") or "").strip()
            if not tok:
                raise ValueError("Para vincular el clúster indica un token de administrador (oc whoami -t): se usa una vez para crear la cuenta de servicio de FaceOps")
            inf_ = q1("SELECT detalle FROM infraestructuras WHERE cliente_id=? AND tipo='openshift' AND (ubicacion=? OR ?='')", g.cliente["id"], e.get("ubicacion") or "", e.get("ubicacion") or "")
            api_ = (j(inf_["detalle"], {}) if inf_ else {}).get("api", "")
            params, secretos = {"vm_tipo": tipo, "vm_usuario": "faceops", "vm_clave": "••••••", **({"vm_api": api_} if api_ else {})}, {"vm_clave": tok}
        t = solicitar(g.usuario, g.cliente, a, None, params, f"Vincular {e['nombre']} a la bóveda",
                      secretos, {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=e)
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "url": url_for("consola.ticket", tid=t["id"])}


@bp.route("/inventario/<int:eid>/inventariar", methods=["POST"])
@requiere_login
def equipo_inventariar(eid):
    """Inventario completo de la máquina (sistema, servicios, paquetes): ticket en AAP; FaceOps contrasta el resultado."""
    from .operacion import solicitar
    from .inventariado import AUTOMATIZACION
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], AUTOMATIZACION)
    if not e:
        abort(404)
    nodo_ = j(e["datos"], {}).get("nodo_ocp") or {}
    if (e.get("categoria") or "") == "clúster openshift" or nodo_:   # un clúster (o uno de sus nodos) se inventaría por la API de OpenShift
        if nodo_:
            e = next((x for x in equipos_visibles() if x["id"] == nodo_.get("cluster_id")), e)
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='OpenShift · Recolectar clúster'", g.cliente["id"])
        if not a:
            return {"error": "Falta «OpenShift · Recolectar clúster» en el catálogo (sincroniza con AAP)"}, 400
    if not a:
        flash(f"Falta «{AUTOMATIZACION}» en el catálogo (sincroniza con AAP)", "error")
        return redirect(url_for("consola.equipo", eid=eid) + "#inventario-maq")
    fetch_ = request.headers.get("X-Requested-With") == "fetch"
    try:
        t = solicitar(g.usuario, g.cliente, a, None, {"pq_respaldar": "si"}, f"Inventariar {e['nombre']}", None,
                      {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=e)
        if fetch_:
            return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]),
                    "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}
        return redirect(url_for("consola.ticket", tid=t["id"]))
    except (PermissionError, ErrorAAP, ValueError) as x:
        if fetch_:
            return {"error": str(x)}, 400
        flash(str(x), "error")
    return redirect(url_for("consola.equipo", eid=eid) + "#inventario-maq")


@bp.route("/inventario/<int:eid>/paquetes", methods=["POST"])
@requiere_login
def equipo_paquetes(eid):
    """Inventario de paquetes (con su fuente) y respaldo de los RPM en el registro propio: ticket en AAP, con aprobación."""
    from .operacion import solicitar
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Sistema · Paquetes · inventario y respaldo'", g.cliente["id"])
    if not e:
        abort(404)
    if not a:
        flash("Falta «Sistema · Paquetes · inventario y respaldo» en el catálogo (sincroniza con AAP)", "error")
        return redirect(url_for("consola.equipo", eid=eid) + "#paquetes")
    try:
        t = solicitar(g.usuario, g.cliente, a, None, {"pq_respaldar": "si"}, f"Inventariar y respaldar paquetes de {e['nombre']}", None,
                      {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=e)
        return redirect(url_for("consola.ticket", tid=t["id"]))
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.equipo", eid=eid) + "#paquetes")


@bp.route("/inventario/<int:eid>/plataforma/<cod>/servicio", methods=["POST"])
@requiere_login
def plataforma_servicio(eid, cod):
    """Encender, apagar o reiniciar la plataforma (su servicio) sin tocar la máquina: ticket en AAP, con aprobación."""
    from .operacion import solicitar_en_equipo
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    accion = request.form.get("accion", "")
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Plataforma · Controlar servicio'", g.cliente["id"])
    if not e or familia(cod) not in SERVICIOS_PLATAFORMA or accion not in ("encender", "apagar", "reiniciar"):
        abort(404)
    if not a:
        flash("Falta «Plataforma · Controlar servicio» en el catálogo (sincroniza con AAP)", "error")
        return redirect(url_for("consola.equipo", eid=eid) + f"#p-{cod}")
    nom = request.form.get("nombre") or cod
    try:
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, {"sp_unidades": SERVICIOS_PLATAFORMA[familia(cod)], "sp_accion": accion, "sp_plataforma": nom},
                                f"{accion.capitalize()} {nom} en {e['nombre']}")
        flash(f"{accion.capitalize()} {nom}: {t['numero']} creado (espera su aprobación)", "ok")
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.equipo", eid=eid) + f"#p-{cod}")


PAQUETES_PLATAFORMA = {"jboss": ["eap8*", "eap7*", "jboss-eap*"], "nginx": ["nginx*"], "postgresql": ["postgresql-server*", "postgresql*-server", "postgresql-contrib*"],
                       "redis": ["redis*", "valkey*"], "httpd": ["httpd"], "tomcat": ["tomcat*", "jws6-tomcat*", "jws5-tomcat*"],
                       "keycloak": ["rhbk*", "keycloak*"], "kafka": ["kafka*", "amq-streams*"], "activemq": ["amq-broker*", "artemis*"],
                       "mysql": ["mysql-server*", "mariadb-server*"],
                       "389-directory-server": ["389-ds-base*"], "red-hat-idm": ["ipa-server*", "freeipa-server*"], "vault": ["vault"],
                       "certificate-system": ["pki-ca", "pki-kra", "redhat-pki*"], "gitea": ["gitea"], "registro-paquetes": ["gitea"],
                       "automation-hub": ["automation-hub*"], "aap": ["automation-controller*"], "frrouting": ["frr"], "vyos-firewall": ["vyos*"],
                       "rabbitmq": ["rabbitmq-server*"]}
# lo que no se quita con paquetes: contenedores (podman) y roles de Windows; IdM antes desinstala el servidor
CONTENEDORES_PLATAFORMA = {"jenkins": ["jenkins"], "keycloak": ["keycloak"], "midpoint": ["midpoint"], "redisinsight": ["redisinsight"], "vault": ["vault"]}
WINDOWS_PLATAFORMA = {"active-directory": ["AD-Domain-Services"], "iis": ["Web-Server"]}
PREVIO_PLATAFORMA = {"red-hat-idm": "ipa-server-install --uninstall -U"}


def desinstalable(cod):
    f = familia(cod)
    return f in PAQUETES_PLATAFORMA or f in CONTENEDORES_PLATAFORMA or f in WINDOWS_PLATAFORMA


@bp.route("/inventario/<int:eid>/operador/<cod>", methods=["POST"])
@requiere_login
def operador_accion(eid, cod):
    """Reiniciar o desinstalar un operador de OpenShift: ticket en AAP con aprobación."""
    from .operacion import solicitar
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    op = next((o for o in ((j(e["datos"], {}).get("ocp") or {}).get("operadores") or []) if "op-" + o["n"] == cod), None) if e else None
    accion = request.form.get("accion", "")
    if not op or accion not in ("reiniciar", "desinstalar"):
        return {"error": "Operador o acción no válidos"}, 400
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='OpenShift · Operador'", g.cliente["id"])
    if not a:
        return {"error": "Falta «OpenShift · Operador» en el catálogo (sincroniza con AAP)"}, 400
    try:
        t = solicitar(g.usuario, g.cliente, a, None, {"op_ns": op["ns"], "op_sub": op.get("sub") or op["n"], "op_csv": op.get("csv") or "", "op_accion": accion},
                      f"{accion.capitalize()} el operador {op.get('nombre') or op['n']} en {e['nombre']}", None,
                      {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=e)
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}


@bp.route("/inventario/<int:eid>/plataforma/<cod>/desinstalar", methods=["POST"])
@requiere_login
def plataforma_desinstalar(eid, cod):
    """Desinstalar una plataforma de la máquina: ticket en AAP con aprobación; al terminar sale de la ficha."""
    from .operacion import solicitar_en_equipo
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    fam = familia(cod)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Plataforma · Desinstalar'", g.cliente["id"])
    if not e or not desinstalable(cod):
        abort(404)
    if not a:
        return {"error": "Falta «Plataforma · Desinstalar» en el catálogo (sincroniza con AAP)"}, 400
    nom = request.form.get("nombre") or cod
    try:
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, {"dp_plataforma": nom, "dp_familia": fam, "dp_unidades": SERVICIOS_PLATAFORMA.get(fam, []),
                                                             "dp_paquetes": PAQUETES_PLATAFORMA.get(fam, []),
                                                             "dp_contenedores": CONTENEDORES_PLATAFORMA.get(fam, []), "dp_windows": WINDOWS_PLATAFORMA.get(fam, []),
                                                             "dp_previo": PREVIO_PLATAFORMA.get(fam, "")}, f"Desinstalar {nom} de {e['nombre']}")
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "url": url_for("consola.ticket", tid=t["id"])}


@bp.route("/inventario/<int:eid>/redis", methods=["GET", "POST"])
@requiere_login
def redis_equipo(eid):
    """Consola de Redis: FaceOps entra con la credencial de la bóveda (el usuario nunca la ve)."""
    from . import redis_consola as rc
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    try:
        r = rc.conectar(e)
        if request.method == "POST":
            if request.form.get("_csrf") != session.get("csrf"):
                abort(400)
            accion = request.form.get("accion")
            if accion == "valor":
                return rc.valor(r, request.form.get("clave", ""))
            if accion == "comando":
                try:
                    res, escribe = rc.comando(r, request.form.get("texto", ""), g.perfil["modo"] == "escritura")
                except (ValueError, PermissionError) as x:
                    return {"error": str(x)}, 400
                auditar(g.usuario["usuario"], g.cliente["id"], "comando Redis" + (" (escritura)" if escribe else ""), e["nombre"],
                        {"comando": request.form.get("texto", "")[:200]})
                return {"resultado": res}
            abort(400)
        patron = request.args.get("patron", "*")
        cursor, ks = rc.claves(r, patron, int(request.args.get("cursor", 0) or 0))
        if not request.args:
            auditar(g.usuario["usuario"], g.cliente["id"], "abrir consola Redis", e["nombre"], {})
        return render_template("redis.html", e=e, info=rc.resumen(r), claves=ks, cursor=cursor, patron=patron, error=None)
    except (LookupError, Exception) as x:  # noqa: BLE001 - conexión, autenticación o bóveda
        if request.method == "POST":
            return {"error": str(x)[:300]}, 502
        return render_template("redis.html", e=e, info=None, claves=[], cursor=0, patron="*", error=str(x)[:300])


# ------------------------------------------------------------------ respaldo continuo: historial de versiones y recuperación
VOLVER, RECUPERAR = "FaceOps · Volver a una versión", "FaceOps · Recuperar servidor"


def _repo_de(e):
    """(repositorio, rama) del historial de la máquina: su repositorio propio (rama master, lo llena el inventario diario);
    si aún no lo tiene, la rama del agente de respaldo continuo."""
    d_ = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    gm = d_.get("git") or {}
    return (gm["repo"], gm.get("rama") or "master") if gm.get("repo") else ("repositorio/respaldo-servidores", e["nombre"])


def _gitea_respaldo(ruta, repo="repositorio/respaldo-servidores", **params):
    import os
    import requests
    base = f"http://192.168.122.1:3000/api/v1/repos/{repo}"
    tok = os.environ.get("FACEOPS_RESPALDO_TOKEN", "")
    try:
        r = requests.get(base + ruta, params=params, timeout=10, headers={"Authorization": f"token {tok}"} if tok else {})
    except requests.RequestException:   # registro de respaldos no disponible (p. ej. instancia nueva sin Gitea)
        return None
    return r.json() if r.ok else None


def _gitea_respaldo_archivo(rama, ruta):
    import requests
    tok = os.environ.get("FACEOPS_RESPALDO_TOKEN", "")
    r = requests.get(f"http://192.168.122.1:3000/api/v1/repos/repositorio/respaldo-servidores/raw/{ruta}", params={"ref": rama}, timeout=10,
                     headers={"Authorization": f"token {tok}"} if tok else {})
    return r.text if r.ok else None


RUNTIMES = [("jboss", "JBoss EAP", "jboss"), ("tomcat", "Apache Tomcat", "tomcat"), ("iis", "IIS", "iis"), ("nginx", "NGINX", "nginx"),
            ("openshift", "OpenShift", None)]


def _apps_servidor(e):
    import yaml as _y
    txt = _gitea_respaldo_archivo(e["nombre"], "meta/aplicaciones.txt") or ""
    out = []
    for bloque in txt.split("--- ")[1:]:
        nombre, _, cuerpo = bloque.partition("\n")
        try:
            d = _y.safe_load("\n".join(l for l in cuerpo.splitlines() if not l.startswith("archivo:"))) or {}
        except _y.YAMLError:
            d = {}
        repo = (d.get("repo") or "").rstrip("/").removesuffix(".git")
        out.append({"app": d.get("app") or nombre.strip(), "version": d.get("version") or "", "repo": repo,
                    "commit": d.get("commit") or "", "donde": e["nombre"], "eid": e["id"], "url": url_for("consola.equipo", eid=e["id"])})
    return out


@bp.route("/runtimes")
@requiere_login
def runtimes():
    """Aplicaciones por runtime: servidores de aplicación (JBoss, Tomcat, IIS, NGINX) y OpenShift, con el repositorio de cada una."""
    sel = request.args.get("r", "")
    eqs = equipos_visibles()
    cajas, apps = [], []
    for k, n, skill in RUNTIMES:
        if k == "openshift":
            hosts = [e for e in eqs if (e.get("categoria") or "") == "clúster openshift"]
        else:
            hosts = [e for e in eqs if skill in (e.get("skills") or [])]
        if sel != k:
            cajas.append({"k": k, "t": n, "hosts": len(hosts), "on": sum(1 for e in hosts if e.get("energia") == "encendido")})
            continue
        cajas.append({"k": k, "t": n, "hosts": len(hosts), "on": sum(1 for e in hosts if e.get("energia") == "encendido")})
        for e in hosts:
            if k == "openshift":
                ocp = (j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})).get("ocp") or {}
                for ns in ocp.get("namespaces_app") or []:
                    for c in ns.get("l_cargas") or []:
                        img = (c.get("imagenes") or [""])[0]
                        apps.append({"app": c.get("n"), "version": img.rsplit(":", 1)[-1] if ":" in img else "", "repo": "", "imagen": img,
                                     "commit": "", "donde": f"{e['nombre']} · {ns.get('ns')}", "eid": e["id"],
                                     "url": url_for("consola.namespace_ocp", eid=e["id"], ns=ns.get("ns"))})
            else:
                a_ = _apps_servidor(e)
                apps += a_ or [{"app": "—", "version": "", "repo": "", "commit": "", "donde": e["nombre"], "eid": e["id"],
                                "url": url_for("consola.equipo", eid=e["id"]), "vacio": True}]
    q_ = (request.args.get("q") or "").strip().lower()
    if q_:
        apps = [a for a in apps if q_ in (a["app"] + " " + a["donde"] + " " + a.get("repo", "")).lower()]
    return render_template("runtimes.html", cajas=cajas, sel=sel, sel_t=dict((k, n) for k, n, _ in RUNTIMES).get(sel, ""), apps=apps, q=q_)


@bp.route("/respaldos")
@requiere_login
def respaldos():
    """Respaldos y recuperación: qué máquinas tienen respaldo continuo en Git, cuándo fue su último respaldo y acceso a sus versiones."""
    from datetime import datetime as _d, timezone as _tz
    ramas = {b["name"]: b for b in (_gitea_respaldo("/branches", limit=200) or [])}
    filas = []
    for e in equipos_visibles():
        if seccion_eq(e) not in ("inventario", "hipervisores", "estaciones", "nubes"):
            continue
        b = ramas.get(e["nombre"])
        cuando = ((b or {}).get("commit") or {}).get("timestamp", "")
        gm_ = (j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})).get("git") or {}
        if gm_.get("fecha") and (not cuando or gm_["fecha"] > cuando):   # instantánea del inventario diario en el repositorio de la máquina
            cuando, b = gm_["fecha"], b or {"commit": {"message": f"inventario diario · {gm_.get('repo')}"}}
        horas = None
        if cuando:
            try:
                horas = (_d.now(_tz.utc) - _d.fromisoformat(cuando.replace("Z", "+00:00"))).total_seconds() / 3600
            except ValueError:
                pass
        estado = "sin respaldo" if not b else ("al día" if horas is not None and horas <= 26 else "atrasado")
        filas.append({"e": e, "estado": estado, "cuando": cuando, "horas": horas,
                      "url": url_for("consola.versiones_equipo", eid=e["id"]), "msg": ((b or {}).get("commit") or {}).get("message", "")[:80]})
    filas.sort(key=lambda x: ({"atrasado": 0, "sin respaldo": 1, "al día": 2}[x["estado"]], x["e"]["nombre"]))
    cuenta = Counter(x["estado"] for x in filas)
    # evaluación global por tipo de respaldo: el punto de restauración es la combinación de todos
    from .puntos import iniciar as _pr_ini
    _pr_ini()
    ultimos = {}
    for r_ in q("SELECT equipo_id, codigo, creado, ficha FROM puntos_restauracion WHERE cliente_id=? ORDER BY id DESC", g.cliente["id"]):
        ultimos.setdefault(r_["equipo_id"], r_)
    TIPOS_R = [("configuracion", "Configuración", "Git · respaldo continuo"), ("datos", "Datos", "restic"),
               ("paquetes", "Paquetes", "Registro propio (RPM)"), ("imagen", "Imagen del sistema", "Registro propio (imágenes)"),
               ("vm", "Máquina virtual", "Snapshots del hipervisor"), ("punto", "Punto de restauración", "FaceOps · combinación de todo lo anterior")]
    for x in filas:
        pr_ = ultimos.get(x["e"]["id"])
        f_ = j(pr_["ficha"], {}) if pr_ else {}
        pq_ = f_.get("paquetes") or {}
        x["tipos"] = {
            "configuracion": "ok" if x["estado"] == "al día" else ("parcial" if x["estado"] == "atrasado" else "no"),
            "datos": "ok" if (f_.get("datos") or {}).get("snapshot") else "no",
            "paquetes": ("ok" if pq_.get("total") and pq_.get("respaldados", 0) >= 0.9 * pq_["total"] else ("parcial" if pq_.get("respaldados") else "no")) if pq_ else "no",
            "imagen": "ok" if ((f_.get("sistema") or {}).get("origen") or {}).get("registro") else "no",
            "vm": "no aplica" if (x["e"].get("forma") or "") != "virtual" else "no",
            "punto": "ok" if pr_ else "no"}
        x["punto"] = pr_
    evaluacion = []
    for k, n, herr in TIPOS_R:
        aplica = [x for x in filas if x["tipos"][k] != "no aplica"]
        ok_ = sum(1 for x in aplica if x["tipos"][k] == "ok")
        evaluacion.append({"k": k, "n": n, "herramienta": herr, "aplica": len(aplica), "ok": ok_,
                           "parcial": sum(1 for x in aplica if x["tipos"][k] == "parcial"),
                           "pct": round(100 * ok_ / len(aplica)) if aplica else None})
    celdas = [v for x in filas for v in x["tipos"].values() if v != "no aplica"]
    global_ = round(100 * sum(1 if v == "ok" else 0.5 if v == "parcial" else 0 for v in celdas) / len(celdas)) if celdas else None
    herramientas = [{"n": "Git · repositorio de cada máquina (inventario diario) y respaldo continuo (agente)", "tipos": "Configuración, paquetes instalados, servicios y aplicaciones como código", "cubre": sum(1 for x in filas if x["estado"] != "sin respaldo"),
                     "url": git_publico("http://x/git/repositorio/respaldo-servidores")},
                    {"n": "restic", "tipos": "Datos (archivos y bases de datos), incremental y cifrado", "cubre": sum(1 for x in filas if x["tipos"]["datos"] == "ok"), "url": ""},
                    {"n": "Registro propio", "tipos": "Imágenes del sistema, paquetes RPM e instaladores de plataformas", "cubre": sum(1 for x in filas if x["tipos"]["paquetes"] != "no" or x["tipos"]["imagen"] == "ok"),
                     "url": git_publico("http://x/git/repositorio/-/packages")},
                    {"n": "Snapshots del hipervisor (KVM)", "tipos": "Máquina virtual completa (hoy solo antes de cambios: migraciones y actualizaciones)", "cubre": 0, "url": ""},
                    {"n": "Puntos de restauración", "tipos": "La llave que combina todo: imagen + paquetes + plataformas + configuración + aplicaciones + datos", "cubre": sum(1 for x in filas if x["punto"]), "url": ""}]
    return render_template("respaldos.html", filas=filas, cuenta=cuenta, evaluacion=evaluacion, global_=global_, herramientas=herramientas,
                           tipos_r=TIPOS_R, sel_eq=request.args.get("equipo", ""))


@bp.route("/inventario/<int:eid>/aplicaciones")
@requiere_login
def aplicaciones_equipo(eid):
    """Aplicaciones desplegadas en el equipo (registro que guarda el respaldo continuo en Git) y las versiones anteriores."""
    import yaml as _y
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    txt = _gitea_respaldo_archivo(e["nombre"], "meta/aplicaciones.txt") or ""
    apps = []
    for bloque in txt.split("--- ")[1:]:
        nombre, _, cuerpo = bloque.partition("\n")
        cuerpo = "\n".join(l for l in cuerpo.splitlines() if not l.startswith("archivo:"))
        try:
            d = _y.safe_load(cuerpo) or {}
        except _y.YAMLError:
            d = {}
        d["app"] = d.get("app") or nombre.strip()
        apps.append(d)
    hist = {}   # versiones anteriores de cada aplicación: de los commits del respaldo que la cambiaron
    for c in _gitea_respaldo("/commits", sha=e["nombre"], limit=50, stat="false", files="false") or []:
        msg = c["commit"]["message"]
        if "Aplicaciones:" not in msg:
            continue
        bloque = msg.split("Aplicaciones:", 1)[1]
        v = re.search(r"version:\s*\"?([\w.\-]+)", bloque)
        cm = re.search(r"commit:\s*\"?([0-9a-f]{6,40})", bloque)
        ap = re.search(r"app:\s*([\w.\-]+)", bloque)
        if v:
            k = ap.group(1) if ap else "app-jboss"
            hist.setdefault(k, []).append({"version": v.group(1), "commit": cm.group(1) if cm else "", "fecha": c["commit"]["author"]["date"],
                                            "respaldo": c["sha"][:7], "respaldo_url": c["html_url"]})
    for a in apps:
        a["historial"] = [h for h in hist.get(a["app"], []) if h["version"] != str(a.get("version"))][:10]
        if a.get("repo") and a.get("commit"):
            a["commit_url"] = f"{a['repo'].rstrip('/').removesuffix('.git')}/commit/{a['commit']}"
    return {"aplicaciones": apps, "puede": g.perfil["modo"] == "escritura", "desplegar": url_for("consola.desplegar_version_app", eid=eid)}


@bp.route("/inventario/<int:eid>/aplicaciones/desplegar", methods=["POST"])
@requiere_login
def desplegar_version_app(eid):
    """Vuelve a desplegar una versión anterior de una aplicación (ticket; aprobación en producción y QA)."""
    from .operacion import solicitar_en_equipo
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='JBoss · Desplegar app'", g.cliente["id"])
    ver = re.sub(r"[^\w.\-]", "", request.form.get("version", ""))[:40]
    com = re.sub(r"[^0-9a-f]", "", request.form.get("commit", ""))[:40]
    if not e or not a or not ver:
        return {"error": "Falta la versión o la acción «JBoss · Desplegar app»"}, 400
    try:
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, {"app_version": ver, "app_commit": com},
                                f"Volver a la versión {ver} de la aplicación en {e['nombre']}")
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}


def _cambios_git(e, hasta, desde=None, repo=None):
    """Todos los commits de la rama de la máquina hasta el commit del punto, con su motivo y qué tipo de cambio traen.
    Los posteriores al punto anterior quedan marcados como nuevos de este punto."""
    if not hasta:
        return []
    cs = _gitea_respaldo("/commits", repo=repo or "repositorio/respaldo-servidores", sha=hasta, limit=100, stat="false", files="false") or []
    out, nuevo = [], bool(desde)
    for c in cs if isinstance(cs, list) else []:
        if desde and c["sha"] == desde:
            nuevo = False
        lineas = c["commit"]["message"].strip().splitlines()
        tipos = [t for t, k in (("paquetes", "Paquetes:"), ("plataformas", "Plataformas:"), ("aplicaciones", "Aplicaciones:"), ("comandos", "Comandos:"))
                 if any(x.strip() == k for x in lineas)]
        tk = re.search(r"\b(T-?\d{3,}|TK-?\d+|#\d{3,})\b", c["commit"]["message"])
        out.append({"sha": c["sha"], "corto": c["sha"][:7], "fecha": c["commit"]["author"]["date"], "autor": c["commit"]["author"]["name"],
                    "motivo": lineas[0] if lineas else "", "tipos": tipos or ["archivos"], "ticket": tk.group(1) if tk else "",
                    "comandos": [re.sub(r"^\$\s*\S+\s+", "", x.strip()) for x in lineas if x.strip().startswith("$")][:8],
                    "archivos_c": [x.strip().lstrip("·").strip() for x in lineas if x.strip().startswith("·")][:8],
                    "url": git_publico(c["html_url"]), "nuevo": nuevo and bool(desde), "punto": c["sha"] == hasta})
    return out


# Agentes de FaceOps que se instalan en las máquinas (no son plataformas del negocio: los usa FaceOps para operarlas)
AGENTES = [
    {"skill": "respaldo-git", "nombre": "Respaldo continuo en Git", "auto": "FaceOps · Respaldo continuo en Git", "sistemas": ["linux"],
     "unidades": ["faceops-respaldo.timer", "faceops-respaldo-paquetes.timer"],
     "opcional": True,
     "que": "Mejora para máquinas críticas: además del inventario diario (repositorio de la máquina, rama master), guarda cada minuto la configuración y registra quién ejecutó qué comando.",
     "falta": "Sin él, la recuperación usa el último inventario diario: se pueden perder los cambios de hasta un día."},
    {"skill": "node-exporter", "nombre": "Monitoreo (métricas)", "auto": "FaceOps · Agente de monitoreo (Linux)", "sistemas": ["linux"], "unidades": ["node_exporter"],
     "que": "Publica CPU, memoria, discos y red para Grafana y las alertas de capacidad.",
     "falta": "Sin él, el uso de la máquina se estima o se emula y las alertas de capacidad no llegan."},
    {"skill": "windows-exporter", "nombre": "Monitoreo (métricas)", "auto": "Monitoreo · Exportador en Windows", "sistemas": ["windows"], "unidades": ["windows_exporter"],
     "que": "Publica CPU, memoria, discos y red para Grafana y las alertas de capacidad.",
     "falta": "Sin él, el uso de la máquina se estima o se emula y las alertas de capacidad no llegan."},
]
AGENTES_SK = {a["skill"] for a in AGENTES} | {"node_exporter", "monitoreo"}


def _agentes_de(e, d_=None):
    """Los agentes que corresponden a la máquina según su sistema, con su estado y la acción que los instala."""
    so = (e.get("so") or "").lower()
    if (e.get("categoria") or "servidor") not in ("servidor", "estación de trabajo"):
        return []
    fam = "windows" if "windows" in so else "linux"
    sk = set(e.get("skills") or [])
    m_ = re.search(r"(\d+)", so)
    mayor = int(m_.group(1)) if m_ else 0
    pend = {t["habilita_skill"]: t for t in q(
        "SELECT t.id, t.numero, t.estado, a.habilita_skill FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
        "WHERE t.cliente_id=? AND t.limite=? AND t.estado IN ('pendiente_aprobacion','en_cola','en_ejecucion','en curso','ejecutando')",
        g.cliente["id"], e["nombre"])}
    out = []
    for ag in AGENTES:
        if fam not in ag["sistemas"]:
            continue
        a_ = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], ag["auto"])
        inst = ag["skill"] in sk or (ag["skill"] == "node-exporter" and sk & {"node_exporter", "monitoreo"})
        limite = ""
        if ag["skill"] == "respaldo-git" and not inst:
            if "ubuntu" in so or "debian" in so:
                limite = "Aún no compatible con Ubuntu (el agente instala git con dnf): en corrección."
            elif any(x in so for x in ("redhat", "red hat", "rhel", "centos", "rocky", "alma")) and 0 < mayor < 8:
                limite = f"Aún no compatible con versión {mayor} (usa yum, no dnf): en corrección."
        out.append(dict(ag, instalado=bool(inst), aid=a_["id"] if a_ else None, probada=bool(a_) and _certificada(a_),
                        pendiente=pend.get(ag["skill"]), limite=limite, salud=_salud_agente(e, ag) if inst else None))
    return out


def _salud_agente(e, ag):
    """Cómo está un agente instalado: encendido o no según el último inventario y, el de respaldo, según su último commit."""
    d_ = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    det = {x.get("n", "").removesuffix(".service"): x.get("estado") for x in d_.get("servicios_det") or []}
    if ag["skill"] == "respaldo-git":
        try:
            cs = _gitea_respaldo("/commits", sha=e["nombre"], limit=1, stat="false", files="false") or []
        except Exception:  # noqa: BLE001 - sin el repositorio no se puede medir
            return {"ok": None, "estado": "sin dato", "detalle": "No se pudo consultar el repositorio"}
        if not cs:
            return {"ok": False, "estado": "sin respaldos", "detalle": "No hay commits del agente en su rama"}
        from datetime import datetime as _d, timezone as _tz
        try:
            mins = (_d.now(_tz.utc) - _d.fromisoformat(cs[0]["commit"]["committer"]["date"].replace("Z", "+00:00"))).total_seconds() / 60
        except (KeyError, ValueError):
            mins = None
        txt = "" if mins is None else (f"último respaldo hace {round(mins)} min" if mins < 120 else f"último cambio respaldado hace {round(mins / 60)} h")
        return {"ok": True, "estado": "encendido", "detalle": txt}
    u = (ag.get("unidades") or [""])[0]
    est = det.get(u)
    if est is None:
        return {"ok": None, "estado": "sin dato", "detalle": "El último inventario no informó su servicio"}
    return {"ok": est == "arriba", "estado": "encendido" if est == "arriba" else ("apagado" if est in ("detenido", "inactivo") else est), "detalle": f"servicio {u}"}


@bp.route("/inventario/<int:eid>/versiones")
@requiere_login
def versiones_equipo(eid):
    """Historial de versiones del equipo (cada commit de su respaldo continuo: qué cambió y qué comandos se ejecutaron)."""
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    repo_, rama_ = _repo_de(e)
    cs = _gitea_respaldo("/commits", repo=repo_, sha=rama_, limit=int(request.args.get("n", 25)), stat="false", files="false")
    if cs is None:
        return {"versiones": [], "error": "Esta máquina aún no tiene historial en Git: se crea con su próximo inventario (repositorio propio, rama master)."}
    out = []
    for i, c in enumerate(cs if isinstance(cs, list) else []):
        lineas = c["commit"]["message"].strip().splitlines()
        sec, partes = "archivos", {"archivos": [], "paquetes": [], "plataformas": [], "aplicaciones": [], "comandos": []}
        for ln in lineas[1:]:
            t_ = ln.strip()
            if t_ in ("Paquetes:", "Plataformas:", "Aplicaciones:", "Comandos:"):
                sec = t_[:-1].lower()
                continue
            if t_:
                partes[sec].append(t_.lstrip("·$ ").strip())
        out.append({"sha": c["sha"], "corto": c["sha"][:7], "fecha": c["commit"]["author"]["date"], "autor": c["commit"]["author"]["name"],
                    "titulo": lineas[0] if lineas else "", "detalle": "\n".join(lineas[1:]).strip(),
                    "url": git_publico(c["html_url"]), "actual": i == 0,
                    **partes})
    return {"versiones": out, "rama": rama_, "repo": repo_, "historial": out[0]["url"].split("/commit/")[0] + f"/commits/branch/{rama_}" if out else "",
            "puede": g.perfil["modo"] == "escritura", "volver": url_for("consola.volver_equipo", eid=eid),
            "recuperar": url_for("consola.recuperar_equipo", eid=eid)}


@bp.route("/inventario/<int:eid>/versiones/<sha>")
@requiere_login
def version_equipo(eid, sha):
    """Un cambio del respaldo continuo de la máquina (el repositorio es privado: FaceOps lo muestra sin entrar a Gitea)."""
    import requests
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        abort(404)
    repo_ = _repo_de(e)[0]
    c = _gitea_respaldo(f"/git/commits/{sha}", repo=repo_, stat="true", files="true")
    if not c and repo_ != "repositorio/respaldo-servidores":   # commit del agente de respaldo continuo
        repo_ = "repositorio/respaldo-servidores"
        c = _gitea_respaldo(f"/git/commits/{sha}", repo=repo_, stat="true", files="true")
    if not c:
        abort(404)
    tok = os.environ.get("FACEOPS_RESPALDO_TOKEN", "")
    r = requests.get(f"http://192.168.122.1:3000/api/v1/repos/{repo_}/git/commits/{sha}.diff", timeout=15,
                     headers={"Authorization": f"token {tok}"} if tok else {})
    diff = r.text[:400_000] if r.ok else ""
    return render_template("version_equipo.html", e=e, c=c, diff=diff, sha=sha)


def _ticket_respaldo(nombre_aap, e, params, motivo):
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(400)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], nombre_aap)
    if not a:
        return {"error": f"Falta «{nombre_aap}» en el catálogo (sincroniza con AAP)"}, 400
    try:
        t = solicitar(g.usuario, g.cliente, a, (e["segmentos"] or [""])[0], params, motivo)
    except (PermissionError, ErrorAAP) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}


@bp.route("/inventario/<int:eid>/volver", methods=["POST"])
@requiere_login
def volver_equipo(eid):
    """Volver a una versión del historial: ticket con aprobación en AAP; restaura sobre la misma máquina."""
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    sha = request.form.get("sha", "")
    if not e or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return {"error": "Versión no válida"}, 400
    repo_ = _repo_de(e)[0]
    if repo_ != "repositorio/respaldo-servidores" and not _gitea_respaldo(f"/git/commits/{sha}", repo=repo_, stat="false", files="false"):
        repo_ = "repositorio/respaldo-servidores"   # versión del agente de respaldo continuo
    return _ticket_respaldo(VOLVER, e, {"rec_equipo": e["nombre"], "rec_version": sha, "rec_ip": e["ip"], "rec_repo": repo_},
                            f"Volver {e['nombre']} a la versión {sha[:7]} de su historial en Git ({repo_})")


def _monitoreo_url(e):
    """Enlace a la máquina en la herramienta de monitoreo del cliente (conf. del cliente «monitoreo_url», con {nombre} e {ip})."""
    conf = j(g.cliente.get("tickets_conf"), {}) if g.get("cliente") else {}
    plantilla = conf.get("monitoreo_url") or os.environ.get("FACEOPS_MONITOREO_URL", "")
    if not plantilla:
        return None
    return {"url": plantilla.replace("{nombre}", quote(e["nombre"], safe="")).replace("{ip}", e.get("ip") or ""),
            "nombre": conf.get("monitoreo_nombre") or os.environ.get("FACEOPS_MONITOREO_NOMBRE", "monitoreo")}


LOGS_PLATAFORMA = {"jboss": r"eap|wildfly|jboss", "httpd": r"/httpd/", "389-directory-server": r"/dirsrv/", "red-hat-idm": r"ipa|krb5|kdc|/pki/|sssd",
                   "nginx": r"/nginx/", "postgresql": r"pgsql|postgres", "redis": r"/redis/", "tomcat": r"tomcat", "keycloak": r"keycloak",
                   "vault": r"/vault/", "midpoint": r"midpoint", "certificate-system": r"/pki/", "gitea": r"gitea"}


def _plat_de_servicio(n):
    for fam, us in SERVICIOS_PLATAFORMA.items():
        for u in us:
            if (u.endswith("@") and n.startswith(u)) or n == u or n.startswith(u + "-"):
                return fam
    return None


def servicios_de(e, c):
    """Servicios de un componente: los de la plataforma, o los del sistema operativo (los que no son de ninguna plataforma)."""
    det = j(e["datos"], {}).get("servicios_det") or []
    fam = None if c.get("so") else familia(c["codigo"])
    xs = [x for x in det if (_plat_de_servicio(x["n"]) == fam) if True] if fam else [x for x in det if not _plat_de_servicio(x["n"])]
    orden = {"error": 0, "detenido": 1, "arriba": 2, "inactivo": 3}
    return sorted(xs, key=lambda x: (orden.get(x["estado"], 9), x["n"]))


SERVICIOS_FIJOS = re.compile(r"^(sshd|dbus(-broker)?|systemd-.*|NetworkManager|polkit|auditd|node_exporter|windows_exporter|getty@.*|serial-getty@.*|user@.*|WinRM|RpcSs|EventLog)(\.service)?$")


def servicio_fijo(n):
    """'' si se puede encender o apagar desde FaceOps; si no, el motivo (lo que sostiene el acceso o el monitoreo)."""
    return "Servicio esencial del sistema: se controla solo desde la máquina (sin él se pierde el acceso o el monitoreo)" if SERVICIOS_FIJOS.match(n or "") else ""


def logs_de(e, c):
    import re as _re
    lg = j(e["datos"], {}).get("logs") or []
    if c.get("so"):
        todos = "|".join(LOGS_PLATAFORMA.values())
        return [x for x in lg if not _re.search(todos, x["ruta"])]
    rx = LOGS_PLATAFORMA.get(familia(c["codigo"]))
    return [x for x in lg if rx and _re.search(rx, x["ruta"])]


def _ns_segmento(ns):
    """Segmento de negocio de FaceOps desplegado en ese namespace de OpenShift (si lo hay)."""
    try:
        return q1("SELECT segmento, ambiente FROM segmentos_ocp WHERE cliente_id=? AND namespace=?", g.cliente["id"], ns)
    except Exception:  # noqa: BLE001
        return None


def _nodo_equipo(n):
    """La ficha del inventario de un nodo de OpenShift (por nombre o por IP), si existe."""
    for x in equipos_visibles():
        if (x.get("categoria") or "") == "clúster openshift":
            continue
        if x["nombre"].split(".")[0] == (n.get("nombre") or "").split(".")[0] or (n.get("ip") and x.get("ip") == n.get("ip")):
            return x
    return None


def _ids_seguridad(autos):
    """Ids de las acciones de seguridad (área «seguridad»), para separarlas de la operación general."""
    from .areas import area_de
    return [a["id"] for a in autos if area_de(a) == "seguridad"]


AGREGAR_DISCO = "FaceOps · Agregar disco"


@bp.route("/inventario/<int:eid>/disco", methods=["POST"])
@requiere_login
def equipo_disco(eid):
    """Agregar un disco a la máquina: del hipervisor (pool de libvirt) o del storage (volumen de la SAN). Ticket con aprobación:
    AAP crea el volumen, lo conecta a la VM y lo prepara dentro del sistema."""
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e or not energia_posible(e):
        return {"error": "Solo se agregan discos a máquinas virtuales del anfitrión KVM"}, 400
    f = request.form
    origen = f.get("origen", "hipervisor")
    nombre = f.get("nombre", "datos").strip().lower()
    gb = f.get("gb", "20")
    if origen not in ("hipervisor", "storage") or not re.fullmatch(r"[a-z0-9-]{2,30}", nombre) or not gb.isdigit() or not 1 <= int(gb) <= 2000:
        return {"error": "Datos del disco no válidos"}, 400
    vm = e["nombre"].split(".")[0]
    params = {"ad_equipo": vm, "ad_gb": gb, "ad_origen": origen, "ad_nombre": nombre, "ad_formatear": bool(f.get("formatear")),
              "ad_serial": f"fo{int(time.time()) % 10**8}"}
    if origen == "storage":
        sts = [x["nombre"] for x in equipos_visibles() if "storage-emulado" in (x.get("skills") or [])]
        if f.get("storage") not in sts:
            return {"error": "Storage no válido"}, 400
        params.update(ad_storage=f["storage"], ad_volumen=f"{vm}-{nombre}", ad_st_pool=f.get("st_pool") if f.get("st_pool") in ("oro", "plata") else "plata")
    else:
        params["ad_pool"] = "images"
    if "windows" in (e.get("so") or "").lower():
        params["ad_letra"] = f.get("letra", "E") if f.get("letra", "E") in "EFGHIJ" else "E"
    else:
        m = f.get("montaje", "/datos")
        if not re.fullmatch(r"/[A-Za-z0-9/_.-]{1,60}", m) or m in ("/", "/boot", "/usr", "/etc", "/var"):
            return {"error": "Punto de montaje no válido"}, 400
        params["ad_montaje"] = m
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], AGREGAR_DISCO)
    if not a:
        return {"error": f"Falta «{AGREGAR_DISCO}» en el catálogo (sincroniza con AAP)"}, 400
    desde = f"del storage {params.get('ad_storage')}" if origen == "storage" else "del hipervisor"
    try:
        t = solicitar(g.usuario, g.cliente, a, (e["segmentos"] or [""])[0], params, f"Agregar disco de {gb} GB {desde} a {e['nombre']}")
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}


@bp.route("/asistente/ejecutar", methods=["POST"])
@requiere_login
def asistente_ejecutar():
    """Formulario de una acción completado en el chat: abre el ticket."""
    from .asistente import ejecutar_formulario
    d = request.get_json(silent=True) or {}
    if d.get("_csrf") != session.get("csrf"):
        return {"error": "Sesión vencida: recarga la página"}, 400
    autos = q("SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=1", g.cliente["id"])
    try:
        aid = int(d.get("aid") or 0)
    except (TypeError, ValueError):
        aid = 0
    msgs = ejecutar_formulario(g.usuario, g.cliente, aid, str(d.get("equipo") or ""), d.get("valores") or {}, equipos_visibles(), autos)
    for x in msgs:
        if x.get("ticket"):
            x["url"] = url_for("consola.ticket", tid=x["ticket"])
    return {"mensajes": msgs}


@bp.route("/asistente", methods=["POST"])
@requiere_login
def asistente():
    """Chat del asistente: interpreta lo pedido, completa los datos y, al confirmar, crea el ticket."""
    from .asistente import responder
    d = request.get_json(silent=True) or {}
    if d.get("_csrf") != session.get("csrf"):
        return {"error": "Sesión vencida: recarga la página"}, 400
    from . import agente
    if d.get("reiniciar"):
        session.pop("asistente", None)
        agente.olvidar(g.usuario, g.cliente)
        return {"mensajes": [], "estado": {}}
    autos = q("SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=1", g.cliente["id"])
    # texto libre escrito por el usuario (no un botón ni un paso pendiente del formulario): lo atiende el agente con Claude
    agente_ = bool(d.get("libre")) and (session.get("asistente") or {}).get("paso") not in ("confirmar", "dato", "elegir") and agente.disponible()
    from .asistente import entender, es_plataformas, plataformas
    from .inventario import normal as normal_
    pl_ = es_plataformas(entender(normal_((d.get("mensaje") or "")[:200]).strip())) \
        if (session.get("asistente") or {}).get("paso") not in ("confirmar", "dato") and not agente_ else None
    if agente_:
        msgs, est = agente.responder(g.usuario, g.cliente, g.perfil, (d.get("mensaje") or "")[:2000], equipos_visibles(), autos, url_for), {}
    elif pl_:
        msgs, est = plataformas(pl_, [(e_, _plataformas_de(e_, r_)) for e_, r_ in _riesgo_parque()]), {}
    else:
        msgs, est = responder(g.usuario, g.cliente, g.perfil, session.get("asistente") or {}, (d.get("mensaje") or "")[:600],
                              equipos_visibles(), autos)
    session["asistente"] = est
    for x in msgs:
        for t_ in x.get("lista") or []:
            t_["url"] = url_for("consola.ticket", tid=t_["id"])
        vis_ = {e["id"]: e for e in equipos_visibles()}
        escritura_ = g.perfil and g.perfil.get("modo") == "escritura"
        for e_ in (x.get("equipos") or []) + ([x["equipo_det"]] if x.get("equipo_det") else []):
            e_["url"] = url_for("consola.equipo", eid=e_["id"])
            eq_ = vis_.get(e_["id"])
            e_["energia_url"] = url_for("consola.equipo_energia", eid=e_["id"]) if (eq_ and escritura_ and energia_posible(eq_)) else ""
            if not e_["energia_url"]:   # por qué no se puede, para mostrarlo en el chat en vez de esconder el botón
                cat_ = ((eq_ or {}).get("categoria") or "servidor")
                e_["energia_motivo"] = ("Necesitas perfil de escritura" if not escritura_ else
                                        "Equipo de red emulado (contenedor): no se enciende ni apaga desde FaceOps" if cat_ in ("router", "switch", "firewall", "balanceador", "almacenamiento") and "vmware" not in ((eq_ or {}).get("ubicacion") or "").lower() else
                                        "Máquina en VMware: se opera desde vCenter" if "vmware" in ((eq_ or {}).get("ubicacion") or "").lower() else
                                        "Equipo retirado del inventario" if (eq_ or {}).get("estado") == "retirado" else
                                        "Este equipo no se enciende ni apaga desde FaceOps")
            e_["energia"] = e_.get("energia") or (eq_ or {}).get("energia") or ""
        for a_ in (x.get("autos") or []) + ([x["auto_det"]] if x.get("auto_det") else []):
            a_["url"] = url_for("consola.automatizacion", aid=a_["id"])
        if x.get("auto_det"):
            x["auto_det"]["equipos"] = sorted(e["nombre"] for e in equipos_visibles())
            x["auto_det"]["infras"] = [r["nombre"] for r in q("SELECT nombre FROM infraestructuras WHERE cliente_id=? ORDER BY id", g.cliente["id"])]
            x["auto_det"]["ejecutar_url"] = url_for("consola.asistente_ejecutar")
        for i_ in (x.get("infras") or []) + ([x["infra_det"]] if x.get("infra_det") else []):
            i_["url"] = url_for("consola.infraestructuras") + f"#infra-{i_['id']}"
        if x.get("seg_det"):
            x["seg_det"]["url"] = url_for("consola.mapa", vista="aplicaciones", segmento=x["seg_det"]["nombre"])
        for s_ in x.get("segs") or []:
            s_["url"] = url_for("consola.mapa", vista="aplicaciones", segmento=s_["nombre"])
        for n_ in (x.get("nss") or []) + ([x["ns_det"]] if x.get("ns_det") else []):
            n_["url"] = url_for("consola.namespace_ocp", eid=n_["eid"], ns=n_["ns"])
        if x.get("cert_det") and x["cert_det"].get("serie"):
            x["cert_det"]["url"] = url_for("consola.certificado", serie=x["cert_det"]["serie"])
        for c_ in x.get("certs") or []:
            c_["url"] = url_for("consola.certificado", serie=c_["serie"]) if c_.get("serie") else ""
        if x.get("ticket"):
            x["url"] = url_for("consola.ticket", tid=x["ticket"])
            t_ = q1("SELECT externo_ref, odoo_ref FROM tickets WHERE id=?", x["ticket"]) or {}
            x["refs"] = [r for r in ((t_.get("externo_ref") or "").split("|")[0], ("Odoo #" + t_["odoo_ref"]) if (t_.get("odoo_ref") or "").isdigit() else "") if r and r not in ("pendiente", "propio")]
            if x.get("detalle"):   # en el detalle: solo el código de FaceOps y, si le toca, aprobar o rechazar aquí mismo
                x["refs"] = []
                tk_ = q1("SELECT * FROM tickets WHERE id=? AND cliente_id=?", x["ticket"], g.cliente["id"])
                if tk_ and tk_["estado"] == "pendiente_aprobacion":
                    from .aprobacion_areas import puede_aprobar as puede_aprobar_etapa
                    from .solicitud_segmento import SEGMENTO_NUEVO
                    a_ = q1("SELECT * FROM automatizaciones WHERE id=?", tk_["automatizacion_id"])
                    if a_ and a_["nombre_aap"] != SEGMENTO_NUEVO and puede_aprobar_etapa(g.usuario, g.cliente["id"], tk_, a_):
                        x["aprobar_url"] = x["url"]
    return {"mensajes": msgs, "estado": {"paso": est.get("paso", "")}}


@bp.route("/movil")
@requiere_login
def movil():
    """FaceOps en el teléfono: solo el chat con el agente, a pantalla completa."""
    from . import agente
    return render_template("movil.html", solo_chat=True, agente_listo=agente.disponible())


@bp.route("/inventario/<int:eid>/disco/ampliar", methods=["POST"])
@requiere_login
def equipo_disco_ampliar(eid):
    """Ampliar un disco de la máquina: ticket con aprobación; AAP agranda el disco virtual en el anfitrión y luego crece
    la partición, el LVM y el sistema de archivos del montaje dentro de la máquina."""
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e or not energia_posible(e):
        return {"error": "Solo se amplían discos de máquinas virtuales del anfitrión KVM"}, 400
    f = request.form
    disco, gb, actual = f.get("disco", "").replace("/dev/", ""), f.get("gb", ""), f.get("actual", "0")
    montaje = f.get("montaje", "").strip()
    if not re.fullmatch(r"(vd|sd|xvd|hd)[a-z]", disco) or not gb.isdigit() or not 2 <= int(gb) <= 4000:
        return {"error": "Disco o tamaño no válidos"}, 400
    try:
        if int(gb) <= float(actual):
            return {"error": f"El tamaño nuevo debe ser mayor que el actual ({actual} GB): los discos no se achican"}, 400
    except ValueError:
        pass
    if montaje and not re.fullmatch(r"/[A-Za-z0-9/_.-]{0,60}", montaje):
        return {"error": "Punto de montaje no válido"}, 400
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "FaceOps · Ampliar disco")
    if not a:
        return {"error": "Falta «FaceOps · Ampliar disco» en el catálogo (sincroniza con AAP)"}, 400
    vm = e["nombre"].split(".")[0]
    params = {"ae_equipo": vm, "ae_disco": disco, "ae_gb": gb, "ae_montaje": montaje}
    try:
        t = solicitar(g.usuario, g.cliente, a, (e["segmentos"] or [""])[0], params,
                      f"Ampliar disco {disco} de {e['nombre']} a {gb} GB" + (f" ({montaje})" if montaje else ""))
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}


@bp.route("/inventario/<int:eid>/puntos", methods=["POST"])
@requiere_login
def punto_crear(eid):
    """Crea un punto de restauración con el estado completo actual de la máquina."""
    from .puntos import crear
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e or request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    p = crear(g.cliente, e, origen="manual", usuario=(g.usuario or {}).get("nombre", ""), nota=request.form.get("nota", "")[:200])
    flash(f"Punto de restauración {p['codigo']} creado.", "ok")
    return redirect(url_for("consola.equipo", eid=eid) + "#puntos")


@bp.route("/inventario/<int:eid>/nodo/<nombre>")
@requiere_login
def nodo_ocp(eid, nombre):
    """Detalle de un nodo de un clúster OpenShift (lo que informa el clúster por su API)."""
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    nds = ((j(e["datos"], {}).get("ocp") or {}).get("nodos") or []) if e else []
    n = next((x for x in nds if x.get("nombre") == nombre), None)
    if not n:
        abort(404)
    return render_template("nodo.html", e=e, n=n, eq=_nodo_equipo(n))


@bp.route("/inventario/<int:eid>/namespace/<ns>")
@requiere_login
def namespace_ocp(eid, ns):
    """Detalle de un namespace de aplicaciones de un clúster OpenShift: cargas, pods, volúmenes y rutas."""
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    ocp = (j(e["datos"], {}).get("ocp") or {}) if e else {}
    n = next((x for x in ocp.get("namespaces_app") or [] if x.get("ns") == ns), None)
    if not n:
        abort(404)
    return render_template("namespace.html", e=e, n=n, sg=_ns_segmento(ns), recolectado=ocp.get("recolectado", ""), r=_resumen_ns(n, ocp))


def _cant(v):
    """«500m» → 0.5 cores · «512Mi» → 0.5 GB (para cuotas y requests)."""
    v = str(v or "").strip()
    try:
        if v.endswith("m"):
            return float(v[:-1]) / 1000
        for suf, f in (("Ki", 1 / 1048576), ("Mi", 1 / 1024), ("Gi", 1), ("Ti", 1024), ("K", 1e3 / 1073741824), ("M", 1e6 / 1073741824), ("G", 1e9 / 1073741824)):
            if v.endswith(suf):
                return float(v[:-len(suf)]) * f
        return float(v) if v else None
    except ValueError:
        return None


def _resumen_ns(n, ocp):
    """Vista consolidada de un namespace: salud, uso frente a lo reservado y a la cuota, cargas con sus pods, gobierno."""
    pods = n.get("l_pods") or []
    cargas = []
    for c in n.get("l_cargas") or []:
        ps = [p for p in pods if (p.get("dueno") or "") == c["n"] or (c["tipo"] == "StatefulSet" and (p.get("dueno") or "") == c["n"])]
        cpu = sum((p.get("uso") or {}).get("cpu") or 0 for p in ps)
        mem = sum((p.get("uso") or {}).get("mem_mb") or 0 for p in ps) / 1024
        img = (c.get("imagenes") or [""])[0]
        cargas.append(dict(c, pods=ps, cpu=round(cpu, 3), mem=round(mem, 2), img_corta=img.split("/")[-1][:60],
                           req_cpu=_cant((c.get("req") or {}).get("cpu")), req_mem=_cant((c.get("req") or {}).get("memory")),
                           ok=c.get("listos") == c.get("rep")))
    sueltos = [p for p in pods if not any(p in c["pods"] for c in cargas)]
    reinicios = [p for p in pods if int(p.get("reinicios") or 0) > 5]
    mal = [p for p in pods if p.get("fase") not in ("Running", "Succeeded")]
    req_cpu = sum((c["req_cpu"] or 0) * (c.get("rep") or 0) for c in cargas)
    req_mem = sum((c["req_mem"] or 0) * (c.get("rep") or 0) for c in cargas)
    cuota = {}
    for q_ in n.get("l_cuotas") or []:
        for k, v in (q_.get("hard") or {}).items():
            cuota[k] = {"hard": v, "used": (q_.get("used") or {}).get(k, "0")}
    gitops = [a for a in ((ocp.get("respaldo") or {}).get("gitops") or []) if a.get("ns_destino") == n["ns"]]
    motivos = []
    if mal:
        motivos.append((f"{len(mal)} pod(s) fuera de Running", 30))
    if [c for c in cargas if not c["ok"]]:
        motivos.append((f"{len([c for c in cargas if not c['ok']])} carga(s) sin todas sus réplicas listas", 25))
    if reinicios:
        motivos.append((f"{len(reinicios)} pod(s) con más de 5 reinicios", 10))
    if n.get("l_eventos"):
        motivos.append((f"{len(n['l_eventos'])} advertencia(s) recientes", 5))
    if not cuota:
        motivos.append(("sin ResourceQuota (T-07)", 5))
    if not n.get("limitranges"):
        motivos.append(("sin LimitRange (T-07)", 5))
    if [c for c in cargas if c["req_cpu"] is None]:
        motivos.append(("cargas sin requests de CPU declarados", 5))
    salud = max(0, 100 - sum(d for _, d in motivos))
    atencion = mal + [p for p in sueltos if p not in mal]
    return {"cargas": cargas, "sueltos": sueltos, "atencion": atencion, "reinicios": reinicios, "mal": mal, "req_cpu": round(req_cpu, 2), "req_mem": round(req_mem, 2),
            "cuota": cuota, "gitops": gitops, "motivos": motivos, "salud": salud,
            "nivel": "verde" if salud >= 85 else ("amarillo" if salud >= 60 else "rojo")}


@bp.route("/inventario/<int:eid>/puntos/<cod>")
@requiere_login
def punto(eid, cod):
    """Un punto de restauración: qué vuelve exactamente si se restaura, con el enlace a la fuente de cada eslabón."""
    from .puntos import lista as _pr_lista
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    ps = _pr_lista(g.cliente["id"], eid) if e else []
    p = next((x for x in ps if x["codigo"] == cod), None)
    if not p:
        abort(404)
    f = p["f"]
    # la imagen de nacimiento no cambia: si el punto se tomó antes de conocerla, se completa con la actual
    _o = (f.get("sistema") or {}).get("origen") or {}
    if not _o.get("registro"):
        _oa = __import__("app.puntos", fromlist=["x"]).origen_de(g.cliente, e)
        if _oa.get("imagen") or _oa.get("registro"):
            f.setdefault("sistema", {})["origen"] = dict(_oa, completado=True)
    reg = ((f.get("sistema") or {}).get("origen") or {}).get("registro") or {}
    conf = f.get("configuracion") or {}
    if not (f.get("paquetes") or {}).get("lista"):   # el punto no trae inventario de paquetes: la lista exacta desde Git
        _pg = __import__("app.puntos", fromlist=["x"]).paquetes_de_git(conf)
        if _pg:
            f["paquetes"] = dict(f.get("paquetes") or {}, **_pg)
    G = "http://x/git/"
    enlaces = {"imagen": git_publico(G + f"repositorio/-/packages/generic/{reg.get('artefacto', '')}/{reg.get('version', '')}") if reg else "",
               "rpm": git_publico(G + "repositorio/-/packages?type=rpm"),
               "rpm_paq": git_publico(G + "repositorio/-/packages?type=rpm&q="),
               "arbol": git_publico(G + f"{conf.get('repositorio') or 'repositorio/respaldo-servidores'}/src/commit/{conf.get('commit', '')}") if conf.get("commit") else "",
               "git_punto": git_publico(G + f"{__import__('app.puntos', fromlist=['x']).dueno_git()}/puntos-restauracion/src/branch/main/{e['nombre']}/{cod}.yml"),
               "registro": git_publico(G + "repositorio/-/packages"),
               "repo_conf": git_publico(G + "{}/src/branch/{}".format(*_repo_de(e)))}
    _sk_actual = [x for x in (e.get("skills") or []) if x not in ("kvm", "respaldo-git", "node-exporter", "windows-exporter", "monitoreo")]
    _sk_punto = {x.get("skill") for x in f.get("plataformas") or []}
    maq_skills = [x for x in _sk_actual if x not in _sk_punto]
    i_ = next(i for i, x in enumerate(ps) if x["codigo"] == cod)
    ant = ps[i_ + 1] if i_ + 1 < len(ps) else None
    fa = ant["f"] if ant else None
    _ca = (fa or {}).get("configuracion") or {}
    cambios_git = _cambios_git(e, conf.get("commit"), _ca.get("commit") if _ca.get("repositorio") == conf.get("repositorio") else None, conf.get("repositorio"))
    # último punto: lo que el agente o el inventario registraron después de tomarlo también entra en él (estado vigente)
    posteriores = []
    if i_ == 0:
        for repo_p, rama_p, desde_p in (((conf.get("continuo") or {}).get("repositorio"), (conf.get("continuo") or {}).get("rama"), (conf.get("continuo") or {}).get("commit")),
                                        (conf.get("repositorio"), conf.get("rama"), conf.get("commit"))):
            if not repo_p or not rama_p:
                continue
            for c_ in _cambios_git(e, rama_p, desde_p, repo_p):
                if c_["sha"] == desde_p:
                    break
                posteriores.append(dict(c_, despues=True, repo=repo_p))
    cambios_git = posteriores + cambios_git
    # cambios hechos con FaceOps (acciones ejecutadas sobre la máquina): hasta este punto, y los posteriores si es el último
    _tks = q("SELECT t.id, t.numero, t.estado, t.creado, t.fin, t.motivo, a.nombre_aap, a.tipo FROM tickets t LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id "
             "WHERE t.cliente_id=? AND (t.limite=? OR t.limite LIKE ? OR t.limite LIKE ? OR t.limite LIKE ?) AND t.estado IN ('exitoso','fallido','error','en curso','ejecutando') ORDER BY t.id DESC LIMIT 200",
             g.cliente["id"], e["nombre"], e["nombre"] + ",%", "%," + e["nombre"], "%," + e["nombre"] + ",%")
    cambios_fo = []
    for t_ in _tks:
        cu = t_["fin"] or t_["creado"] or ""
        cambios_fo.append(dict(t_, accion=(t_["nombre_aap"] or "").replace("FaceOps · ", ""), cuando=cu,
                               despues=cu > (p["creado"] or ""), nuevo=bool(ant) and (ant["creado"] or "") < cu <= (p["creado"] or "")))
    if i_ != 0:
        cambios_fo = [x for x in cambios_fo if not x["despues"]]
    k3 = lambda x: "|".join(x.split("|")[:3])
    pa = {x.split("|")[0]: x for x in ((fa or {}).get("paquetes") or {}).get("lista") or []}
    pb = {x.split("|")[0]: x for x in (f.get("paquetes") or {}).get("lista") or []}
    # sin inventario de paquetes en el punto anterior no hay con qué comparar: no se marca nada como cambiado
    paq_cambio = {n: ("nuevo" if n not in pa else "versión") for n, x in pb.items() if pa and (n not in pa or k3(pa[n]) != k3(x))}
    paq_quitados = [pa[n] for n in sorted(set(pa) - set(pb))] if pa and pb else []
    pla_ant = {x["plataforma"]: x for x in (fa or {}).get("plataformas") or []}
    app_ant = {x["aplicacion"]: x for x in (fa or {}).get("aplicaciones") or []}
    from .puntos import volumenes as _vols
    try:
        vols_actuales = _vols(g.cliente, e)
    except Exception:  # noqa: BLE001
        vols_actuales = []
    return render_template("punto.html", rec_pr=__import__("app.puntos", fromlist=["x"]).recuperacion(f), cambios_fo=cambios_fo, cambios_git=cambios_git, vols_actuales=vols_actuales, maq_skills=maq_skills, eslabones_ocp=__import__("app.puntos", fromlist=["x"]).ESLABONES_OCP, ant=ant, fa=fa, paq_cambio=paq_cambio, paq_quitados=paq_quitados, pla_ant=pla_ant, app_ant=app_ant, url_repo=__import__("app.paquetes", fromlist=["x"]).url_repo, e=e, p=p, enlaces=enlaces, ultimo=ps[0]["codigo"] == cod)


@bp.route("/inventario/<int:eid>/puntos/frecuencia", methods=["POST"])
@requiere_login
def punto_frecuencia(eid):
    """Cada cuánto FaceOps crea solo un punto de restauración de la máquina (cada hora, cada día o solo manual)."""
    from .puntos import FRECUENCIAS
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    fr = request.form.get("frecuencia", "")
    campo = "inventario_frecuencia" if request.form.get("que") == "inventario" else "puntos_frecuencia"
    validas = ("dia", "no") if campo == "inventario_frecuencia" else FRECUENCIAS
    if not e or fr not in validas or request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "No válido"}, 400
    d = j(q1("SELECT datos FROM equipos WHERE id=?", eid)["datos"], {})
    d[campo] = fr
    ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d, ensure_ascii=False), eid)
    auditar(g.usuario["usuario"], g.cliente["id"], "frecuencia de " + ("inventario" if campo == "inventario_frecuencia" else "puntos de restauración"), e["nombre"], {"frecuencia": fr})
    return {"ok": True}


@bp.route("/inventario/<int:eid>/puntos/<cod>/restaurar", methods=["POST"])
@requiere_login
def punto_restaurar(eid, cod):
    """Restaurar un punto: FaceOps orquesta la vuelta del conjunto (configuración, paquetes, plataformas y aplicaciones
    del commit del punto) con un ticket con aprobación en AAP. La máquina no se toca desde FaceOps."""
    from .bd import q1 as _q1
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    p = _q1("SELECT * FROM puntos_restauracion WHERE codigo=? AND equipo_id=? AND cliente_id=?", cod, eid, g.cliente["id"]) if e else None
    if not p or request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(404)
    f = j(p["ficha"], {})
    sha = (f.get("configuracion") or {}).get("commit") or ""
    if not sha:
        flash("Ese punto no tiene configuración en Git para restaurar.", "error")
        return redirect(url_for("consola.equipo", eid=eid) + "#puntos")
    r = _ticket_respaldo(VOLVER, e, {"rec_equipo": e["nombre"], "rec_version": sha, "rec_ip": e["ip"],
                                     "rec_repo": (f.get("configuracion") or {}).get("repositorio") or "repositorio/respaldo-servidores"},
                         f"Restaurar {e['nombre']} al punto {cod} (configuración {sha[:7]}, paquetes, plataformas y aplicaciones)")
    x = r[0] if isinstance(r, tuple) else r
    try:
        x = x.get_json() if hasattr(x, "get_json") else x
        if x.get("url"):
            return redirect(x["url"])
        flash(x.get("error") or "No se pudo crear el ticket", "error")
    except Exception:  # noqa: BLE001
        pass
    return redirect(url_for("consola.equipo", eid=eid) + "#puntos")


def _so_habilitar(so):
    """Sistema de la máquina perdida → la imagen con que «Habilitar servidor» la vuelve a crear (misma familia y versión mayor)."""
    s_ = (so or "").lower()
    m_ = re.search(r"(\d+)", s_)
    v = m_.group(1) if m_ else "9"
    if "windows" in s_:
        return "windows2022"
    if "ubuntu" in s_:
        return "ubuntu2404"
    if "rocky" in s_:
        return "rocky9"
    return f"rhel{v}" if v in ("5", "6", "7", "8", "9", "10") else "rhel9"


@bp.route("/inventario/<int:eid>/puntos/<cod>/recuperar-nueva", methods=["POST"])
@requiere_login
def punto_recuperar_nueva(eid, cod):
    """La máquina no responde: se recrea desde ESTE punto como una réplica nueva (misma imagen y versión de sistema, paquetes
    exactos, configuración del commit del punto, carpetas, software sin paquete y secretos). La caída queda conservada como
    <nombre>-fallida-<fecha> para revisarla; nada se borra. Ticket con aprobación; mide RTO y RPO."""
    from .bd import q1 as _q1
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    p = _q1("SELECT * FROM puntos_restauracion WHERE codigo=? AND equipo_id=? AND cliente_id=?", cod, eid, g.cliente["id"]) if e else None
    if not p or request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(404)
    f = j(p["ficha"], {})
    conf = f.get("configuracion") or {}
    if not conf.get("commit"):
        flash("Ese punto no tiene configuración en Git: no se puede recrear la máquina desde él.", "error")
        return redirect(url_for("consola.punto", eid=eid, cod=cod))
    r = _recursos(e)
    so_ = (f.get("sistema") or {}).get("so") or e.get("so")
    params = {"hab_interno": True, "hab_so": _so_habilitar(so_), "hab_destino": "onpremise", "hab_nombre_exacto": e["nombre"], "hab_reemplazar": True,
              "hab_conservar_fallida": True, "hab_nombre": "recuperado", "hab_replicas": "1", "hab_cpu": str(r["cpu"]), "hab_ram_gb": str(r["ram_gb"]),
              "hab_disco_gb": str(max(40, r["disco_gb"])), "hab_ambiente": e.get("ambiente") or "produccion",
              "rec_equipo": e["nombre"], "rec_version": conf["commit"], "rec_repo": conf.get("repositorio") or "repositorio/respaldo-servidores",
              "rec_punto": cod}
    res = _ticket_respaldo(RECUPERAR, e, params, f"Recuperar {e['nombre']} desde el punto {cod}: réplica nueva; la caída queda conservada para revisarla")
    x = res[0] if isinstance(res, tuple) else res
    x = x.get_json() if hasattr(x, "get_json") else x
    if x.get("url"):
        d_ = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else dict(e.get("datos") or {})
        d_["recuperacion"] = {"estado": "en recuperación", "punto": cod, "ticket": x.get("numero"), "desde": ahora(), "punto_creado": p["creado"]}
        ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d_, ensure_ascii=False), e["id"])
        return redirect(x["url"])
    flash(x.get("error") or "No se pudo crear el ticket", "error")
    return redirect(url_for("consola.punto", eid=eid, cod=cod))


@bp.route("/inventario/<int:eid>/recuperar", methods=["POST"])
@requiere_login
def recuperar_equipo(eid):
    """Recuperar servidor: recrea la máquina con su mismo nombre y la reconstruye desde el respaldo (ticket con aprobación)."""
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    r = _recursos(e)
    params = {"hab_interno": True, "hab_so": _so_habilitar(e.get("so")), "hab_destino": "onpremise", "hab_nombre_exacto": e["nombre"], "hab_reemplazar": True,
              "hab_nombre": "recuperado", "hab_replicas": "1", "hab_cpu": str(r["cpu"]), "hab_ram_gb": str(r["ram_gb"]),
              "hab_disco_gb": str(max(40, r["disco_gb"])), "hab_ambiente": e.get("ambiente") or "produccion",
              "rec_equipo": e["nombre"], "rec_version": request.form.get("sha", ""), "rec_repo": _repo_de(e)[0]}
    return _ticket_respaldo(RECUPERAR, e, params, f"Recuperar {e['nombre']} desde su respaldo en Git y el registro de paquetes")


@bp.route("/inventario/<int:eid>/credencial-web", methods=["POST"])
@requiere_login
def credencial_web(eid):
    """Credencial de la consola web del equipo, leída de la bóveda (queda en la auditoría quién la vio)."""
    from .boveda import web
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    c = (web(e["nombre"], request.form.get("plataforma") or None) or web(e["nombre"])) if e else None
    if not c:
        return {"error": "No hay credencial web en la bóveda para este equipo"}, 404
    auditar(g.usuario["usuario"], g.cliente["id"], "ver credencial web (bóveda)", e["nombre"], {"sitio": c.get("sitio", "")})
    return {"usuario": c.get("usuario", ""), "clave": c.get("clave", ""), "sitio": c.get("sitio", "")}


ELIMINAR = "FaceOps · Eliminar servidor"


def eliminable(e):
    """Máquinas virtuales que FaceOps controla en el hipervisor (las mismas que puede encender o apagar), salvo los nodos de un
    clúster OpenShift, que se retiran desde el clúster."""
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    return e.get("estado") != "retirado" and not d.get("nodo_ocp") and (e.get("categoria") or "servidor") != "clúster openshift" \
        and ("Servidores habilitados" in j(e.get("inventarios"), []) or energia_posible(e))


@bp.route("/inventario/<int:eid>/eliminar", methods=["POST"])
@requiere_login
def eliminar_equipo(eid):
    """Eliminar servidor: ticket con aprobación en AAP (lo saca del balanceo, apaga y retira la VM; disco y Git se conservan)."""
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(400)
    eqs = equipos_visibles()
    e = next((x for x in eqs if x["id"] == eid), None)
    if not e or not eliminable(e):
        return {"error": "Esta máquina no se puede eliminar desde FaceOps (no es una máquina virtual del hipervisor o es nodo de OpenShift)"}, 400
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], ELIMINAR)
    if not a:
        return {"error": f"Falta «{ELIMINAR}» en el catálogo (sincroniza con AAP)"}, 400
    bal = [{"nombre": x["nombre"], "ip": x["ip"]} for x in eqs if "nginx" in x["skills"] and x.get("ip") and x["id"] != e["id"]]
    params = {"el_equipo": e["nombre"], "el_ip": e["ip"], "el_balanceadores": bal, "hab_replicas_nuevas": 1}
    try:
        t = solicitar(g.usuario, g.cliente, a, (e["segmentos"] or [""])[0], params, f"Eliminar {e['nombre']} ({e['ip']})")
    except (PermissionError, ErrorAAP) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}


@bp.route("/inventario/<int:eid>/consola")
@requiere_login
def consola_equipo(eid):
    """Abre la consola (SSH) o el escritorio remoto (RDP) del equipo en el navegador, vía Guacamole."""
    from .consola import disponible, tipo_consola, url_consola
    from .inventario import sistema_eq
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e or g.perfil["modo"] != "escritura" or not disponible():
        abort(403)
    tipo = tipo_consola(e, sistema_eq(e))
    if not tipo:
        abort(404)
    auditar(g.usuario["usuario"], g.cliente["id"], "abrir consola" if tipo == "ssh" else "abrir escritorio remoto", e["nombre"],
            {"ip": e["ip"], "protocolo": tipo})
    comando = None
    ruta = request.args.get("log", "")
    if ruta and tipo == "ssh":   # solo archivos de log que la recolección encontró en la máquina
        if ruta not in {x["ruta"] for x in j(e["datos"], {}).get("logs") or []}:
            abort(404)
        import shlex
        comando = f"sudo less -R +G {shlex.quote(ruta)}; exec bash -l"
        auditar(g.usuario["usuario"], g.cliente["id"], "ver log", e["nombre"], {"archivo": ruta})
    destino = url_consola(g.usuario, e, tipo, comando)
    # Guacamole reutiliza la sesión anterior (otra máquina) y responde «no existe»: se cierra antes de abrir esta
    return f"""<!doctype html><meta charset="utf-8"><title>Abriendo {e['nombre']}…</title>
<body style="font-family:sans-serif;color:#555;padding:24px">Abriendo la consola de {e['nombre']}…
<script>(async function(){{
  const toks=[];
  for (const st of [localStorage, sessionStorage]) {{ try {{ for (const k of Object.keys(st)) if (/guac/i.test(k)) {{ toks.push(st.getItem(k)); st.removeItem(k); }} }} catch(e) {{}} }}
  for (const t of toks) {{ const v=(t||'').replace(/^"|"$/g,''); if (v && v.length < 200) {{ try {{ await fetch('/consola/api/session', {{method:'DELETE', headers:{{'Guacamole-Token': v}}}}); }} catch(e) {{}} }} }}
  location.replace({json.dumps(destino)});
}})();</script>"""


@bp.route("/inventario/<int:eid>", methods=["GET", "POST"])
@requiere_login
def equipo(eid):
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    if request.method == "POST":
        if g.perfil["modo"] != "escritura":
            abort(403)
        if request.form.get("accion") == "apagar":
            from .operacion import solicitar_apagado
            try:
                t = solicitar_apagado(g.usuario, g.cliente, e, request.form.get("motivo", "").strip())
                if request.headers.get("X-Inline"):
                    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}
                flash(f"{t['numero']}: apagado enviado a AAP; espera la aprobación", "ok")
                return redirect(url_for("consola.ticket", tid=t["id"]))
            except (PermissionError, ErrorAAP) as x:
                if request.headers.get("X-Inline"):
                    return {"error": str(x)}, 400
                flash(str(x), "error")
                return redirect(url_for("consola.equipo", eid=eid))
        manual = sorted({s.strip().lower() for s in request.form.get("skills", "").split(",") if s.strip()})
        ej("UPDATE equipos SET skills_manual=? WHERE id=?", json.dumps(manual), eid)
        auditar(g.usuario["usuario"], g.cliente["id"], "editar skills", e["nombre"], {"skills_manual": manual})
        from .inventario import calcular
        calcular(g.cliente)
        flash("Skills actualizadas; políticas y hallazgos recalculados", "ok")
        return redirect(url_for("consola.equipo", eid=eid))
    autos = autos_visibles()
    hs = [h for h in q("SELECT * FROM hallazgos WHERE equipo_id=?", eid)]
    from .operacion import aplica_a_equipo, requiere_aprobacion
    for h in hs:  # cada hallazgo trae su remediación lista para generar el ticket ahí mismo
        a = auto_sugerida(h["sugerencia"], autos)
        if a and aplica_a_equipo(g.usuario, g.cliente["id"], a, e):
            a = dict(a, campos_l=[c for c in j(a["campos"], []) if c["tipo"] not in ("equipo", "equipos")],
                     aprob=requiere_aprobacion(a, e.get("ambiente")), puede=puede_ejecutar(g.usuario, g.cliente["id"], a)[0])
        else:
            a = None
        h["auto"] = a
    acciones = []
    for a in sorted(autos, key=lambda x: (0 if x.get("demo") or x["categoria"] == "Skills" else 1, x.get("orden") or 999, x["nombre"])):
        if aplica_a_equipo(g.usuario, g.cliente["id"], a, e):
            a["campos_l"] = [c for c in j(a["campos"], []) if c["tipo"] not in ("equipo", "equipos")]
            a["aprob"] = requiere_aprobacion(a, e.get("ambiente"))
            a["puede"] = puede_ejecutar(g.usuario, g.cliente["id"], a)[0]
            acciones.append(a)
    from .consola import disponible, tipo_consola
    from .inventario import sistema_eq
    consola_tipo = tipo_consola(e, sistema_eq(e)) if disponible() and g.perfil["modo"] == "escritura" else None
    from .consola import web_de as _web_de
    _w = _web_de(e)
    web_eq = dict(_w, abrir=url_for("consola.redis_equipo", eid=e["id"]) if _w["producto"] == "Redis"
                  else _w["url"] if _w["producto"].startswith("Consola de OpenShift")  # OAuth propio: se abre directo, no por el proxy
                  else url_for("consola.web_equipo", eid=e["id"], sub="")) if _w else None
    from .boveda import web as _web_b, disponible as _boveda_ok
    if web_eq and web_eq["producto"] != "Redis" and g.perfil["modo"] == "escritura" and _boveda_ok() and _web_b(e["nombre"]):
        web_eq["credencial"] = url_for("consola.credencial_web", eid=e["id"])
    redis_eq = (url_for("consola.redis_equipo", eid=e["id"]) if ("redis" in e["skills"] or bool(_web_b(e["nombre"], "redis")))
                and not (web_eq and web_eq["producto"] == "Redis") else None)
    try:
        from .riesgo import evaluar as evaluar_riesgo
        riesgo_eq = evaluar_riesgo(g.cliente["id"], e)
    except Exception:  # noqa: BLE001 - sin la base de errores conocidos la ficha sigue
        riesgo_eq = None
    componentes = []
    if riesgo_eq:   # cada plataforma instalada por separado: sus accesos, su integración y sus errores conocidos
        from . import kb as kb_
        try:
            nombres = {p_["codigo"]: p_["nombre"] for p_ in kb_.productos()}
            integ = {x["producto"]: x for x in kb_.integraciones(g.cliente["id"]) if x["equipo"] == e["nombre"]}
        except Exception:  # noqa: BLE001
            nombres, integ = {}, {}
        for cod in riesgo_eq["productos"]:
            errs = [f for f in riesgo_eq["errores"] if f["producto"] == nombres.get(cod)]
            nom = nombres.get(cod, cod)
            acc = []
            cw = consola_plataforma(e, cod)
            if cw and g.perfil["modo"] == "escritura":
                acc.append({"t": cw["producto"], "url": url_for("consola.consola_web_plataforma", eid=e["id"], cod=cod, sub=""), "ic": "abrir", "nueva": True})
            elif cod == "openshift" and web_eq:
                acc.append({"t": "Consola de OpenShift", "url": web_eq["abrir"], "ic": "abrir", "nueva": True})
            if acc and web_eq and web_eq.get("credencial") and web_eq.get("producto", "").lower() in nom.lower():
                acc.append({"t": "Usuario y clave", "cred": web_eq["credencial"], "ic": "llave"})
            from .versiones import de as version_de
            ver = version_de(e, cod) if cod != "rhel" else None
            if ver and ver["version"] not in nom:
                nom = f"{nom} · versión {ver['version']}"
            componentes.append({"codigo": cod, "nombre": nom, "so": cod == "rhel", "errores": len(errs), "version": ver,
                                "activos": sum(1 for f in errs if f["activa"]),
                                "prevenidos": sum(1 for f in errs if f["cobertura"].get("estado") == "cubierto"),
                                "integracion": (integ.get(cod) or {}).get("estado"), "acciones": acc, "lista": errs})
        componentes.sort(key=lambda c: (not c["so"], c["nombre"]))
    from .identidad import NOMBRES_PLAT, plataformas as _plats
    for nom_ in _plats(e):   # plataformas que corre la máquina aunque la base de conocimiento aún no las tenga (p. ej. Keycloak recién incorporado)
        if not any(nom_.lower() in c["nombre"].lower() or c["nombre"].lower() in nom_.lower() for c in componentes):
            cod_ = next((k for k, v in NOMBRES_PLAT.items() if v == nom_), nom_.lower())
            cw_ = consola_plataforma(e, cod_)
            componentes.append({"codigo": cod_, "nombre": nom_, "so": False, "errores": 0, "activos": 0, "prevenidos": 0, "integracion": None,
                                "acciones": [{"t": cw_["producto"], "url": url_for("consola.consola_web_plataforma", eid=e["id"], cod=cod_, sub=""), "ic": "abrir", "nueva": True}]
                                if cw_ and g.perfil["modo"] == "escritura" else [], "lista": []})
    if not any(c["so"] for c in componentes) and (e.get("categoria") or "servidor") != "clúster openshift":
        # todas las fichas con el mismo formato: el sistema del equipo (Windows, VyOS, FortiOS…) es su primera «plataforma»
        componentes.insert(0, {"codigo": "so", "nombre": e.get("so") or (e.get("categoria") or "Sistema").capitalize(), "so": True, "sintetico": True,
                               "errores": 0, "activos": 0, "prevenidos": 0, "integracion": None, "acciones": [], "lista": []})
    if (e.get("categoria") or "") == "clúster openshift":   # OpenShift es el «sistema» del clúster (arriba, como RHEL en una máquina)
        for c in componentes:
            if c["codigo"] == "openshift":
                c["so"] = True
                c["ocp_base"] = True
    if (e.get("categoria") or "") == "clúster openshift":   # en un clúster, cada operador instalado es una plataforma
        for o_ in sorted((j(e["datos"], {}).get("ocp") or {}).get("operadores") or [], key=lambda x: (x.get("nombre") or x["n"]).lower()):
            if any(c["codigo"] == "op-" + o_["n"] for c in componentes):
                continue
            componentes.append({"codigo": "op-" + o_["n"], "nombre": o_.get("nombre") or o_["n"], "so": False, "op": o_, "errores": 0, "activos": 0,
                                "prevenidos": 0, "integracion": None, "acciones": [], "lista": [], "salud": None, "version": None})
    nodo_c = j(e["datos"], {}).get("nodo_ocp") or {}
    if nodo_c and not any(c["codigo"] == "openshift-nodo" for c in componentes):   # un nodo de OpenShift corre la plataforma OpenShift
        cl_ = q1("SELECT id, nombre, so FROM equipos WHERE id=?", nodo_c.get("cluster_id")) if nodo_c.get("cluster_id") else None
        componentes.append({"codigo": "openshift-nodo", "nombre": (cl_["so"] if cl_ and cl_["so"] else "OpenShift"), "so": False, "nodo": nodo_c,
                            "cluster": dict(cl_) if cl_ else None, "errores": 0, "activos": 0, "prevenidos": 0, "integracion": None, "lista": [],
                            "acciones": [{"t": f"Clúster {cl_['nombre']} en FaceOps", "url": url_for("consola.equipo", eid=cl_["id"]), "ic": "abrir"}] if cl_ else [],
                            "salud": None, "version": None})
    for c in componentes:   # icono para entrar a cada plataforma: consola SSH/RDP del sistema operativo, consola web, Redis…
        ent = None
        if c["so"] or (c["codigo"] == "windows" and consola_tipo):
            if consola_tipo:
                ent = {"url": url_for("consola.consola_equipo", eid=e["id"]), "t": "Consola SSH" if consola_tipo == "ssh" else "Escritorio remoto", "nueva": True,
                       "ic": consola_tipo}
        if not ent:
            a_ = next((a for a in c["acciones"] if a.get("url")), None)
            if a_:
                ent = {"url": a_["url"], "t": a_["t"], "nueva": True, "ic": "web"}
        c["entrar"] = ent
        if c["codigo"] == "satellite":   # Satellite instalado por FaceOps: se entra como admin y su salud sale de sus servicios
            c["entrar"] = {"url": url_for("consola.consola_satellite", eid=e["id"]), "t": "Consola de Satellite (admin)", "nueva": True, "ic": "web"}
            sv_ = " ".join(j(e.get("servicios"), [])).lower()
            req_ = ["foreman", "httpd", "postgresql", "pulpcore", "tomcat", "redis"]
            ok_ = [x for x in req_ if x in sv_]
            if sv_:
                c["salud"] = round(100 * len(ok_) / len(req_))
                c["nivel"] = "verde" if c["salud"] >= 85 else ("amarillo" if c["salud"] >= 60 else "rojo")
                c["motivos"] = [f"servicio {x} detenido" for x in req_ if x not in ok_]
    comp_alias = []   # para clasificar cada cambio del historial: sistema operativo o la plataforma a la que pertenece
    try:
        from . import kb as kb_
        info_p = {p_["codigo"]: p_ for p_ in kb_.q("SELECT codigo, nombre, skill, detecta FROM productos")} if kb_.disponible() else {}
    except Exception:  # noqa: BLE001
        info_p = {}
    for c in componentes:
        p_ = info_p.get(c["codigo"], {})
        al = {x.lower() for x in (p_.get("detecta") or [])} | ({p_["skill"].lower()} if p_.get("skill") else set()) | {c["codigo"].lower()}
        comp_alias.append({"codigo": c["codigo"], "nombre": c["nombre"], "so": c["so"], "alias": sorted(a for a in al if len(a) >= 3)})
    def _de_comp(a):   # a qué plataforma pertenece una automatización (por su nombre); si no es de ninguna, al sistema operativo
        t = f"{a.get('nombre') or ''} {a.get('nombre_aap') or ''}".lower()
        m = [c["codigo"] for c in comp_alias if not c["so"] and any(x in t for x in c["alias"])]
        return m or [next((c["codigo"] for c in comp_alias if c["so"]), None)]
    from .inventario import segmentos_candidatos
    e["seg_cand"] = [x for x in segmentos_candidatos(e, q("SELECT * FROM segmentos WHERE cliente_id=?", g.cliente["id"])) if x not in e["segmentos"]]
    compat_all = _autos_compatibles(e)
    # plataformas que se pueden incorporar a esta máquina: automatizaciones compatibles que habilitan una skill que aún no tiene
    # plataformas en incorporación: tickets en curso sobre esta máquina que habilitan una skill que aún no tiene
    incorporando = [dict(t, nombre_auto=t["anombre"]) for t in q(
        "SELECT t.id, t.numero, t.estado, a.nombre anombre, a.habilita_skill FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
        "WHERE t.cliente_id=? AND t.limite=? AND t.estado IN ('pendiente_aprobacion','en_cola','en_ejecucion') AND COALESCE(a.habilita_skill,'')!='' "
        "ORDER BY t.id", g.cliente["id"], e["nombre"]) if t["habilita_skill"] not in (e.get("skills") or [])]
    desinstalando = {j(t["parametros"], {}).get("dp_familia"): t for t in q(
        "SELECT t.id, t.numero, t.estado, t.parametros FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND t.limite=? "
        "AND a.nombre_aap='Plataforma · Desinstalar' AND t.estado IN ('pendiente_aprobacion','en_cola','en_ejecucion')", g.cliente["id"], e["nombre"])}
    plat_nuevas, vistos_ = [], {t["habilita_skill"] for t in incorporando}
    d_ = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    sin_registro = (e.get("so") or "").lower().startswith(("redhat", "red hat")) and d_.get("registrado") is False   # sin repositorios de Red Hat
    reg_auto = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='DC · Registrar en Red Hat'", g.cliente["id"]) if sin_registro else None
    for _ar, autos_ in compat_all:
        for a_ in autos_:
            sk_ = a_.get("habilita_skill")
            if sk_ and sk_ not in (e.get("skills") or []) and sk_ not in vistos_:
                vistos_.add(sk_)
                faltan = [c for c in j(a_.get("campos"), []) if c.get("requerido") and c.get("defecto") in (None, "") and c["tipo"] not in ("equipo", "equipos")]
                plat_nuevas.append({"id": a_["id"], "nombre": a_["nombre"], "skill": sk_, "desc": (j(a_.get("guia"), {}).get("proposito") or a_.get("descripcion") or "")[:180],
                                    "datos": bool(faltan)})
    # compatibles pero aún sin su prueba aprobada: se muestran (no se pueden lanzar hasta pasar el plan de prueba)
    # y las que solo fallan por recursos (memoria): se muestran con la alerta para ajustar antes de incorporar
    from .compatibilidad import compatible as _compat_, falta_recurso as _falta_
    for a_ in autos_visibles(todas=True):
        sk_ = a_.get("habilita_skill")
        if sk_ and sk_ not in (e.get("skills") or []) and sk_ not in vistos_ and a_.get("usa_limite") and _compat_(a_, e, recursos=False)[0]:
            vistos_.add(sk_)
            plat_nuevas.append({"id": a_["id"], "nombre": a_["nombre"], "skill": sk_, "desc": (j(a_.get("guia"), {}).get("proposito") or a_.get("descripcion") or "")[:180],
                                "datos": False, "bloqueada": bool(a_.get("_bloqueada")), "alerta": _falta_(a_, e)})
    plat_nuevas = [x for x in plat_nuevas if x["skill"] not in AGENTES_SK]
    from .manifiesto import permite as _mf_permite   # manifiesto de arquitectura: solo lo aceptado se ofrece
    for x in plat_nuevas:
        x["mf_ok"], x["mf_nota"] = _mf_permite(g.cliente["id"], x["skill"])
    plat_nuevas = [x for x in plat_nuevas if x["mf_ok"]]
    agentes_eq = _agentes_de(e, d_)
    _acc = __import__("app.boveda", fromlist=["x"]).acceso(e["nombre"]) or {}
    if agentes_eq is not None and (e.get("categoria") or "servidor") in ("servidor", "estación de trabajo"):
        agentes_eq.insert(0, {"skill": "acceso", "nombre": "Acceso de FaceOps", "instalado": bool(_acc.get("usuario")), "opcional": False,
                              "que": (f"Usuario de servicio «{_acc['usuario']}» con llave propia guardada en la bóveda: por él AAP opera la máquina y hace el inventario diario."
                                      if _acc.get("usuario") else "Usuario de servicio con llave propia en la bóveda: por él AAP opera la máquina."),
                              "falta": "Sin él, AAP usa una credencial compartida.", "aid": None, "probada": False, "pendiente": None, "limite": "se vincula al incorporar la máquina"})
    for c in componentes:
        c["acc"] = [a for a in acciones if c["codigo"] in _de_comp(a)]
        c["compat"] = [(n, xs) for n, xs in ((n, [a for a in autos if c["codigo"] in _de_comp(a)]) for n, autos in compat_all) if xs]
    hist_tickets = [{"numero": t["numero"], "auto": t["auto"] or "", "nombre_aap": t["nombre_aap"] or "", "estado": t["estado"], "autor": t["solicitante"] or "",
                     "fecha": t["fin"] or t["creado"] or "", "inicio": t["inicio"] or t["creado"] or "", "fin": t["fin"] or "",
                     "url": url_for("consola.ticket", tid=t["id"])}
                    for t in q("SELECT t.id, t.numero, t.estado, t.creado, t.inicio, t.fin, a.nombre auto, a.nombre_aap, u.nombre solicitante FROM tickets t "
                               "LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id LEFT JOIN usuarios u ON u.id=t.usuario_id "
                               "WHERE t.cliente_id=? AND ((':' || COALESCE(t.limite,'') || ':') LIKE ? OR t.parametros LIKE ?) ORDER BY t.creado DESC LIMIT 100",
                               g.cliente["id"], f"%:{e['nombre']}:%", f'%"{e["nombre"]}"%')]
    salud_eq = None
    try:   # salud por componente y global (reemplaza el puntaje de riesgo como indicador principal)
        from . import kb as kb_
        from .salud import de_equipo as salud_de
        from .evaluacion_so import de_equipo as _ev_de
        _ext = {"cves": _cves_equipo(e), "politicas": _politicas_equipo(e),
                "eval_so": _ev_de(g.cliente["id"], e["nombre"]) if sistema_de(e) == "linux" else None}
        salud_eq = salud_de(e, riesgo_eq, kb_.q("SELECT codigo, nombre, skill, detecta FROM productos") if riesgo_eq else [], _ext)
        sc = {c["codigo"]: c for c in salud_eq["componentes"]}
        for c in componentes:
            if c.get("sintetico"):
                from .salud import componente_sistema
                c.update(componente_sistema(e, _ext))
                continue
            if c.get("op"):
                from .salud import componente_operador
                c.update(componente_operador(c["op"]))
                continue
            if c.get("nodo"):
                from .salud import componente_nodo_ocp
                c.update(componente_nodo_ocp(c["nodo"]))
                continue
            if c["codigo"] == "satellite" and c.get("salud") is not None and c["codigo"] not in sc:
                continue
            c.update({k: sc.get(c["codigo"], {}).get(k) for k in ("salud", "nivel", "motivos", "secciones", "umbral")})
        from .radar import radar_componente
        _r_maq = _radar_de([e])
        for c in componentes:
            c["radar"] = radar_componente(_r_maq, c)
    except Exception:  # noqa: BLE001
        from .salud import encendido
        salud_eq = {"estado": encendido(e), "salud": None, "nivel": "sin datos", "componentes": []}
    from .evaluacion_so import de_equipo as eval_so_de
    eval_so = eval_so_de(g.cliente["id"], e["nombre"]) if sistema_de(e) == "linux" else None
    from .informe_so import secuencia as secuencia_so_de, ultimo as informe_ultimo
    secuencia_so = secuencia_so_de(e, eval_so) if eval_so is not None else []
    try:
        informe_so = informe_ultimo(g.cliente["id"], e["nombre"])
    except Exception:  # noqa: BLE001
        informe_so = None
    from .paquetes import de_equipo as paquetes_de
    from .inventariado import antiguedad as antig_inv
    try:
        from .puntos import origenes, _nombre_vm, registro_generico
        _org = origenes(g.cliente)
        disco_origen = dict(_org.get(_nombre_vm(e, _org)) or {}) or None
        if disco_origen and disco_origen.get("sha256"):
            _gen = registro_generico().get(disco_origen["sha256"])
            if _gen:
                disco_origen["registro"] = {"artefacto": _gen[0], "version": _gen[1], "igual": True}
    except Exception:  # noqa: BLE001
        disco_origen = None
    try:
        from .puntos import lista as _pr_lista
        puntos_r = _pr_lista(g.cliente["id"], e["id"])
    except Exception:  # noqa: BLE001
        puntos_r = []
    from .puntos import frecuencia as _pr_frec
    puntos_frec = _pr_frec(e)
    from .puntos import dueno_git
    puntos_git = git_publico(f"http://x/git/{dueno_git()}/puntos-restauracion/src/branch/main/" + e["nombre"]) if puntos_r else ""
    inv_frec = j(e["datos"], {}).get("inventario_frecuencia") or "dia"
    storages = [x["nombre"] for x in equipos_visibles() if "storage-emulado" in (x.get("skills") or [])]
    # último cambio de estado de energía: el que vio FaceOps o, si no, el arranque según el tiempo encendido de la última recolección
    ult_estado = e.get("energia_cambio") or ""
    if not ult_estado and e.get("ultima_recoleccion") and (j(e["datos"], {}).get("uptime_dias") is not None):
        try:
            from datetime import datetime as _dt, timedelta as _td
            ult_estado = (_dt.fromisoformat(str(e["ultima_recoleccion"]).replace("Z", "+00:00")) - _td(days=float(j(e["datos"], {})["uptime_dias"]))).isoformat(timespec="seconds")
        except (ValueError, TypeError):
            ult_estado = ""
    monit = _monitoreo_url(e)
    recursos_ok = (g.perfil["modo"] == "escritura" and (e.get("forma") or "") == "virtual" and "kvm" not in (e.get("skills") or [])
                   and bool(q1("SELECT 1 FROM infraestructuras WHERE cliente_id=? AND tipo='kvm' AND ubicacion=?", g.cliente["id"], e.get("ubicacion") or "")))
    lim_rec = _limites_recursos(e) if recursos_ok else None
    from .duenos import de as dueno_de, candidatos as dueno_cand
    from .alertas_objeto import contexto as alertas_ctx
    from .hardware_fisico import es_fisico as _es_fis, ficha as _ficha_hw
    from .boveda import acceso as _acceso_maq
    _usu_fo = (_acceso_maq(e["nombre"]) or {}).get("usuario") or ("ansible" if (e.get("forma") or "") != "nube" else "faceops")
    from .grupos_dueno import grupo as _grupo_d
    from .areas import NOMBRES_SECCION as _NS
    _sec_resp = {"almacenamiento": "storage", "router": "redes", "switch": "redes", "firewall": "redes", "balanceador": "redes",
                 "clúster openshift": "contenedores"}.get((e.get("categoria") or "servidor").lower(), "servidores")
    area_resp = _grupo_d(g.cliente["id"], _sec_resp)
    try:
        from .uso_energia import uso as _uso_energia
        horas_eq = _uso_energia(e, j(e["datos"], {}))
    except Exception:  # noqa: BLE001 - sin historial la ficha sigue
        horas_eq = None
    return render_template("equipo.html", horas_eq=horas_eq, recuperacion_pr=__import__("app.puntos", fromlist=["x"]).recuperacion, autos_id={a_["nombre_aap"]: a_["id"] for a_ in autos_visibles()}, radar_eq=_radar_maquina(e, componentes, puntos_r), git_url=git_publico((j(e["datos"], {}).get("git") or {}).get("url") or ""), agentes_eq=agentes_eq, area_resp=area_resp, lim_rec=lim_rec, usuario_faceops=_usu_fo, hw_fis=_ficha_hw(e) if _es_fis(e) else None, alertas_obj=alertas_ctx(g.cliente["id"], e, g.perfil["modo"] == "escritura"), acciones_so=_acciones_so(e), dueno=dueno_de(g.cliente["id"], "equipo", e["id"]), dueno_cand=dueno_cand(g.cliente["id"]), recursos_ok=recursos_ok, uso_eq=uso_equipo(e, j(e["datos"], {}), j(e["discos"], [])), ns_segmento=_ns_segmento, nodo_equipo=_nodo_equipo, servicios_de=servicios_de, servicio_fijo=servicio_fijo, logs_de=logs_de, ids_seguridad=_ids_seguridad, url_repo=__import__("app.paquetes", fromlist=["x"]).url_repo, inv_frec=inv_frec, ult_estado=ult_estado, monit=monit, pr_ult=(puntos_r[0]["f"] if puntos_r else None), reg_url=git_publico("http://x/git/repositorio/-/packages"), puntos_frec=puntos_frec, storages=storages, disco_origen=disco_origen, puntos_r=puntos_r, puntos_git=puntos_git, inv_maq=antig_inv(e), paquetes=paquetes_de(j(e["datos"], {}), e.get("skills")), servicio_estado=servicio_estado, servicios_plat=SERVICIOS_PLATAFORMA, familia=familia, hipervisor=_hipervisor(e), uso_seg=_uso_seg(e), energia_ok=energia_posible(e) and g.perfil["modo"] == "escritura", secuencia_so=secuencia_so, informe_so=informe_so, comp_alias=comp_alias, hist_tickets=hist_tickets, salud_eq=salud_eq, riesgo_eq=riesgo_eq, eval_so=eval_so, componentes=componentes, plat_nuevas=plat_nuevas, incorporando=incorporando, sin_registro=sin_registro, reg_auto=reg_auto, desinstalando=desinstalando, paquetes_plat=PAQUETES_PLATAFORMA, desinstalable=desinstalable, redis_eq=redis_eq, web_eq=web_eq, cves_eq=_cves_equipo(e), puede_eliminar=eliminable(e) and g.perfil["modo"] == "escritura", autos_compat=compat_all, pols_eq=_politicas_equipo(e), e=e, datos=j(e["datos"], {}), consola_tipo=consola_tipo, acciones=acciones, discos=j(e["discos"], []),
                           servicios=j(e["servicios"], []), grupos=j(e["grupos"], []), hallazgos=hs,
                           cumplimiento=cumplimiento(e, g.cliente["id"]),
                           manual=j(e["skills_manual"], []))


@bp.route("/segmentos")
@requiere_login
def segmentos():
    return redirect(url_for("consola.mapa"))


def _gobierno_desactivado():   # vista de Gobierno (segmentos, skills, políticas): fuera del menú por ahora
    eqs = equipos_visibles()
    segs = segmentos_visibles(g.usuario, g.cliente["id"])
    from .grupos_segmento import GRUPOS, NOMBRE, grupo_de
    for s in segs:
        s["equipos"] = [e for e in eqs if s["nombre"] in e["segmentos"]]
        s["regla_d"] = j(s["regla"], {})
        s["grupo_ef"] = grupo_de(s)
    segs.sort(key=lambda x: ([k for k, *_ in GRUPOS].index(x["grupo_ef"]), x.get("orden") or 0, x["nombre"]))
    sk = q("SELECT * FROM skills WHERE cliente_id=? ORDER BY nombre", g.cliente["id"])
    autos_por_nombre = {a["nombre_aap"]: a for a in autos_visibles()}
    for s in sk:
        s["auto"] = autos_por_nombre.get(s.get("automatizacion") or "")
        s["equipos"] = sum(1 for e in eqs if s["nombre"] in e["skills"])
        s["regla_d"] = j(s["regla"], {})
    pol = q("SELECT * FROM politicas WHERE cliente_id=? ORDER BY nombre", g.cliente["id"])
    for p in pol:
        p["aplica_d"], p["controles_d"] = j(p["aplica"], {}), j(p["controles"], [])
        p["equipos"] = sum(1 for e in eqs if any(x["id"] == p["id"] for x in politicas_de(e, g.cliente["id"])))
    from .segmentos_archivo import lista as archivados
    return render_template("segmentos.html", segmentos=segs, skills=sk, politicas=pol, archivados=archivados(g.cliente["id"]),
                           grupos=GRUPOS, nombre_grupo=NOMBRE, areas=sorted({s_.get("area") for s_ in segs if s_.get("area")}))


@bp.route("/segmentos/grupo", methods=["POST"])
@requiere_login
def segmento_grupo():
    """Cambia el grupo (entrada del menú principal) de un segmento de negocio."""
    from .grupos_segmento import NOMBRE, grupo_de, normalizar
    if g.perfil["modo"] != "escritura" or request.form.get("_csrf") != session.get("csrf"):
        abort(403)
    seg, gr = request.form.get("segmento", ""), normalizar(request.form.get("grupo"))
    gr = gr or grupo_de(q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre=?", g.cliente["id"], seg) or {})
    if not gr or not puede_ver_segmento(g.usuario, g.cliente["id"], seg):
        abort(400)
    ej("UPDATE segmentos SET grupo=?, area=? WHERE cliente_id=? AND nombre=?", gr, (request.form.get("area") or "").strip()[:60], g.cliente["id"], seg)
    imp = (request.form.get("impacto") or "").strip()
    if imp in ("crítica", "alta", "media", "baja"):   # impacto en el negocio: los críticos y de impacto alto encienden el indicador de riesgo
        r_ = q1("SELECT ficha FROM segmentos WHERE cliente_id=? AND nombre=?", g.cliente["id"], seg) or {}
        fi = j(r_.get("ficha"), {})
        fi["criticidad"] = imp
        ej("UPDATE segmentos SET ficha=? WHERE cliente_id=? AND nombre=?", json.dumps(fi, ensure_ascii=False), g.cliente["id"], seg)
        from . import riesgo_global
        riesgo_global._cache.pop(g.cliente["id"], None)
    auditar(g.usuario["usuario"], g.cliente["id"], "cambiar grupo de segmento", seg, {"grupo": gr})
    flash(f"«{seg}»: {NOMBRE[gr]}" + (f" · área {request.form.get('area').strip()}" if (request.form.get("area") or "").strip() else ""), "ok")
    return redirect(request.referrer or url_for("consola.mapa"))


@bp.route("/aprobaciones/trazabilidad")
@requiere_login
def aprobaciones_traza():
    """Trazabilidad de aprobaciones por persona: qué aprobó o rechazó cada uno, cuándo, con qué comentario y cuánto tardó."""
    quien = request.args.get("persona", "")
    filas = q("SELECT e.ticket_id, e.etapa, e.total, e.area, e.inicio, e.fin, e.estado, e.quien, t.numero, t.estado t_estado, t.respuesta, "
              "a.nombre auto FROM ticket_etapas e JOIN tickets t ON t.id=e.ticket_id LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id "
              "WHERE t.cliente_id=? AND e.estado IN ('aprobada','rechazada') AND COALESCE(e.quien,'')!='' ORDER BY e.fin DESC LIMIT 2000", g.cliente["id"])
    from .aprobacion_areas import filtrar_tickets
    vis = {t["id"] for t in filtrar_tickets(q("SELECT * FROM tickets WHERE cliente_id=?", g.cliente["id"]), g.usuario, g.cliente["id"])}
    filas = [f for f in filas if f["ticket_id"] in vis]
    personas = defaultdict(lambda: {"aprobadas": 0, "rechazadas": 0, "horas": [], "areas": set(), "ultima": ""})
    for f in filas:
        p_ = personas[f["quien"]]
        p_["aprobadas" if f["estado"] == "aprobada" else "rechazadas"] += 1
        p_["areas"].add(f["area"] or "—")
        p_["ultima"] = max(p_["ultima"], f["fin"] or "")
        try:
            from datetime import datetime as _d
            p_["horas"].append((_d.fromisoformat(f["fin"]) - _d.fromisoformat(f["inicio"])).total_seconds() / 3600)
        except Exception:  # noqa: BLE001
            pass
    resumen = sorted(({"quien": k, "aprobadas": v["aprobadas"], "rechazadas": v["rechazadas"], "areas": sorted(v["areas"]), "ultima": v["ultima"],
                       "espera_h": round(sum(v["horas"]) / len(v["horas"]), 1) if v["horas"] else None} for k, v in personas.items()),
                     key=lambda x: -(x["aprobadas"] + x["rechazadas"]))
    detalle = [f for f in filas if f["quien"] == quien][:300] if quien else []
    return render_template("aprobaciones_traza.html", resumen=resumen, detalle=detalle, persona=quien)


# ------------------------------------------------------------------ DevSecOps: pipelines Tekton como código
def _dso_semilla():
    import glob as _g
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "devsecops")
    host = os.environ.get("FACEOPS_HOST_IP", "192.168.122.1")
    return {os.path.basename(f).rsplit(".", 1)[0]: open(f).read().replace("__HOST__", host) for f in _g.glob(os.path.join(base, "*.yaml"))}


def _certificada(a):
    """Regla de prueba: la versión vigente de la acción pasó su plan de prueba (si cambia, se vuelve a bloquear)."""
    from .pruebas import certificada
    a_ = a if a.get("tecnico") is not None and a.get("cliente_id") else q1("SELECT * FROM automatizaciones WHERE id=?", a["id"])
    return certificada(dict(a_, cliente_id=a_["cliente_id"]))


def _desbloqueadas():
    """Automatizaciones que se muestran habilitadas aunque aún no tengan una ejecución exitosa (datos/catalogo.yml › desbloqueadas)."""
    import yaml as _y
    try:
        return set((_y.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "catalogo.yml"))) or {}).get("desbloqueadas") or [])
    except (OSError, _y.YAMLError):
        return set()


_SAT_FICHAS = {}   # ficha de un solo uso (60 s) → equipo: abre la consola de Satellite como admin sin mostrar la clave


@bp.route("/inventario/<int:eid>/consola-satellite")
@requiere_login
def consola_satellite(eid):
    """Satellite instalado por FaceOps: el usuario entra directo como admin. FaceOps emite una ficha de un solo uso y lo
    manda a la consola publicada; allí FaceOps inicia la sesión de Satellite por él (la clave sigue solo en la bóveda)."""
    import secrets as _s
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e or "satellite" not in (e.get("skills") or []):
        abort(404)
    if (g.get("perfil") or {}).get("modo") != "escritura":
        abort(403)
    tok = _s.token_urlsafe(24)
    _SAT_FICHAS[tok] = (e["nombre"], e["ip"], time.time() + 60, g.usuario["usuario"])
    auditar(g.usuario["usuario"], g.cliente["id"], "abrir consola de Satellite", e["nombre"], {})
    ip_pub = os.environ.get("FACEOPS_IP_PUBLICA", "34.176.42.56")
    return redirect(f"https://satellite.{ip_pub}.sslip.io:{os.environ.get('FACEOPS_PUERTO_PUBLICO', '8843')}/faceops-entrar?t={tok}")


@bp.route("/satellite/entrar")
def satellite_entrar():
    """Llega por el proxy de la consola de Satellite (mismo sitio que Satellite): canjea la ficha, inicia sesión en Satellite
    como admin y deja la cookie de sesión de Satellite en el navegador."""
    import re as _re
    from requests.utils import dict_from_cookiejar
    from .boveda import web as cred_web
    f = _SAT_FICHAS.pop(request.args.get("t", ""), None)
    if not f or f[2] < time.time():
        abort(403, "Ficha vencida: vuelve a abrir la consola desde FaceOps")
    nombre, ip, _, quien = f
    cr = cred_web(nombre, "satellite") or {}
    if not cr.get("clave"):
        abort(503, "La clave de Satellite no está en la bóveda")
    fqdn = f"{nombre}.invoke.lab"
    sx = requests.Session()
    sx.verify = False
    h = {"Host": fqdn, "Origin": f"https://{fqdn}", "Referer": f"https://{fqdn}/users/login"}
    r = sx.get(f"https://{ip}/users/login", headers=h, timeout=20)
    m = _re.search(r'name="authenticity_token" value="([^"]+)"', r.text) or _re.search(r'name="csrf-token" content="([^"]+)"', r.text)
    sx.post(f"https://{ip}/users/login", headers=h, timeout=30, allow_redirects=False,
            data={"authenticity_token": m.group(1) if m else "", "login[login]": cr.get("usuario", "admin"), "login[password]": cr["clave"]})
    galletas = dict_from_cookiejar(sx.cookies)
    if not galletas:
        abort(502, "Satellite no inició la sesión")
    resp = redirect("/")
    for k, v in galletas.items():
        resp.set_cookie(k, v, secure=True, httponly=True, samesite="Lax", path="/")
    return resp


@bp.route("/integraciones", methods=["GET", "POST"])
@requiere_login
def integraciones():
    """Gobierno › Integraciones: herramientas externas del cliente por categoría (tickets, acción, bóveda…)."""
    from . import conexiones as cx
    if not g.usuario.get("es_admin"):
        abort(403)
    cat = request.args.get("cat", "")
    todos = cx.catalogo(g.cliente)
    grupos = [x for x in todos if not cat or x["clave"] == cat]
    res = {x["clave"]: {"titulo": x["titulo"], "op": x["operativas"],
                        "nombres": [i["nombre"] for i in x["items"] if i["estado"] == "operativa"][:2]} for x in todos}   # para el diagrama
    return render_template("integraciones.html", grupos=grupos, cat=cat, categorias=cx.CATEGORIAS, res=res)


@bp.route("/integraciones/<tipo>", methods=["GET", "POST"])
@requiere_login
def integracion(tipo):
    """Detalle de una integración: parámetros de conexión (editables), prueba de conexión y estado."""
    from . import conexiones as cx
    if not g.usuario.get("es_admin"):
        abort(403)
    if tipo not in cx.TIPOS:
        abort(404)
    if request.method == "POST":
        try:
            if request.form.get("accion") == "probar":
                r_ = cx.probar(g.cliente, tipo)
                flash(("Conexión correcta: " if r_.get("ok") else "La prueba falló: ") + r_["detalle"], "ok" if r_.get("ok") else "error")
            else:
                cx.guardar(g.cliente, tipo, request.form, g.usuario["usuario"])
                flash("Parámetros guardados" + (" · clave en la bóveda" if request.form.get("clave") else ""), "ok")
            auditar(g.usuario["usuario"], g.cliente["id"], "integración: " + (request.form.get("accion") or "guardar"), tipo)
        except ValueError as x_:
            flash(str(x_), "error")
        return redirect(url_for("consola.integracion", tipo=tipo, editar=1 if request.form.get("accion") != "probar" and False else None))
    x = cx.detalle(g.cliente, tipo)
    if not x:
        abort(404)
    return render_template("integracion.html", x=x, campos=cx.CAMPOS, editar=request.args.get("editar") == "1")


@bp.route("/devsecops", methods=["GET", "POST"])
@requiere_login
def devsecops():
    """Herramientas de CI/CD del cliente (Tekton, Jenkins, Azure DevOps…): una caja por herramienta; de ahí, sus pipelines."""
    from . import devsecops as dso
    eqs = equipos_visibles()
    if request.method == "POST":
        _escritura()
        try:
            dso.agregar_herramienta(g.cliente["id"], request.form)
            auditar(g.usuario["usuario"], g.cliente["id"], "agregar herramienta CI/CD", request.form.get("nombre", "")[:80])
            flash("Herramienta registrada. Queda en gris hasta que FaceOps la conecte y la pruebe.", "ok")
        except ValueError as x:
            flash(str(x), "error")
        return redirect(url_for("consola.devsecops"))   # antes apuntaba a una herramienta inexistente (error al agregar)
    cajas = []
    n_tk = None
    for h in dso.herramientas(g.cliente["id"], eqs):
        t = dso.TIPOS.get(h["tipo"], {})
        c = dict(h, tipo_nombre=t.get("nombre", h["tipo"]), detalle=t.get("detalle", ""), color=t.get("color", "#6a6e73"),
                 url_det=url_for("consola.devsecops_herramienta", hid=h["id"]), n=None, estado="sin conectar", no_probado=True)
        if h["tipo"] == "tekton":
            try:
                n_tk = len(dso.listar()) if n_tk is None else n_tk
                ult = q1("SELECT * FROM devsecops_corridas WHERE cliente_id=? ORDER BY COALESCE(NULLIF(inicio,''), visto) DESC LIMIT 1", g.cliente["id"])
                c.update(n=n_tk, estado="operativa", no_probado=False, ultima=ult)
            except (requests.RequestException, Exception):  # noqa: BLE001 - sin Git la caja queda en gris
                c["estado"] = "sin acceso al repositorio"
        elif h["tipo"] == "jenkins":
            e = next((x for x in eqs if x["nombre"] == h["equipo"]), None)
            if e and "jenkins" in (e.get("skills") or []):
                c.update(estado="instalada · por probar", eid=e["id"])
            elif e:
                c.update(estado="servidor listo · falta instalar Jenkins", eid=e["id"])
        cajas.append(c)
    return render_template("devsecops_herramientas.html", cajas=cajas, tipos=dso.TIPOS, usos=dso.USOS)


def _tec_urls():
    """Dónde ver en FaceOps cada herramienta que hace una etapa (las que corren como contenedor, en su herramienta de CI/CD)."""
    tk = q1("SELECT id FROM devsecops_herramientas WHERE cliente_id=? AND tipo='tekton' ORDER BY id LIMIT 1", g.cliente["id"])
    en_tk = url_for("consola.devsecops_herramienta", hid=tk["id"]) if tk else url_for("consola.devsecops")
    base = {k: (en_tk, "contenedor en Tekton") for k in ("gitleaks", "trivy", "owasp-dc", "cosign", "buildah", "maven", "dotnet", "python", "node", "sonarqube")}
    base.update({"aap": (url_for("consola.automatizaciones"), "automatizaciones de AAP administradas por FaceOps"),
                 "acs": (url_for("consola.politicas"), "políticas de seguridad (ACS) en FaceOps"),
                 "git": (git_publico("http://192.168.122.1:3000/"), "repositorios en Git"),
                 "registro": (git_publico("http://192.168.122.1:3000/invoke/-/packages"), "paquetes publicados en Git")})
    return base


def _catalogo_etapas():
    from . import devsecops as dso
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "devsecops", "catalogo", "etapas.yaml")
    return dso.catalogo_etapas(open(base).read() if os.path.exists(base) else None)


@bp.route("/seguridad/escaneo", methods=["GET", "POST"])
@requiere_login
def escaneo_seguridad():
    """Escaneo de seguridad de toda la infraestructura, por dimensiones (fase 1: servidores)."""
    from . import escaneo as esc
    if request.method == "POST":
        _escritura()
        try:
            ts = esc.solicitar(g.usuario, g.cliente, equipos_visibles())
            flash(f"Escaneo pedido: {', '.join(t['numero'] for t in ts)} (uno por inventario de AAP), cada uno con su aprobación.", "ok")
        except (ValueError, PermissionError, ErrorAAP) as x:
            flash(str(x), "error")
        return redirect(url_for("consola.escaneo_seguridad"))
    on, off = esc.objetivos(equipos_visibles())
    v = esc.vista(g.cliente["id"])
    eq_ids = {e["nombre"]: e["id"] for e in equipos_visibles()}
    return render_template("seguridad_escaneo.html", v=v, dims=esc.DIMENSIONES, sev_txt=esc.SEV_TXT, on=on, off=off, eq_ids=eq_ids)


@bp.route("/devsecops/catalogo")
@requiere_login
def devsecops_catalogo():
    """Catálogo global de etapas DevSecOps: la definición (en Git) desde la que se crean los pipelines."""
    from . import devsecops as dso
    try:
        cat, error = _catalogo_etapas(), ""
    except (requests.RequestException, ValueError) as x:
        cat, error = {"runtimes": {}, "etapas": []}, f"No se pudo leer el catálogo en Git: {x}"
    return render_template("devsecops_catalogo.html", cat=cat, error=error,
                           url_git=_origen_links({"repo": f"http://192.168.122.1:3000/{dso.DUENO}/{dso.REPO}.git", "rama": "main"})["web"] + f"/src/branch/main/{dso.CATALOGO_RUTA}")


@bp.route("/devsecops/h/<int:hid>/nuevo", methods=["GET", "POST"])
@requiere_login
def devsecops_nuevo(hid):
    """Asistente para crear un pipeline desde el catálogo global: nombre, repositorio, runtime, etapas y vista previa."""
    from . import devsecops as dso
    h = q1("SELECT * FROM devsecops_herramientas WHERE id=? AND cliente_id=?", hid, g.cliente["id"])
    if not h or h["tipo"] != "tekton":
        abort(404)
    cat = _catalogo_etapas()
    if request.method == "POST":
        _escritura()
        f = request.form
        nombre = (f.get("nombre") or "").strip().lower()
        try:
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", nombre):
                raise ValueError("Nombre del pipeline: minúsculas, números y guiones")
            if dso.leer(nombre)[0]:
                raise ValueError(f"Ya existe el pipeline {nombre}")
            rt = f.get("runtime", "")
            if rt not in cat["runtimes"]:
                raise ValueError("Elige el runtime")
            repo = (f.get("repo") or "").strip()
            if not re.fullmatch(dso.RE_REPO, repo):
                raise ValueError("Repositorio: http://…:3000/organización/repo.git")
            texto = dso.generar_pipeline(nombre, (f.get("desc") or "").strip()[:120], rt, set(f.getlist("etapa")), repo, cat)
            dso.guardar(nombre, texto, g.usuario["usuario"], f"pipeline nuevo desde el catálogo ({cat['runtimes'][rt]})")
            auditar(g.usuario["usuario"], g.cliente["id"], "crear pipeline", nombre, {"runtime": rt, "etapas": sorted(f.getlist("etapa"))})
            flash(f"Pipeline {nombre} creado en Git desde el catálogo de etapas.", "ok")
            return redirect(url_for("consola.devsecops_pipeline", nombre=nombre))
        except (ValueError, requests.RequestException) as x:
            flash(str(x), "error")
    return render_template("devsecops_nuevo.html", herramienta=h, cat=cat, form=request.form)


@bp.route("/devsecops/h/<int:hid>", methods=["GET", "POST"])
@requiere_login
def devsecops_herramienta(hid):
    """Pipelines Tekton del cliente (desde Git): cajas con sus etapas; crear uno nuevo desde plantilla."""
    from . import devsecops as dso
    dso.herramientas(g.cliente["id"])
    h = q1("SELECT * FROM devsecops_herramientas WHERE id=? AND cliente_id=?", hid, g.cliente["id"])
    if not h:
        abort(404)
    t_ = dso.TIPOS.get(h["tipo"], {})
    h = dict(h, tipo_nombre=t_.get("nombre", h["tipo"]), detalle=t_.get("detalle", ""), color=t_.get("color", "#6a6e73"))
    if h["tipo"] != "tekton":   # las demás: ficha de la herramienta (sus pipelines aparecen cuando FaceOps la conecte)
        e = next((x for x in equipos_visibles() if x["nombre"] == h.get("equipo")), None)
        return render_template("devsecops.html", herramienta=h, cajas=[], error="", repo="", equipo_h=e, usos=dso.USOS, usos_amb=dso.AMBIENTES)
    if request.method == "POST":
        _escritura()
        nombre = (request.form.get("nombre") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", nombre):
            flash("Nombre del pipeline: minúsculas, números y guiones", "error")
            return redirect(url_for("consola.devsecops_herramienta", hid=hid))
        if dso.leer(nombre)[0]:
            flash(f"Ya existe el pipeline {nombre}", "error")
            return redirect(url_for("consola.devsecops_pipeline", nombre=nombre))
        try:
            dso.guardar(nombre, dso.PLANTILLA.format(nombre=nombre, desc=(request.form.get("desc") or "Pipeline DevSecOps").replace(":", " ")[:120]),
                        g.usuario["usuario"], "pipeline nuevo")
            auditar(g.usuario["usuario"], g.cliente["id"], "crear pipeline", nombre)
            flash(f"Pipeline {nombre} creado en Git. Súmale etapas y aplícalo.", "ok")
        except (ValueError, requests.RequestException) as x:
            flash(str(x), "error")
        return redirect(url_for("consola.devsecops_pipeline", nombre=nombre))
    error, cajas = "", []
    eqs_ = equipos_visibles()
    try:
        dso.asegurar_repo(_dso_semilla())
        for n in dso.listar():
            texto, _ = dso.leer(n)
            p, err = dso.validar(texto or "")
            cols = dso.etapas(p) if p and not err else []
            cats = Counter(x["categoria"] for c in cols for x in c)
            hist = dso.historial(n, 1)
            o_ = dict(dso.origen(p), ambiente=dso.ultimo_ambiente(g.cliente["id"], n))   # el ambiente de su última ejecución
            fz = dso.fases(cols)
            cajas.append({"nombre": n, "etapas": sum(len(c) for c in cols), "cols": cols, "origen": _origen_links(o_), "runtime": dso.runtime(n, dso.plataforma(o_)),
                          "destinos": (lambda d_: {"otro": next((x["texto"] for x in d_.values() if x["tipo"] != "maquinas"), ""), "por": d_})({a_: dso.destino(n, a_, eqs_, p, dso.plataforma(o_)) for a_ in dso.AMBIENTES}), "n_ci": sum(len(c) for c in fz["ci"]), "n_cd": sum(len(c) for c in fz["cd"]), "seguridad": cats.get("seguridad", 0),
                          "automatizacion": cats.get("automatización", 0), "error": "; ".join(err), "ultimo": hist[0] if hist else None})
    except (ValueError, requests.RequestException) as x:
        error = f"No se pudo leer el repositorio de pipelines: {x}"
    return render_template("devsecops.html", herramienta=h, cajas=cajas, error=error, repo=f"{dso.DUENO}/{dso.REPO}", usos=dso.USOS, usos_amb=dso.AMBIENTES)


@bp.route("/devsecops/<nombre>/avance")
@requiere_login
def devsecops_avance(nombre):
    """JSON: avance en vivo de la ejecución en curso (la lanzada por FaceOps con su ticket) y estado de su ticket."""
    from . import devsecops as dso
    cor = request.args.get("corrida", "")
    if cor.startswith("ticket-"):   # ejecución recién pedida: aún sin PipelineRun registrado, se sigue por su ticket
        c = {"ticket_id": int(cor[7:] or 0) if cor[7:].isdigit() else 0, "estado": ""}
    else:
        c = q1("SELECT * FROM devsecops_corridas WHERE cliente_id=? AND pipeline=? AND nombre=?", g.cliente["id"], nombre, cor)
    if not c:
        abort(404)
    t = q1("SELECT * FROM tickets WHERE id=? AND cliente_id=?", c["ticket_id"], g.cliente["id"]) if c.get("ticket_id") else None
    return {"etapas": dso.avance(g.cliente, t) or {}, "ticket": (t or {}).get("estado", ""), "estado": c.get("estado") or ""}


@bp.route("/devsecops/<nombre>", methods=["GET", "POST"])
@requiere_login
def devsecops_pipeline(nombre):
    """Un pipeline: diagrama de sus etapas, su YAML (editar = commit en Git), etapas DevSecOps para sumar y pedir a AAP
    aplicarlo o ejecutarlo (ticket con aprobación)."""
    from . import devsecops as dso
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", nombre):
        abort(404)
    texto, _ = dso.leer(nombre)
    if texto is None:
        abort(404)
    if request.method == "POST":
        _escritura()
        acc = request.form.get("accion", "")
        try:
            if acc == "guardar":
                nuevo = request.form.get("yaml", "")
                p, err = dso.validar(nuevo)
                if err:
                    raise ValueError("; ".join(err))
                if p["metadata"]["name"] != nombre:
                    raise ValueError(f"metadata.name debe seguir siendo {nombre}")
                dso.guardar(nombre, nuevo, g.usuario["usuario"], (request.form.get("motivo") or "cambio desde FaceOps")[:120])
                flash("Cambio guardado en Git (nueva versión). Para llevarlo al clúster usa «Aplicar en Tekton».", "ok")
            elif acc == "ajuste_agregar":
                aa = request.form.get("automatizacion", "")
                if aa and not q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], aa):
                    raise ValueError("Esa acción no está en el catálogo")
                dso.ajuste_agregar(g.cliente["id"], nombre, request.form.get("titulo", ""), aa)
                auditar(g.usuario["usuario"], g.cliente["id"], "ajuste operativo agregado", nombre, {"automatizacion": aa})
                flash("Acción sumada al ajuste operativo.", "ok")
                return redirect(url_for("consola.devsecops_pipeline", nombre=nombre) + "#ajuste")
            elif acc == "ajuste_quitar":
                dso.ajuste_quitar(g.cliente["id"], nombre, int(request.form.get("aid") or 0))
                auditar(g.usuario["usuario"], g.cliente["id"], "ajuste operativo quitado", nombre)
                flash("Acción quitada del ajuste operativo.", "ok")
                return redirect(url_for("consola.devsecops_pipeline", nombre=nombre) + "#ajuste")
            elif acc == "agregar":
                nuevo = dso.agregar_etapa(texto, request.form.get("etapa", ""), request.form.get("despues", ""))
                dso.guardar(nombre, nuevo, g.usuario["usuario"], f"suma la etapa {request.form.get('etapa')}")
                flash("Etapa sumada al pipeline (commit en Git).", "ok")
            elif acc in ("aplicar", "ejecutar", "estado"):
                from .operacion import solicitar
                a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], dso.AUTOMATIZACION)
                if not a:
                    raise ValueError(f"Falta «{dso.AUTOMATIZACION}» en el catálogo: sincroniza con AAP")
                params = {"dp_accion": acc, "dp_pipeline": nombre, "dp_url": dso.url_raw(nombre)}
                if acc == "ejecutar":   # repositorio y rama de los que se lanza esta ejecución
                    o = dso.origen(dso.validar(texto)[0])
                    lista_, mot_ = dso.ramas_gitflow(o)
                    rama_ = (request.form.get("rama") or "").strip()
                    if not any(x["rama"] == rama_ for x in lista_):
                        raise ValueError(f"Elige una rama de Gitflow del repositorio{': ' + mot_ if mot_ else ''}")
                    amb_sel = dso.ambiente_de_rama(rama_)   # Gitflow: la rama define el ambiente
                    repo_ = o["repo"]
                    if not re.fullmatch(dso.RE_REPO, repo_) or not re.fullmatch(dso.RE_RAMA, rama_) or ".." in rama_:
                        raise ValueError("Repositorio (http://…:3000/organización/repo.git) o rama no válidos")
                    params["dp_params"] = {"repo-url": repo_, "revision": rama_} if o["tiene_rama"] else {"repo-url": repo_}
                    if o["tiene_ambiente"]:
                        amb_ = amb_sel
                        params["dp_params"]["ambiente"] = amb_
                        dz = dso.destino(nombre, amb_, equipos_visibles(), dso.validar(texto)[0], dso.plataforma(o, rama_))
                        if dz["tipo"] == "maquinas":
                            if not dz["maquinas"]:
                                raise ValueError(f"No hay máquinas de destino en {dso.AMBIENTES[amb_]} para este pipeline")
                            if "maquinas" in {x.get("name") for x in (dso.validar(texto)[0].get("spec") or {}).get("params") or []}:
                                params["dp_params"]["maquinas"] = ",".join(m["nombre"] for m in dz["maquinas"])
                        es_ = dso.estrategia(g.cliente["id"], nombre, amb_, dso.plataforma(o, rama_))   # blue-green / canary / A/B
                        if es_ and not es_.get("pendiente"):
                            if es_.get("error"):
                                raise ValueError(es_["error"])
                            nombres_ps = {x.get("name") for x in (dso.validar(texto)[0].get("spec") or {}).get("params") or []}
                            if "trafico-destino" in nombres_ps:   # la versión nueva va al grupo inactivo; la etapa «Cambiar tráfico» lo activa
                                params["dp_params"]["maquinas"] = ",".join(es_["maquinas"])
                                params["dp_params"]["trafico-destino"] = es_["nuevo"]
                t = solicitar(g.usuario, g.cliente, a, "", params,
                              {"aplicar": "Aplicar", "ejecutar": "Ejecutar", "estado": "Leer ejecuciones de"}[acc] + f" el pipeline {nombre} en Tekton"
                              + (f" · {re.sub(r'^https?://[^/]+/|[.]git$', '', params['dp_params']['repo-url'])} @ {params['dp_params'].get('revision', 'main')}" if params.get("dp_params") else "")
                              + (f" → {dso.AMBIENTES[params['dp_params']['ambiente']]}" if (params.get("dp_params") or {}).get("ambiente") else ""))
                flash(f"Ticket {t['numero']} creado: queda esperando su aprobación.", "ok")
                if acc == "ejecutar":   # se queda en el pipeline, con la nueva ejecución marcada y seguida en vivo
                    return redirect(url_for("consola.devsecops_pipeline", nombre=nombre, ticket=t["id"]) + "#ejecuciones")
                return redirect(url_for("consola.ticket", tid=t["id"]))
        except (ValueError, ErrorAAP, PermissionError, requests.RequestException) as x:
            flash(str(x), "error")
        return redirect(url_for("consola.devsecops_pipeline", nombre=nombre))
    p, err = dso.validar(texto)
    tks = q("SELECT t.id, t.numero, t.estado, t.creado, t.motivo FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
            "WHERE t.cliente_id=? AND a.nombre_aap=? AND t.motivo LIKE ? ORDER BY t.id DESC LIMIT 8", g.cliente["id"], dso.AUTOMATIZACION, f"%pipeline {nombre} %")
    cols_ = dso.etapas(p) if p and not err else []
    return render_template("devsecops_pipeline.html", nombre=nombre, texto=texto, cols=cols_, fases=dso.fases(cols_), err=err, herramienta=q1("SELECT * FROM devsecops_herramientas WHERE cliente_id=? AND tipo='tekton' ORDER BY id LIMIT 1", g.cliente["id"]), origen=_origen_links(dict(dso.origen(p), ambiente=dso.ultimo_ambiente(g.cliente["id"], nombre))), durs=dso.duraciones(g.cliente["id"], nombre), ajustes=dso.ajustes(g.cliente["id"], nombre, dso.plataforma(dso.origen(p)) if p and not err else None), variables_aj=dso.VARIABLES,
                           por_corrida=dso.etapas_por_corrida(g.cliente["id"], nombre),
                           tec_url=_tec_urls(),
                           segmento=(dso.plataforma(dso.origen(p)) if p and not err else {}).get("segmento", ""),
                           dest_todos=",".join(sorted({m["nombre"] for a_ in dso.AMBIENTES for m in dso.destino(nombre, a_, equipos_visibles(), p, dso.plataforma(dso.origen(p))).get("maquinas", [])})) if p and not err else "",
                           pendientes=[dict(t_, p=j(t_["parametros"], {}).get("dp_params") or {}) for t_ in q(
                               "SELECT t.id, t.numero, t.estado, t.creado, t.parametros FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
                               "WHERE t.cliente_id=? AND a.nombre_aap=? AND t.motivo LIKE ? AND t.estado IN ('pendiente_aprobacion','en_cola','en_ejecucion') "
                               "AND t.id NOT IN (SELECT COALESCE(ticket_id,0) FROM devsecops_corridas WHERE cliente_id=?) ORDER BY t.id DESC",
                               g.cliente["id"], dso.AUTOMATIZACION, f"Ejecutar el pipeline {nombre} %", g.cliente["id"])],
                           destinos={a_: dso.destino(nombre, a_, equipos_visibles(), p, dso.plataforma(dso.origen(p))) for a_ in dso.AMBIENTES},
                           estrategias={a_: dso.estrategia(g.cliente["id"], nombre, a_, dso.plataforma(dso.origen(p))) for a_ in dso.AMBIENTES} if p and not err else {},
                           ramas_gf=dso.ramas_gitflow(dso.origen(p)) if p and not err else ([], ""),
                           etapas_det={e["nombre"]: dict(e, fase=dso.fase(e)) for c_ in cols_ for e in c_},
                           autos_cat=q("SELECT nombre, nombre_aap FROM automatizaciones WHERE cliente_id=? AND visible=1 ORDER BY nombre", g.cliente["id"]), ambientes=dso.AMBIENTES,
                           catalogo=dso.CATALOGO, historial=[dict(h, url=git_publico(h.get("url") or "")) for h in dso.historial(nombre)], tickets=tks, corridas=dso.corridas(g.cliente["id"], nombre), url_git=f"http://192.168.122.1:3000/{dso.DUENO}/{dso.REPO}")


@bp.route("/riesgo/estado")
@requiere_login
def riesgo_estado():
    """Indicador central de la cabecera: ¿hay riesgo crítico ahora (segmentos críticos caídos, seguridad, hallazgos)?"""
    from .riesgo_global import calcular
    try:
        return calcular(g.cliente, g.usuario, equipos_visibles(), url_for)
    except Exception as x:  # noqa: BLE001
        return {"estado": "desconocido", "items": [{"nivel": "info", "texto": f"No se pudo calcular: {str(x)[:120]}", "url": ""}], "n": 0}


@bp.route("/segmentos/archivar", methods=["POST"])
@requiere_login
def segmento_archivar():
    """Archiva un segmento de negocio: sale de las listas y libera sus máquinas (no elimina máquinas virtuales)."""
    from .segmentos_archivo import archivar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    nombre = request.form.get("segmento", "")
    if not puede_ver_segmento(g.usuario, g.cliente["id"], nombre):
        abort(404)
    eqs = [e["nombre"] for e in equipos_visibles() if nombre in e["segmentos"]]
    try:
        archivar(g.usuario, g.cliente, nombre, request.form.get("motivo", ""), eqs)
        flash(f"Segmento «{nombre}» archivado: {len(eqs)} máquina(s) liberada(s), sin eliminar ninguna.", "ok")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(request.referrer or url_for("consola.mapa"))
    return redirect(url_for("consola.mapa") + "#archivados")


@bp.route("/segmentos/archivados/<int:aid>/restaurar", methods=["POST"])
@requiere_login
def segmento_restaurar(aid):
    from .segmentos_archivo import restaurar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    try:
        flash(f"Segmento «{restaurar(g.usuario, g.cliente, aid)}» restaurado.", "ok")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("consola.mapa") + "#archivados")


# ------------------------------------------------------------------ mapa del ecosistema
@bp.route("/mapa")
@requiere_login
def mapa():
    """Mapa vivo: infraestructura → redes (o segmentos de negocio) → equipos, con los flujos de red y el estado de cada uno."""
    vista = request.args.get("vista", "aplicaciones")
    segmento = request.args.get("segmento", "")
    tarjetas, texto, entendido = [], request.args.get("q", "").strip(), {}
    if vista == "aplicaciones" and not segmento:
        from .topologia import resumen_segmentos
        eqs = equipos_visibles()
        hs = hallazgos_visibles(eqs)
        from .grupos_segmento import grupo_de
        grupo = request.args.get("grupo", "")
        tarjetas = resumen_segmentos(g.cliente, eqs, hs, [x for x in segmentos_visibles(g.usuario, g.cliente["id"]) if not grupo or grupo_de(x) == grupo])
        if texto:
            tarjetas, entendido = buscar_segmentos(texto, tarjetas, eqs, hs)
            if len(tarjetas) == 1 and request.args.get("ir") == "1":
                return redirect(url_for("consola.mapa", vista="aplicaciones", segmento=tarjetas[0]["nombre"]))
    seg_info = None
    if segmento:
        from .arquitectura import vigente
        seg_info = q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre=?", g.cliente["id"], segmento)
        if seg_info:
            seg_info["ficha_d"] = j(seg_info.get("ficha"), {})
            arq = vigente(g.cliente["id"], segmento)
            seg_info["arq"] = {"version": arq["version"], "entrada": arq["m"].get("entrada") or {}} if arq else None
            # ambientes del segmento (pestañas): los de su arquitectura o los de sus equipos; primero producción
            orden = {"produccion": 0, "qa": 1, "desarrollo": 2}
            ambs = arq["m"]["ambientes"] if arq else sorted({e["ambiente"] for e in equipos_visibles() if segmento in e["segmentos"] and e.get("ambiente")})
            seg_info["ambientes"] = sorted(ambs, key=lambda a: (orden.get(a, 9), a)) or ["produccion"]
            seg_info["ambiente"] = request.args.get("ambiente") if request.args.get("ambiente") in seg_info["ambientes"] else seg_info["ambientes"][0]
            if request.args.get("ambiente") == "homologacion":
                seg_info["ambiente"] = "homologacion"
                seg_info["homolog"] = _homologar_segmento(segmento, arq)
                from .topologia import resumen_segmento as _rs
                eqs_h = equipos_visibles(); hs_h = hallazgos_visibles(eqs_h); ambs_h = []
                for a_, n_ in (("produccion", "Producción"), ("qa", "QA"), ("desarrollo", "Desarrollo")):
                    r_ = _rs(g.cliente, eqs_h, hs_h, segmento, a_)
                    if r_["equipos"] or (a_ == "produccion" and r_.get("ocp")):
                        ambs_h.append({"amb": a_, "etq": n_, "pct": r_["salud_pct"]})
                seg_info["salud_media"] = {"pct": round(sum(x["pct"] for x in ambs_h) / len(ambs_h)) if ambs_h else None, "ambs": ambs_h}
    from .grupos_segmento import GRUPOS, NOMBRE, grupo_de
    grupo = request.args.get("grupo") or (grupo_de(seg_info) if seg_info else ("redes" if vista == "infra" else ""))
    from .segmentos_archivo import lista as archivados
    areas = sorted({x["area"] for x in q("SELECT area FROM segmentos WHERE cliente_id=? AND COALESCE(area,'')<>''", g.cliente["id"])})
    accesos = _accesos_apps(tarjetas, solo=None) if vista == "aplicaciones" and not segmento else []
    acceso_app = None
    if seg_info:
        try:
            acceso_app = next(iter(_accesos_apps([{"nombre": segmento, "ambientes": seg_info.get("ambientes") or []}])), None)
        except Exception as e_:  # noqa: BLE001
            current_app.logger.warning("acceso de la aplicación %s: %s", segmento, e_)
    tickets_seg = []
    if seg_info and segmento:   # actividad lanzada sobre el segmento o sus máquinas (encendidos, réplicas, certificados…)
        maq_ = {e["nombre"] for e in equipos_visibles() if segmento in (e.get("segmentos") or [])}
        for t_ in q("SELECT t.id, t.numero, t.estado, t.creado, t.motivo, t.limite, t.segmento, a.nombre AS accion FROM tickets t "
                    "LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? ORDER BY t.id DESC LIMIT 600", g.cliente["id"]):
            en_ = set(x for x in re.split(r"[:,\s]+", t_["limite"] or "") if x) & maq_
            if t_["segmento"] == segmento or en_:
                t_["maquinas"] = sorted(en_)
                tickets_seg.append(t_)
            if len(tickets_seg) >= 40:
                break
    radar_sec = None
    if vista == "aplicaciones" and not segmento:
        noms_ = {t["nombre"] for t in tarjetas}
        radar_sec = _radar_de([e for e in equipos_visibles() if noms_ & set(e["segmentos"])])
    return render_template("mapa.html", vista=vista, segmento=segmento, tarjetas=tarjetas, texto=texto, ent=entendido, seg_info=seg_info, accesos=accesos, ambs_acc=sorted({k for a in accesos for k in a["por_amb"]}, key=lambda k: (AMB_ORDEN.index(k) if k in AMB_ORDEN else 99, k)), radar_sec=radar_sec, acceso_app=acceso_app,
                           grupo=grupo if grupo in NOMBRE else "", grupos=GRUPOS, nombre_grupo=NOMBRE, areas=areas,
                           archivados=archivados(g.cliente["id"]) if vista == "aplicaciones" and not segmento else [], tickets_seg=tickets_seg)


def _homologar_segmento(segmento, arq):
    """Homologación de los ambientes del segmento: por componente, las máquinas de cada ambiente lado a lado con lo que
    importa (cantidad/HA, sistema, versión del producto, cómputo, parches…) comparado contra producción. Cada diferencia
    dice por qué y con qué se remedia."""
    from .arquitectura import asignacion
    from .topologia import rol_de
    eqs = [e for e in equipos_visibles() if segmento in e["segmentos"]]
    AMBS = [("desarrollo", "Desarrollo"), ("qa", "QA"), ("produccion", "Producción")]
    comps = {}
    asignadas = set()
    if arq:   # los componentes de la arquitectura: máquinas y también lo que corre en OpenShift
        from .segmentos_ocp import de_segmento
        ocp_amb = {o["ambiente"]: o for o in de_segmento(g.cliente["id"], segmento)}
        for cid, por_amb in asignacion(arq["m"], eqs).items():
            c = next((x for x in arq["m"]["componentes"] if x["id"] == cid), {})
            if c.get("plataforma") == "openshift":
                comps[(c.get("nombre") or cid) + " · OpenShift"] = {
                    a: ([{"id": None, "nombre": f"namespace {ocp_amb[a]['namespace']}", "so": "OpenShift (contenedores)", "datos": "{}", "skills": [c.get("producto") or cid],
                          "cpu": (c.get("recursos") or {}).get("cpu"), "ram_mb": int(float((c.get("recursos") or {}).get("ram_gb") or 0) * 1024) or None}]
                        if a in ocp_amb else []) for a, _ in AMBS}
                continue
            comps[c.get("nombre") or cid] = {a: por_amb.get(a, []) for a, _ in AMBS}
            asignadas |= {x["nombre"] for xs in por_amb.values() for x in xs}
    for e in eqs:   # el resto de las máquinas del segmento (sin componente en la arquitectura), por su rol
        if e["nombre"] in asignadas:
            continue
        _, rol = rol_de(e)
        clave = rol if not arq else f"{rol} (fuera de la arquitectura)"
        comps.setdefault(clave, {a: [] for a, _ in AMBS})
        if e.get("ambiente") in comps[clave]:
            comps[clave][e["ambiente"]].append(e)
        elif not e.get("ambiente"):
            comps[clave]["produccion"].append(e)

    def auto(n):
        a = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=? AND visible=1", g.cliente["id"], n)
        return url_for("consola.automatizacion", aid=a["id"]) if a else ""
    SUG = {"cantidad": "Igualar la cantidad de réplicas (alta disponibilidad) o agregar el componente que falta en ese ambiente",
           "so": "Llevar el sistema operativo a la misma versión que producción", "kernel": "Aplicar los mismos parches de kernel que producción",
           "producto": "Alinear la versión del producto con la de producción antes del próximo paso a producción",
           "cpu": "Revisar el dimensionamiento: un ambiente bajo no debería tener más vCPU que producción",
           "ram": "Revisar el dimensionamiento: un ambiente bajo no debería tener más memoria que producción",
           "parches": "Aplicar los parches de seguridad pendientes", "selinux": "Dejar SELinux igual que en producción (política de seguridad base)"}

    def val(es, k):
        if not es:
            return None
        vs = []
        for e in es:
            d = j(e.get("datos"), {})
            if k == "cantidad":
                return len(es)
            v = {"so": e.get("so") or "", "kernel": d.get("kernel") or "", "cpu": e.get("cpu"), "ram": round((e.get("ram_mb") or 0) / 1024) or None,
                 "disco": round(sum((x.get("gb") or 0) for x in j(e.get("discos"), []) if isinstance(x, dict))) or None,
                 "parches": d.get("parches_seguridad"), "selinux": d.get("selinux") or "",
                 "producto": ", ".join(sorted({f"{x.get('familia', '')} {x.get('version', '')}".strip() for x in d.get("jboss") or []})) or ", ".join(sorted(e["skills"]))}[k]
            vs.append("" if v is None else str(v))
        u = sorted(set(vs))
        return u[0] if len(u) == 1 else " / ".join(u)
    ATR = [("cantidad", "Máquinas (alta disponibilidad)", "tamaño", "Máquinas"), ("so", "Sistema operativo", "igual", "Sistema"), ("kernel", "Kernel", "igual", "Sistema"),
           ("producto", "Producto y versión", "igual", "Producto"), ("cpu", "vCPU", "tamaño", "Recursos"), ("ram", "Memoria (GB)", "tamaño", "Recursos"),
           ("disco", "Disco (GB)", "tamaño", "Recursos"), ("parches", "Parches de seguridad pendientes", "igual", "Parches y seguridad"),
           ("selinux", "SELinux", "igual", "Parches y seguridad")]
    filas, tot, iguales = [], {a: 0 for a, _ in AMBS}, {a: 0 for a, _ in AMBS}
    for cnom, por_amb in comps.items():
        ref = "produccion" if por_amb.get("produccion") else next((a for a, _ in reversed(AMBS) if por_amb.get(a)), None)
        for k, etq, pol, grupo in ATR:
            celdas = {}
            for a, _ in AMBS:
                v = val(por_amb.get(a), k)
                rv = val(por_amb.get(ref), k) if ref else None
                if v is None:
                    est, por = "falta", "no hay máquina de este componente en el ambiente"
                elif a == ref:
                    est, por = "ref", "referencia"
                elif str(v) == str(rv):
                    est, por = "igual", ""
                elif pol == "tamaño":
                    try:
                        menor = float(str(v).split(" /")[0]) < float(str(rv).split(" /")[0])
                    except ValueError:
                        menor = False
                    est, por = ("menor", f"menor que producción ({v} vs {rv}): esperado fuera de producción") if menor else ("distinto", f"{v} vs {rv} en producción")
                else:
                    est, por = "distinto", f"{v} vs {rv} en producción"
                if est != "falta":
                    tot[a] += 1
                    iguales[a] += est in ("igual", "ref", "menor")
                celdas[a] = {"v": "—" if v is None else v, "est": est, "por": por,
                             "nodos": [{"n": e["nombre"], "u": url_for("consola.equipo", eid=e["id"]) if e.get("id") else ""} for e in por_amb.get(a) or []] if k == "cantidad" else []}
            mal = [c for c in celdas.values() if c["est"] in ("distinto", "falta")]
            filas.append({"comp": cnom, "atr": etq, "grupo": grupo, "celdas": celdas, "sug": SUG.get(k) if mal else "",
                          "url_comp": url_for("consola.homologacion_componente", segmento=segmento, comp=cnom)})
    # producción es la base (100 % por definición): el % de los otros ambientes es cuánto se parecen a producción, no su salud
    cajas = [{"amb": a, "etq": n, "pct": (100 if tot[a] else None) if a == "produccion" else (round(100 * iguales[a] / tot[a]) if tot[a] else None), "maquinas": sum(len(c.get(a, [])) for c in comps.values()),
              "difs": sum(1 for f in filas if f["celdas"][a]["est"] in ("distinto", "falta"))} for a, n in AMBS]
    return {"ambientes": AMBS, "cajas": cajas, "filas": filas, "relevar": auto("Homologación · Relevar entorno"),
            "detalle": url_for("consola.homologacion") if "consola.homologacion" in current_app.view_functions else ""}


AMB_ORDEN = ["produccion", "contingencia", "calidad", "pruebas", "desarrollo"]


def _corta(t, n=140):
    """Primera oración de una descripción, para mostrarla en una lista."""
    t = " ".join((t or "").split())
    p = t.split(". ")[0].rstrip(".")
    return (p[:n - 1] + "…") if len(p) > n else p


def _recursos_amb(mios):
    """Recursos que ocupa un segmento en cada ambiente: máquinas, vCPU, memoria y disco."""
    out = {}
    for e in mios:
        a = e.get("ambiente") or "sin ambiente"
        x = out.setdefault(a, {"n": 0, "cpu": 0, "ram_gb": 0.0, "disco_gb": 0})
        x["n"] += 1
        x["cpu"] += int(e.get("cpu") or 0)
        x["ram_gb"] += (e.get("ram_mb") or 0) / 1024
        x["disco_gb"] += sum(d.get("gb") or 0 for d in (j(e.get("discos"), []) if isinstance(e.get("discos"), str) else (e.get("discos") or [])))
    for x in out.values():
        x["ram_gb"], x["disco_gb"] = round(x["ram_gb"], 1), round(x["disco_gb"])
    return out


def _accesos_apps(tarjetas, solo="aplicaciones"):
    """Cada aplicación con su plataforma y sus URL de acceso: la URL pública de su arquitectura, las rutas de OpenShift y
    la interfaz web de sus máquinas de entrada (vía FaceOps), para entrar a cada caso desde un solo lugar."""
    from urllib.parse import urlsplit
    from .arquitectura import vigente
    from .consola import web_de
    from .grupos_segmento import grupo_de
    from .segmentos_ocp import de_segmento
    from .topologia import resumen_segmento
    eqs = equipos_visibles()
    hs_ = hallazgos_visibles(eqs)
    out = []
    for t in tarjetas:
        seg = q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre=?", g.cliente["id"], t["nombre"])
        if not seg or (solo and grupo_de(seg) != solo):
            continue
        arq = vigente(g.cliente["id"], t["nombre"])
        m = arq["m"] if arq else {}
        urls, vistos = [], set()

        def agregar(etq, u):
            if u and u not in vistos:
                vistos.add(u)
                urls.append({"t": etq, "u": u})
        en = (m.get("entrada") or {}).get("url") or ""
        if en.startswith("http"):
            p_ = urlsplit(en)
            agregar("Aplicación", f"{p_.scheme}://{p_.netloc}/")
        for o in de_segmento(g.cliente["id"], t["nombre"]):
            for k, u in (o["rutas_d"] or {}).items():
                agregar(k.replace("_", " ").capitalize(), u)
        mios = [e for e in eqs if t["nombre"] in e["segmentos"]]
        for e in sorted(mios, key=lambda e: (e.get("ambiente") != "produccion", e["nombre"])):
            w = web_de(e)
            if w and (e.get("categoria") or "servidor") == "servidor" and len(urls) < 6:
                agregar(f"{w['producto']} · {e['nombre']}", url_for("consola.web_equipo", eid=e["id"], sub=""))
        comps = m.get("componentes") or []
        ocp = [c for c in comps if c.get("plataforma") == "openshift"]
        ubic = sorted({(e.get("ubicacion") or "").split(" · ")[-1] for e in mios if e.get("ubicacion")} - {""})
        nube = any("gcp" in (e.get("proveedor") or "").lower() or "cloud" in (e.get("ubicacion") or "").lower() for e in mios)
        if comps and len(ocp) == len(comps):
            plat = "OpenShift · " + ", ".join(sorted({x for c in ocp for x in c.get("equipos") or []}) or ["clúster"])
        else:
            plat = " + ".join(x for x in (("OpenShift" if ocp else ""), ("Nube GCP" if nube else ""),
                                          ("Máquinas virtuales · " + ", ".join(ubic) if ubic and not nube else "")) if x) or "Sin máquinas asignadas"
        try:
            sal_ = resumen_segmento(g.cliente, eqs, hs_, t["nombre"], "produccion")
            salud_ = sal_["salud_pct"] if (sal_["equipos"] or sal_.get("ocp")) else None
        except Exception:  # noqa: BLE001
            salud_ = None
        cred_ = _cred_app(t["nombre"])
        on_ = sum(1 for e in mios if e.get("energia") == "encendido")
        estado_ = "sin máquinas" if not mios else ("encendido" if on_ == len(mios) else ("apagado" if not on_ else "parcial"))
        out.append({"id": seg["id"], "codigo": f"APP-{seg['id']:03d}", "nombre": t["nombre"], "plataforma": plat, "urls": urls,
                    "ambientes": t.get("ambientes") or [], "salud": salud_, "radar": _radar_de(mios),
                    "estado": estado_, "on": on_, "n": len(mios),
                    "grupo": __import__("app.grupos_segmento", fromlist=["NOMBRE"]).NOMBRE.get(grupo_de(seg), ""),
                    "descripcion": _corta(seg.get("descripcion") or m.get("descripcion") or t.get("descripcion") or ""),
                    "por_amb": _recursos_amb(mios),
                    "cpu": sum(int(e.get("cpu") or 0) for e in mios),
                    "ram_gb": round(sum(e.get("ram_mb") or 0 for e in mios) / 1024, 1),
                    "disco_gb": round(sum(sum(d.get("gb") or 0 for d in (j(e.get("discos"), []) if isinstance(e.get("discos"), str) else (e.get("discos") or []))) for e in mios)),
                    "entrar": url_for("consola.app_entrar", sid=seg["id"], sub="") if (_url_app(t["nombre"], m, mios)) else None,
                    "con_credencial": bool(cred_ and cred_.get("usuario")),
                    "ir": url_for("consola.mapa", vista="aplicaciones", segmento=t["nombre"])})
    return out


def _cred_app(segmento):
    """Credencial de la aplicación en la bóveda central: faceops/aplicaciones/<segmento>
    {url, usuario, clave, metodo: form|basic|ninguno, login: ruta del formulario, campo_usuario, campo_clave}."""
    from .boveda import leer
    return leer(f"aplicaciones/{segmento}") or {}


def _url_app(segmento, m=None, mios=None):
    """URL interna de la aplicación: la de su credencial en la bóveda, la de entrada de su arquitectura o la web de su máquina de entrada."""
    from .consola import web_de
    u = (_cred_app(segmento).get("url") or "").strip()
    if u.startswith("http"):
        return u
    if m is None:
        from .arquitectura import vigente
        a_ = vigente(g.cliente["id"], segmento)
        m = a_["m"] if a_ else {}
    u = ((m.get("entrada") or {}).get("url") or "").strip()
    if u.startswith("http"):
        return u
    for e in sorted(mios if mios is not None else [e for e in equipos_visibles() if segmento in e["segmentos"]],
                    key=lambda e: (e.get("ambiente") != "produccion", e["nombre"])):
        w = web_de(e)
        if w and (e.get("categoria") or "servidor") == "servidor":
            return w["url"]
    return None


@bp.route("/aplicaciones/<int:sid>/entrar/", defaults={"sub": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@bp.route("/aplicaciones/<int:sid>/entrar/<path:sub>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@requiere_login
def app_entrar(sid, sub):
    """Entrar a la aplicación del segmento a través de FaceOps, autenticado con su credencial de la bóveda central
    (el usuario de FaceOps nunca ve la clave): basic = FaceOps la agrega a cada pedido; form = FaceOps hace el ingreso
    en el servidor y le pasa al navegador solo la sesión (cookies) que devolvió la aplicación."""
    from urllib.parse import urlsplit
    seg = q1("SELECT * FROM segmentos WHERE cliente_id=? AND id=?", g.cliente["id"], sid)
    if not seg or not puede_ver_segmento(g.usuario, g.cliente["id"], seg["nombre"]) or g.perfil["modo"] != "escritura":
        abort(404)
    url = _url_app(seg["nombre"])
    if not url:
        abort(404)
    cr = _cred_app(seg["nombre"])
    metodo = (cr.get("metodo") or ("basic" if cr.get("usuario") and not cr.get("login") else "form" if cr.get("usuario") else "ninguno")).lower()
    prefijo = url_for("consola.app_entrar", sid=sid, sub="").rstrip("/")
    w = {"url": url, "producto": "aplicación"}
    e = {"nombre": seg["nombre"]}
    if not sub and request.method == "GET":
        auditar(g.usuario["usuario"], g.cliente["id"], "entrar a aplicación", seg["nombre"],
                {"url": url, "credencial": "bóveda" if cr.get("usuario") else "sin credencial", "metodo": metodo})
        if metodo == "form" and cr.get("usuario") and cr.get("clave"):
            p_ = urlsplit(url)
            login = cr.get("login") or "/login"
            destino = f"{p_.scheme}://{p_.netloc}/{login.lstrip('/')}"
            try:
                r_ = requests.post(destino, timeout=10, verify=False, allow_redirects=False,
                                   data={cr.get("campo_usuario") or "username": cr["usuario"], cr.get("campo_clave") or "password": cr["clave"]})
                ok_ = r_.status_code < 400 and bool(r_.cookies)
            except requests.RequestException:
                r_, ok_ = None, False
            resp = redirect(prefijo + "/" + (p_.path.lstrip("/") if p_.path not in ("", "/") else ""))
            if ok_:
                for c in r_.cookies:
                    resp.set_cookie(c.name, c.value, path=prefijo + "/", httponly=True, secure=request.is_secure, samesite="Lax")
                auditar(g.usuario["usuario"], g.cliente["id"], "ingreso a aplicación con credencial de la bóveda", seg["nombre"], {"usuario_app": cr["usuario"]})
            else:   # sin aviso (flash): se mostraría después en otra página de FaceOps; queda en la auditoría
                auditar(g.usuario["usuario"], g.cliente["id"], "ingreso con credencial de la bóveda falló", seg["nombre"], {"login": login})
            return resp
    auth = (cr["usuario"], cr.get("clave") or "") if metodo == "basic" and cr.get("usuario") else None
    return _proxy_web(e, w, sub, prefijo, auth=auth)


def buscar_segmentos(texto, tarjetas, eqs, hs):
    """Buscador inteligente de segmentos de negocio: entiende lo mismo que el del inventario (skills, ambientes, sistemas,
    equipos, IPs, hallazgos, encendidos/apagados…) y además «flujos bloqueados» y «latencia». Devuelve los segmentos que
    tienen algún componente que cumple, marcando cuáles coinciden."""
    import re
    from .buscador import buscar_equipos
    from .inventario import normal
    t = normal(texto)
    bloq = bool(re.search(r"bloquead|cerrad|sin conexion|sin comunicacion|flujos? (rot|caid)", t))
    resto = re.sub(r"(flujos?|puertos?|red)? ?(bloquead\w*|cerrad\w*|sin conexion|sin comunicacion)|\bflujos?\b|\blatencia\b", " ", t).strip()
    # el nombre del segmento también cuenta (p. ej. «pagos», «portal»)
    por_nombre = [x for x in tarjetas if resto and all(p in normal(x["nombre"] + " " + x["descripcion"]) for p in resto.split()
                                                    if len(p) > 2)]
    por_eq = defaultdict(list)
    for h in hs:
        por_eq[h["equipo_id"]].append(h)
    if por_nombre:  # «pagos», «identidades»: el nombre del segmento manda
        return [dict(x, coinciden=[]) for x in por_nombre if not bloq or x["bloqueados"]], {"segmentos": [x["nombre"] for x in por_nombre], "bloqueados": bloq}
    entendido, cumplen = buscar_equipos(resto, g.cliente["id"], eqs, por_eq) if resto else ({}, eqs)
    ids = {e["id"] for e in cumplen}
    out = []
    for x in tarjetas:
        coinciden = [e["nombre"] for e in x["componentes"] if e["id"] in ids]
        if x not in por_nombre and not coinciden:
            continue
        if len(coinciden) == len(x["componentes"]):
            coinciden = []  # coincide el segmento completo: no hace falta marcar componentes
        if bloq and not x["bloqueados"]:
            continue
        out.append(dict(x, coinciden=coinciden))
    entendido = dict(entendido or {}, bloqueados=bloq)
    return out, entendido


@bp.route("/mapa/verificar", methods=["POST"])
@requiere_login
def mapa_verificar():
    from .topologia import verificar_flujos
    if g.perfil["modo"] != "escritura" or request.form.get("_csrf") != session.get("csrf"):
        abort(403)
    try:
        f = request.form
        solo = {(f["desde"], f["hacia"])} if f.get("desde") and f.get("hacia") else None
        n, jobs = verificar_flujos(g.cliente, equipos_visibles(), g.usuario["usuario"], solo=solo)
        return {"flujos": n, "trabajos": jobs}
    except ErrorAAP as e:
        return {"error": str(e)}, 400


@bp.route("/mapa/flujo", methods=["POST"])
@requiere_login
def mapa_flujo():
    """Abre o cierra un flujo de red desde el mapa: crea el ticket de «Acceso de red» de inmediato (aprobación en AAP)."""
    from .operacion import solicitar
    f = request.form
    if f.get("_csrf") != session.get("csrf") or f.get("accion") not in ("abrir", "bloquear"):
        abort(400)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='FaceOps · Acceso de red'", g.cliente["id"])
    if not a:
        return {"error": "Falta «Acceso de red» en el catálogo"}, 400
    params = {"red_accion": f["accion"], "origen": f.get("origen", ""), "destino": f.get("destino", ""), "puerto": f.get("puerto", "")}
    if not all(params.values()):
        return {"error": "Faltan origen, destino o puerto"}, 400
    try:
        t = solicitar(g.usuario, g.cliente, a, None, params,
                      f"Mapa: {'abrir' if f['accion'] == 'abrir' else 'cerrar'} {f.get('desde')} → {f.get('hacia')} ({params['puerto']})")
    except (PermissionError, ErrorAAP) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}


REPLICAR = "FaceOps · Replicar con alta disponibilidad"
HA_PRODUCTOS = {"jboss": 8080, "nginx": 80}   # productos sin estado que se replican y balancean tal cual


def _recursos(e):
    cpu = max(1, min(8, int(e.get("cpu") or 2)))
    ram = max(2, min(16, round((e.get("ram_mb") or 4096) / 1024)))
    disco = max(20, min(200, int(max([d.get("gb", 0) for d in j(e.get("discos"), [])] or [40]))))
    return {"cpu": cpu, "ram_gb": ram, "disco_gb": disco}


def datos_ha(e, els, eqs, segmento):
    """Lo que necesita el botón «Replicar con HA» de la caja: producto, balanceador actual o candidatos para habilitarlo."""
    prod = next((p for p in HA_PRODUCTOS if p in e["skills"]), None)
    nube = "cloud" in (e.get("ubicacion") or "").lower() or "gcp" in (e.get("proveedor") or "").lower()
    if not prod or not e.get("ip") or nube or "windows" in (e.get("so") or "").lower():
        return None
    por_id = {"e:" + x["nombre"]: x for x in eqs}
    bal = None
    for el in els:  # quién le llega: un equipo Nginx que le entrega tráfico es su balanceador
        d = el["data"]
        if d.get("target") == "e:" + e["nombre"] and d.get("source") in por_id and "nginx" in por_id[d["source"]]["skills"]:
            b = por_id[d["source"]]
            bal = {"nombre": b["nombre"], "ip": b["ip"]}
            break
    cand = [{"nombre": x["nombre"], "ip": x["ip"]} for x in eqs if "nginx" in x["skills"] and x["id"] != e["id"] and x.get("ip")
            and (x.get("ambiente") or "") == (e.get("ambiente") or "") and "cloud" not in (x.get("ubicacion") or "").lower()]
    if prod == "nginx" and bal is None:
        cand = [c for c in cand if c["nombre"] != e["nombre"]]
    return {"producto": prod, "puerto": HA_PRODUCTOS[prod], "balanceador": bal, "candidatos": cand, "recursos": _recursos(e),
            "segmento": segmento, "url": url_for("consola.mapa_replicar")}


@bp.route("/mapa/replicar", methods=["POST"])
@requiere_login
def mapa_replicar():
    """Replicar con alta disponibilidad desde la caja del mapa: un ticket con aprobación en AAP antes de crear nada."""
    from .arquitectura import asignacion, vigente
    from .operacion import solicitar
    f = request.form
    if f.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(400)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], REPLICAR)
    if not a:
        return {"error": "Falta «Replicar con alta disponibilidad» en el catálogo (sincroniza con AAP)"}, 400
    eqs = equipos_visibles()
    e = next((x for x in eqs if str(x["id"]) == f.get("eid")), None)
    if not e:
        return {"error": "Equipo no encontrado"}, 404
    segmento = f.get("segmento", "")
    from .topologia import construir
    els, _ = construir(g.cliente, eqs, hallazgos_visibles(eqs), "segmento", segmento, "negocio", e.get("ambiente") or "")
    ha = datos_ha(e, els, eqs, segmento)
    if not ha:
        return {"error": "Este equipo no se puede replicar con HA (solo JBoss o Nginx en Linux on-premise)"}, 400
    n = max(1, min(3, int(f.get("replicas") or 1)))
    nuevo = None
    if not ha["balanceador"]:
        nuevo = next((c for c in ha["candidatos"] if c["nombre"] == f.get("nuevo")), None)
        if not nuevo:
            return {"error": "Elige el equipo Nginx donde se habilita el balanceo"}, 400
    # nombre de las réplicas: srv-<codigo>-<componente>-NN, así quedan solas en su segmento y componente
    base, ruta = re.sub(r"[^a-z0-9-]", "-", e["nombre"].lower())[:14] + "-ha", "/"
    arq = vigente(g.cliente["id"], segmento) if segmento else None
    if arq:
        m = arq["m"]
        asg = asignacion(m, eqs)
        comp = next((cid for cid, por in asg.items() for xs in por.values() for x in xs if x["nombre"] == e["nombre"]), None)
        if comp:
            base = f"{m['codigo']}-{comp}"[:20]
        url_e = (m.get("entrada") or {}).get("url") or ""
        ruta = "/" + url_e.split("/", 3)[3] if url_e.count("/") >= 3 else "/"
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,19}", base):
        base = "ha-" + str(e["id"])
    ya = len([x for x in eqs if x["nombre"].startswith(f"srv-{base}-")])
    r = ha["recursos"]
    params = {"hab_interno": True, "hab_so": "rhel9", "hab_destino": "onpremise", "hab_nombre": base, "hab_replicas": str(min(5, ya + n)),
              "hab_cpu": str(r["cpu"]), "hab_ram_gb": str(r["ram_gb"]), "hab_disco_gb": str(r["disco_gb"]),
              "hab_ambiente": e.get("ambiente") or "desarrollo", "hab_segmento": segmento,
              "ha_origen": e["nombre"], "ha_origen_ip": e["ip"], "ha_producto": ha["producto"], "ha_puerto": ha["puerto"],
              "ha_balanceador": (ha["balanceador"] or {}).get("nombre", ""), "ha_balanceador_ip": (ha["balanceador"] or {}).get("ip", ""),
              "ha_replicas_nuevas": n, "hab_replicas_nuevas": n,
              "ha_nuevo": (nuevo or {}).get("nombre", ""), "ha_nuevo_ip": (nuevo or {}).get("ip", ""), "ha_puerto_lb": 8008, "ha_ruta": ruta}
    if ya + n > 5:
        return {"error": f"Ya hay {ya} réplicas de este componente; el máximo por solicitud es 5"}, 400
    try:
        t = solicitar(g.usuario, g.cliente, a, segmento, params,
                      f"Mapa: replicar {e['nombre']} ×{n} con HA · balanceo round robin en "
                      + (ha["balanceador"]["nombre"] if ha["balanceador"] else f"{nuevo['nombre']} (nuevo, puerto 8008)"))
    except (PermissionError, ErrorAAP) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])}


@bp.route("/mapa/homologacion")
@requiere_login
def homologacion_componente():
    """Homologación de un componente del segmento: sus máquinas de desarrollo, QA y producción lado a lado, con pestañas
    por tema (máquinas, sistema, recursos, producto, parches y seguridad). Informativa: cada máquina lleva a su ficha."""
    from .arquitectura import vigente
    seg, comp = request.args.get("segmento", ""), request.args.get("comp", "")
    if not puede_ver_segmento(g.usuario, g.cliente["id"], seg):
        abort(404)
    H = _homologar_segmento(seg, vigente(g.cliente["id"], seg))
    filas = [f for f in H["filas"] if f["comp"] == comp]
    if not filas:
        abort(404)
    maq = filas[0]["celdas"]
    grupos = []
    for f in filas:
        if f["grupo"] not in grupos:
            grupos.append(f["grupo"])
    difs = {gr: sum(1 for f in filas if f["grupo"] == gr for a, _ in H["ambientes"] if f["celdas"][a]["est"] in ("distinto", "falta")) for gr in grupos}
    return render_template("homologacion_componente.html", segmento=seg, comp=comp, filas=filas, grupos=grupos, difs=difs, H=H, maq=maq,
                           volver=url_for("consola.mapa", vista="aplicaciones", segmento=seg, ambiente="homologacion"))


@bp.route("/mapa/salud")
@requiere_login
def salud_detalle():
    """Detalle de la salud del segmento en un ambiente: todo lo que hay que remediar, por área, con su remediación."""
    from .topologia import resumen_segmento
    seg, amb = request.args.get("segmento", ""), request.args.get("ambiente", "produccion")
    if not puede_ver_segmento(g.usuario, g.cliente["id"], seg):
        abort(404)
    eqs = equipos_visibles()
    r = resumen_segmento(g.cliente, eqs, hallazgos_visibles(eqs), seg, amb)
    areas = []
    for a in r["salud_areas"]:
        areas.append(dict(a, items=[x for x in r["salud"] if x["area"] == a["area"]]))
    return render_template("salud_segmento.html", r=r, segmento=seg, ambiente=amb, areas=areas,
                           ambientes=[("produccion", "Producción"), ("qa", "QA"), ("desarrollo", "Desarrollo")],
                           volver=url_for("consola.mapa", vista="aplicaciones", segmento=seg, ambiente=amb))


@bp.route("/mapa/bifurcacion", methods=["POST"])
@requiere_login
def mapa_bifurcacion():
    """Activa o desactiva la derivación de un segmento hacia el data lake: ticket en AAP (con aprobación) que pone el espejo
    de tráfico en su punto de entrada y arranca los flujos funcionales. El estado cambia cuando el ticket termina bien."""
    from .operacion import solicitar_en_equipo
    from .topologia import params_derivacion
    f = request.form
    if f.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    seg = f.get("segmento", "")
    if not q1("SELECT id FROM segmentos WHERE cliente_id=? AND nombre=?", g.cliente["id"], seg) or not puede_ver_segmento(g.usuario, g.cliente["id"], seg):
        return {"error": "Segmento no encontrado"}, 404
    e = next((x for x in equipos_visibles() if x["nombre"] == f.get("punto", "")), None)
    if not e:
        return {"error": "No encuentro la máquina de entrada del segmento (el punto donde se copia el tráfico)"}, 400
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], DATALAKE)
    if not a:
        return {"error": f"Falta «{DATALAKE}» en el catálogo (sincroniza con AAP)"}, 400
    accion = "activar" if f.get("accion") == "activar" else "desactivar"
    try:
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, params_derivacion(g.cliente["id"], seg, e["nombre"], accion),
                                f"Data lake · {accion} la derivación del segmento {seg} (para JavIA)")
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    auditar(g.usuario["usuario"], g.cliente["id"], f"{accion} derivación al data lake (ticket)", seg, {"punto": e["nombre"], "ticket": t["numero"]})
    return {"ok": True, "ticket": t["numero"], "url": url_for("consola.ticket", tid=t["id"]), "estado": t["estado"]}


@bp.route("/mapa/rendimiento", methods=["GET", "POST"])
@requiere_login
def mapa_rendimiento():
    """Prueba de rendimiento del canal (en vivo): POST accion=iniciar|pausar|reanudar|detener|umbral; GET su avance y muestras."""
    from . import rendimiento as REN
    from .aap import ErrorAAP as _EA
    seg = request.values.get("segmento", "")
    if not q1("SELECT id FROM segmentos WHERE cliente_id=? AND nombre=?", g.cliente["id"], seg) or not puede_ver_segmento(g.usuario, g.cliente["id"], seg):
        return {"error": "Segmento no encontrado"}, 404
    if request.method == "POST":
        f = request.form
        if f.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
            return {"error": "Necesitas perfil de escritura"}, 403
        acc = f.get("accion", "iniciar")
        try:
            if acc == "umbral":
                v = REN.umbral(g.cliente["id"], float(f.get("umbral") or REN.UMBRAL_DEF))
                auditar(g.usuario["usuario"], g.cliente["id"], "umbral de rendimiento", f"{v:.0f} ms")
            elif acc == "iniciar":
                actual = REN.estado(g.cliente, seg)
                if actual and actual["estado"] in ("corriendo", "pausado"):
                    return {"error": "Ya hay una prueba en curso: detenla antes de iniciar otra"}, 409
                REN.iniciar(g.usuario, g.cliente, seg, equipos_visibles(), int(f.get("segundos") or 30))
            elif acc in ("pausar", "reanudar", "detener"):
                REN.controlar(g.usuario, g.cliente, seg, equipos_visibles(), acc)
            else:
                return {"error": "Acción no válida"}, 400
        except (ValueError, _EA) as x:
            return {"error": str(x)}, 400
    r = REN.estado(g.cliente, seg)
    return r or {"estado": "sin pruebas", "umbral": REN.umbral(g.cliente["id"])}


@bp.route("/mapa/datos")
@requiere_login
def mapa_datos():
    from .topologia import construir
    eqs = equipos_visibles()
    amb = request.args.get("ambiente", "")
    els, miga = construir(g.cliente, eqs, hallazgos_visibles(eqs), request.args.get("nivel", "global"),
                          request.args.get("valor", ""), request.args.get("vista", "infra"), amb)
    acceso = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='FaceOps · Acceso de red' AND visible=1", g.cliente["id"])
    # por qué está en rojo o amarillo cada máquina y con qué se repara (para resolverlo desde el mapa)
    from .operacion import aplica_a_equipo, requiere_aprobacion
    autos, por_eq_e = autos_visibles(), {e["id"]: e for e in eqs}
    ids_mapa = {el["data"].get("eid") for el in els if el["data"].get("eid")}
    por_equipo_h = defaultdict(list)
    sev_n = {"alta": 3, "media": 2, "baja": 1}
    for h in hallazgos_visibles(eqs):
        if h["equipo_id"] not in ids_mapa:
            continue
        e = por_eq_e[h["equipo_id"]]
        a = auto_sugerida(h["sugerencia"], autos)
        rem = None
        if a and g.perfil["modo"] == "escritura" and aplica_a_equipo(g.usuario, g.cliente["id"], a, e):
            req = [c for c in j(a["campos"], []) if c.get("requerido") and c["tipo"] not in ("equipo", "equipos") and c.get("defecto") in (None, "")]
            rem = {"id": a["id"], "nombre": a["nombre"], "aprob": requiere_aprobacion(a, e.get("ambiente")), "pide_datos": bool(req),
                   "url": url_for("consola.equipo", eid=e["id"]) + "#codigo"}
        por_equipo_h[h["equipo_id"]].append({"titulo": h["titulo"], "detalle": h["detalle"], "severidad": h["severidad"],
                                             "n": sev_n.get(h["severidad"], 0), "remediacion": rem,
                                             "apagar": h["sugerencia"] == "apagar" and g.perfil["modo"] == "escritura"})
    for v_ in por_equipo_h.values():
        v_.sort(key=lambda x: -x["n"])
    from .politicas_seg import tablero as tablero_pol
    _, pol_eq = tablero_pol(g.cliente["id"], eqs)
    from .cves import por_equipo as cves_por_equipo
    cve_eq = cves_por_equipo(g.cliente["id"], eqs)
    from .consola import disponible, tipo_consola, web_de
    from .inventario import sistema_eq
    consola_ok = disponible()
    replicar_ok = bool(q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], REPLICAR))
    cert_a = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Certificados · Emitir para servicio' AND visible=1", g.cliente["id"])
    por_nom_e = {e["nombre"]: e for e in eqs}
    RECARGA = {"nginx": "systemctl reload nginx", "httpd": "systemctl reload httpd", "jboss": "systemctl restart jboss", "jboss-eap": "systemctl restart jboss",
               "tomcat": "systemctl restart tomcat", "postgres": "systemctl restart postgresql", "keycloak": "systemctl restart keycloak", "redis": "systemctl restart redis",
               "kafka": "systemctl restart kafka"}
    for el in els:
        d = el["data"]
        if d.get("bif") and g.perfil["modo"] == "escritura":
            d["bif"]["url"] = url_for("consola.mapa_bifurcacion")
        if cert_a and d.get("tipo") == "flujo" and not (d.get("tls") or {}).get("tipo") and por_nom_e.get(d.get("hacia")) and g.perfil["modo"] == "escritura":
            dst = por_nom_e[d["hacia"]]
            sk = next((k for k in dst.get("skills") or [] if k in RECARGA), "")
            d["cert"] = {"url": url_for("consola.inventario") + f"/{dst['id']}/ejecutar/{cert_a['id']}", "equipo": dst["nombre"],
                         "servicio": (sk or "servicio").replace("jboss-eap", "jboss"), "recargar": RECARGA.get(sk, ""),
                         "puerto": (re.findall(r"\d{2,5}", d.get("puerto") or "") or [""])[0],
                         "formato": "pkcs12" if sk in ("jboss", "jboss-eap", "tomcat", "kafka", "keycloak") else "pem"}
        if acceso and d.get("origen_ip") and d.get("tipo") == "flujo":
            comun = dict(aid=acceso["id"], v_origen=d["origen_ip"], v_destino=d["destino_ip"], v_puerto=d["puerto"].replace(" · ", ", "))
            if d.get("estado_flujo") != "abierto":
                d["desbloquear"] = url_for("consola.automatizacion", v_red_accion="abrir", **comun,
                                           motivo=f"Mapa: abrir {d['desde']} → {d['hacia']} ({d['puerto']})")
            if d.get("estado_flujo") != "bloqueado":
                d["bloquear"] = url_for("consola.automatizacion", v_red_accion="bloquear", **comun,
                                        motivo=f"Mapa: bloquear {d['desde']} → {d['hacia']} ({d['puerto']})")
        if d.get("eid"):
            d["url"] = url_for("consola.equipo", eid=d["eid"])
            d["acciones"] = url_for("consola.equipo", eid=d["eid"]) + "#acciones"
            d["ejecutar"] = url_for("consola.inventario") + f"/{d['eid']}/ejecutar/"
            d["apagar"] = url_for("consola.equipo", eid=d["eid"])
            e_en = por_eq_e.get(d["eid"])
            d["energia_url"] = url_for("consola.equipo_energia", eid=d["eid"]) if (e_en and g.perfil["modo"] == "escritura" and energia_posible(e_en)) else ""
            d["hallazgos"] = por_equipo_h.get(d["eid"], [])
            e_ = por_eq_e.get(d["eid"])
            if e_:  # accesos: consola web (vía FaceOps) y SSH o escritorio remoto (vía Guacamole)
                w = web_de(e_)
                d["web"] = dict(w, abrir=url_for("consola.web_equipo", eid=e_["id"], sub="")) if w else None
                t_ = tipo_consola(e_, sistema_eq(e_)) if consola_ok and g.perfil["modo"] == "escritura" else None
                from .boveda import acceso as acceso_b, web as web_b, disponible as boveda_ok
                cred_ = acceso_b(e_["nombre"]) if boveda_ok() else None
                d["ssh"] = {"tipo": t_, "url": url_for("consola.consola_equipo", eid=e_["id"]),
                            "destino": (f"{(cred_ or {}).get('usuario') or 'ansible'}@{e_['ip']}" if t_ == "ssh" else ((cred_ or {}).get("usuario", "") + "@" if cred_ else "") + e_["ip"]),
                            "boveda": bool(cred_)} if t_ else None
                if d.get("web") is not None and boveda_ok() and web_b(e_["nombre"]):
                    d["web"]["credencial"] = url_for("consola.credencial_web", eid=e_["id"])
                d["politicas"] = [{"codigo": x["codigo"], "nombre": x["nombre"], "estado": x["estado"], "version": x["version"], "vigente": x["vigente"],
                                   "url": url_for("consola.politica", codigo=x["codigo"]),
                                   "solicitar": url_for("consola.politica_solicitar", codigo=x["codigo"])} for x in pol_eq.get(e_["id"], [])]
                cx = cve_eq.get(e_["id"]) or {}
                d["cves"] = dict({k: cx.get(k) for k in ("revisado", "repos", "pendientes", "criticos", "importantes", "cubiertos")},
                                 top=[x["cve"] for x in cx.get("top", [])[:3]], url=url_for("consola.equipo", eid=e_["id"]) + "#cves") if cx.get("revisado") else None
                d["eliminar"] = url_for("consola.eliminar_equipo", eid=e_["id"]) if g.perfil["modo"] == "escritura" and eliminable(e_) else None
                d["respaldo"] = {"versiones": url_for("consola.versiones_equipo", eid=e_["id"])} if "respaldo-git" in e_["skills"] else None
                d["ha"] = datos_ha(e_, els, eqs, request.args.get("valor", "") if request.args.get("nivel") == "segmento" else "") \
                    if g.perfil["modo"] == "escritura" and replicar_ok else None
    seg = None
    if request.args.get("nivel") == "segmento" and request.args.get("valor"):
        from .topologia import resumen_segmento
        seg = resumen_segmento(g.cliente, eqs, hallazgos_visibles(eqs), request.args["valor"], amb)
        seg["url"] = url_for("consola.arquitectura", segmento=seg["nombre"])
        seg["inventario"] = url_for("consola.inventario", q=seg["nombre"])
    return {"elementos": els, "miga": miga, "segmento": seg}


@bp.route("/auditoria")
@requiere_login
def auditoria():
    if not puede_aprobar(g.usuario, g.cliente["id"]):
        abort(403)
    return render_template("auditoria.html", filas=q("SELECT * FROM auditoria WHERE cliente_id=? OR cliente_id IS NULL "
                                                      "ORDER BY id DESC LIMIT 300", g.cliente["id"]))


# ------------------------------------------------------------------ arquitectura de los segmentos de negocio
def _arq_vigente(segmento):
    from .arquitectura import vigente
    if not segmento or not puede_ver_segmento(g.usuario, g.cliente["id"], segmento):
        abort(404)
    return vigente(g.cliente["id"], segmento)


def _escritura():
    """Perfil con escritura; en POST además el token CSRF (un GET que solo muestra una pantalla no lo trae)."""
    if g.perfil["modo"] != "escritura" or (request.method != "GET" and request.form.get("_csrf") != session.get("csrf")):
        abort(403)


@bp.route("/arquitectura")
@requiere_login
def arquitectura():
    """Ficha del segmento de negocio, su arquitectura aprobada y el plan contra el inventario."""
    from .arquitectura import FICHA, costo, plan, ultimo_recorrido
    segmento = request.args.get("segmento", "")
    a = _arq_vigente(segmento)
    versiones = q("SELECT id, version, estado, origen, autor, creado, aprobado_por, aprobado_en, ticket_id FROM arquitecturas "
                  "WHERE cliente_id=? AND segmento=? ORDER BY version DESC", g.cliente["id"], segmento)
    seg = q1("SELECT * FROM segmentos WHERE cliente_id=? AND nombre=?", g.cliente["id"], segmento)
    eqs = equipos_visibles()
    p = plan(g.cliente, a["m"], eqs) if a else None
    return render_template("arquitectura.html", segmento=segmento, a=a, m=a["m"] if a else None, p=p, seg=seg, versiones=versiones,
                           k=costo(a["m"], eqs) if a else None,
                           FICHA=FICHA, ficha=(a["m"]["ficha"] if a else j(seg["ficha"] if seg else "{}", {})),
                           recorrido=ultimo_recorrido(g.cliente["id"], segmento),
                           puede_aprobar=puede_aprobar(g.usuario, g.cliente["id"]))


@bp.route("/arquitectura/version/<int:aid>.yml")
@requiere_login
def arquitectura_yaml(aid):
    a = q1("SELECT * FROM arquitecturas WHERE id=? AND cliente_id=?", aid, g.cliente["id"])
    if not a or not puede_ver_segmento(g.usuario, g.cliente["id"], a["segmento"]):
        abort(404)
    nombre = re.sub(r"[^a-z0-9]+", "-", normal(a["segmento"])).strip("-")
    original = request.args.get("original") and a.get("original")  # el adjunto tal como lo pidió el proyecto
    return (a["original"] if original else a["contenido"]), 200, {
        "Content-Type": "text/yaml; charset=utf-8",
        "Content-Disposition": f'attachment; filename="{nombre}-v{a["version"]}{"-solicitud" if original else ""}.yml"'}


@bp.route("/arquitectura/nueva", methods=["GET", "POST"])
@requiere_login
def arquitectura_nueva():
    """Nuevo segmento (o nueva versión) desde su arquitectura: se pega o sube el YAML, se revisa y queda como propuesta."""
    from .arquitectura import DIR, costo, leer, plan, proponer
    texto, m, errores, p, k = "", None, [], None, None
    if request.method == "POST":
        _escritura()
        f = request.files.get("archivo")
        texto = (f.read().decode("utf-8", "replace") if f and f.filename else request.form.get("texto", "")).strip()
        m, errores = leer(texto) if texto else (None, ["Pega o sube el manifiesto"])
        if m and not errores and m["segmento"] and not puede_ver_segmento(g.usuario, g.cliente["id"], m["segmento"]) \
                and q1("SELECT id FROM segmentos WHERE cliente_id=? AND nombre=?", g.cliente["id"], m["segmento"]):
            errores = ["No tienes acceso a ese segmento"]
        if m and not errores:
            eqs = equipos_visibles()
            p, k = plan(g.cliente, m, eqs), costo(m, eqs)
            if request.form.get("accion") == "proponer":
                from .solicitud_segmento import solicitar as solicitar_segmento
                try:
                    t = solicitar_segmento(g.usuario, g.cliente, texto, request.form.get("motivo", "").strip())
                    flash(f"{t['numero']}: solicitud enviada a doble aprobación en AAP (Arquitectura → Infraestructura)", "ok")
                    return redirect(url_for("consola.ticket", tid=t["id"]))
                except (ValueError, PermissionError, ErrorAAP) as e:
                    errores = [str(e)]
    elif request.args.get("desde"):
        a = _arq_vigente(request.args["desde"])
        texto = a["contenido"] if a else ""
    else:
        try:
            texto = open(f"{DIR}/ejemplo-ventas-en-linea.yml.ejemplo", encoding="utf-8").read().split("\n", 1)[1].strip()
        except (OSError, IndexError):
            texto = ""
        from .grupos_segmento import normalizar as norm_grupo
        gr = norm_grupo(request.args.get("grupo"))
        if gr and texto:
            import re as _re
            texto = _re.sub(r"(?m)^grupo:.*\n", "", texto)
            texto = _re.sub(r"(?m)^(segmento:.*)$", r"\1\ngrupo: " + gr, texto, count=1)
    return render_template("arquitectura_nueva.html", texto=texto, m=m, errores=errores, p=p, k=k)


@bp.route("/arquitectura/<int:aid>/<accion>", methods=["POST"])
@requiere_login
def arquitectura_decidir(aid, accion):
    from .arquitectura import aprobar, rechazar
    _escritura()
    if not puede_aprobar(g.usuario, g.cliente["id"]) or accion not in ("aprobar", "rechazar"):
        abort(403)
    a = q1("SELECT * FROM arquitecturas WHERE id=? AND cliente_id=?", aid, g.cliente["id"])
    if not a:
        abort(404)
    if a.get("ticket_id"):
        flash("Esta versión se aprueba en su ticket (doble aprobación en AAP)", "error")
        return redirect(url_for("consola.ticket", tid=a["ticket_id"]))
    try:
        if accion == "aprobar":
            aprobar(g.cliente, aid, g.usuario["usuario"])
            from .inventario import calcular
            calcular(g.cliente)
            flash(f"Arquitectura v{a['version']} aprobada: el segmento quedó definido por ella", "ok")
        else:
            rechazar(g.cliente, aid, g.usuario["usuario"])
            flash(f"Arquitectura v{a['version']} rechazada", "ok")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("consola.arquitectura", segmento=a["segmento"]))


@bp.route("/arquitectura/paso", methods=["POST"])
@requiere_login
def arquitectura_paso():
    """Ejecuta uno o todos los pasos pendientes del plan: crea los tickets de una vez (JSON)."""
    from .arquitectura import ejecutar_paso, plan
    _escritura()
    a = _arq_vigente(request.form.get("segmento", ""))
    if not a:
        return {"error": "El segmento no tiene una arquitectura aprobada"}, 400
    eqs = equipos_visibles()
    p = plan(g.cliente, a["m"], eqs)
    clave = request.form.get("clave", "")
    pasos = [x for x in p["pasos"] if not x.get("ticket") and x.get("posible") and x["tipo"] != "verificar"
             and (clave == "*" or x["clave"] == clave)]
    if not pasos:
        return {"error": "No hay pasos pendientes (o ya tienen ticket en curso)"}, 400
    out = []
    for x in pasos:
        try:
            t = ejecutar_paso(g.usuario, g.cliente, dict(a["m"], version=a["version"]), x, eqs)
            out.append({"clave": x["clave"], "numero": t["numero"], "estado": t["estado"], "id": t["id"],
                        "url": url_for("consola.ticket", tid=t["id"])})
        except (ValueError, PermissionError, ErrorAAP) as e:
            out.append({"clave": x["clave"], "error": str(e)})
    return {"tickets": out}


@bp.route("/arquitectura/recorrido", methods=["GET", "POST"])
@requiere_login
def arquitectura_recorrido():
    """POST: prueba el recorrido de punta a punta (entrada HTTP + cada tramo en AAP). GET: estado para refrescar."""
    from .arquitectura import plan, probar_recorrido, ultimo_recorrido
    segmento = request.values.get("segmento", "")
    a = _arq_vigente(segmento)
    if not a:
        return {"error": "El segmento no tiene una arquitectura aprobada (sin entrada ni tramos declarados)"}, 400
    eqs = equipos_visibles()
    if request.method == "POST":
        _escritura()
        probar_recorrido(g.usuario, g.cliente, a["m"], eqs)
    r = ultimo_recorrido(g.cliente["id"], segmento)
    p = plan(g.cliente, a["m"], eqs)
    tramos = [{"desde": f["desde"], "hacia": f["hacia"], "puerto": f["puerto"], "estado": f["estado"], "lat": f.get("lat", ""),
               "texto": f["texto"], "orden": f["orden"]} for f in p["flujos"] if f["estado"] != "entrada"]
    return {"cuando": r["cuando"] if r else None, "entrada": (r["r"].get("entrada") if r else None), "error": (r["r"].get("error") if r else ""),
            "decl_entrada": a["m"].get("entrada") or {}, "tramos": tramos,
            "verificando": any(t["estado"] == "verificando" for t in tramos)}


@bp.route("/arquitectura/app")
@requiere_login
def arquitectura_app():
    """Abre la aplicación del segmento a través de FaceOps (su URL de entrada vive en la red interna del cliente):
    lo que se ve es la respuesta real de la app, incluida su conexión a la base de datos."""
    import requests
    a = _arq_vigente(request.args.get("segmento", ""))
    url = ((a or {}).get("m") or {}).get("entrada", {}).get("url") if a else None
    if not url:
        abort(404)
    auditar(g.usuario["usuario"], g.cliente["id"], "abrir aplicación", a["segmento"], {"url": url})
    try:
        r = requests.get(url, timeout=8, verify=False)
    except requests.RequestException as e:
        return render_template("app_caida.html", segmento=a["segmento"], url=url, error=str(e).split(":")[0][:160]), 502
    tipo = r.headers.get("Content-Type", "text/html; charset=utf-8")
    cuerpo = r.content
    if "text/html" in tipo:  # una franja arriba deja claro que se ve a través de FaceOps
        franja = (f'<div style="font:13px Arial,sans-serif;background:#ee0000;color:#fff;padding:6px 14px">Vista a través de FaceOps · '
                  f'{a["segmento"]} · {url} · HTTP {r.status_code} en {r.elapsed.total_seconds() * 1000:.0f} ms · '
                  f'<a style="color:#fff" href="{url_for("consola.arquitectura", segmento=a["segmento"])}">volver al segmento</a></div>').encode()
        txt = cuerpo.decode(r.encoding or "utf-8", "replace")
        i = txt.lower().find("<body")
        j_ = txt.find(">", i) + 1 if i >= 0 else 0
        cuerpo = (txt[:j_] + franja.decode() + txt[j_:]).encode("utf-8")
        tipo = "text/html; charset=utf-8"
    return cuerpo, r.status_code, {"Content-Type": tipo, "Cache-Control": "no-store"}


# ------------------------------------------------------------------ políticas de seguridad (automatizaciones versionadas)
@bp.route("/politicas")
@requiere_login
def politicas():
    from .politicas_seg import CATEGORIAS, DESC_CAT, ETIQUETA, tablero
    pols, _ = tablero(g.cliente["id"], equipos_visibles())
    cat = request.args.get("categoria", "")
    conteo = {k: len([p for p in pols if p["categoria"] == k]) for k, _ in CATEGORIAS}
    total = {k: sum(p["cuenta"][k] for p in pols) for k in ETIQUETA}
    orden = [k for k, _ in CATEGORIAS]
    pols.sort(key=lambda p: (orden.index(p["categoria"]) if p["categoria"] in orden else 99, p["codigo"]))
    # cajas por categoría (todas, aunque no tengan políticas: desde cada una se agrega la primera)
    cajas = [{"codigo": k, "nombre": n, "pols": [p for p in pols if p["categoria"] == k], "desc": DESC_CAT.get(k, ""),
              "cuenta": {e: sum(p["cuenta"][e] for p in pols if p["categoria"] == k) for e in ETIQUETA}} for k, n in CATEGORIAS]
    return render_template("politicas.html", pols=[p for p in pols if not cat or p["categoria"] == cat], categorias=CATEGORIAS, cat=cat,
                           conteo=conteo, total=total, etiqueta=ETIQUETA, cajas=cajas, cat_nombre=dict(CATEGORIAS).get(cat, ""))


@bp.route("/politicas/nueva", methods=["GET", "POST"])
@requiere_login
def politica_nueva():
    """Política nueva dentro de una categoría: se escribe en YAML (con plantilla), se valida y queda en Git como versión 1."""
    import yaml
    from .politicas_seg import CATEGORIAS, PLANTILLA, codigo_nuevo, editar, una, validar, _normalizar
    if g.perfil["modo"] != "escritura":
        abort(403)
    cat = request.values.get("categoria", "")
    if cat not in dict(CATEGORIAS):
        abort(404)
    codigo = (request.form.get("codigo") or codigo_nuevo(g.cliente["id"], cat)).strip().upper()
    texto = request.form.get("texto") or yaml.safe_dump(PLANTILLA, allow_unicode=True, sort_keys=False, width=140)
    if request.method == "POST":
        if request.form.get("_csrf") != session.get("csrf"):
            abort(400)
        try:
            p = _normalizar(yaml.safe_load(texto) or {})
        except yaml.YAMLError as x:
            flash(f"YAML no válido: {str(x)[:200]}", "error")
            p = None
        if p is not None:
            p.update(codigo=codigo, categoria=cat)
            err = validar(p)
            if not re.match(r"^[A-Z]{2,4}-[0-9]{2,3}$", codigo):
                err.append("el código debe ser como CON-03")
            if una(g.cliente["id"], codigo):
                err.append(f"ya existe la política {codigo}")
            if err:
                flash("No se guardó: " + "; ".join(err), "error")
            else:
                try:
                    v = editar(g.cliente["id"], p, g.usuario["usuario"], request.form.get("motivo", "") or "política nueva en FaceOps")
                    auditar(g.usuario["usuario"], g.cliente["id"], "nueva política", codigo, {"categoria": cat, "version": v})
                    flash(f"Política {codigo} creada en Git (versión {v}); se asigna sola a los equipos que cumplen sus condiciones", "ok")
                    return redirect(url_for("consola.politica", codigo=codigo))
                except RuntimeError as x:
                    flash(str(x), "error")
    return render_template("politica_nueva.html", cat=cat, cat_nombre=dict(CATEGORIAS)[cat], codigo=codigo, texto=texto)


@bp.route("/politicas/<codigo>", methods=["GET", "POST"])
@requiere_login
def politica(codigo):
    import yaml
    from .politicas_seg import ETIQUETA, editar, historial, tablero, validar, _normalizar
    if request.method == "POST":
        if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
            abort(400)
        try:
            p = _normalizar(yaml.safe_load(request.form.get("texto", "")) or {})
        except yaml.YAMLError as x:
            flash(f"YAML no válido: {str(x)[:200]}", "error")
            return redirect(url_for("consola.politica", codigo=codigo))
        p["codigo"] = codigo
        err = validar(p)
        if err:
            flash("No se guardó: " + "; ".join(err), "error")
        else:
            try:
                v = editar(g.cliente["id"], p, g.usuario["usuario"], request.form.get("motivo", "") or "edición en FaceOps")
                auditar(g.usuario["usuario"], g.cliente["id"], "editar política", codigo, {"version": v})
                flash(f"Política {codigo} guardada en Git como versión {v}: los equipos que la tenían quedan desactualizados", "ok")
            except RuntimeError as x:
                flash(str(x), "error")
        return redirect(url_for("consola.politica", codigo=codigo))
    pols, _ = tablero(g.cliente["id"], equipos_visibles())
    p = next((x for x in pols if x["codigo"] == codigo), None)
    if not p:
        abort(404)
    texto = yaml.safe_dump({k: v for k, v in p.items() if k in ("nombre", "categoria", "severidad", "descripcion", "aplica", "reglas", "recargar")},
                           allow_unicode=True, sort_keys=False, width=140)
    return render_template("politica.html", p=p, texto=texto, historial=historial(g.cliente["id"], codigo), etiqueta=ETIQUETA)


@bp.route("/politicas/<codigo>/eliminar", methods=["POST"])
@requiere_login
def politica_eliminar(codigo):
    """Retira la política: se borra en Git (queda el historial) y deja de asignarse; lo aplicado en las máquinas se mantiene."""
    from .politicas_seg import eliminar, una
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(400)
    p = una(g.cliente["id"], codigo)
    if not p:
        abort(404)
    try:
        eliminar(g.cliente["id"], codigo, g.usuario["usuario"], request.form.get("motivo", "") or "retirada en FaceOps")
        auditar(g.usuario["usuario"], g.cliente["id"], "eliminar política", codigo, {"categoria": p.get("categoria")})
        flash(f"Política {codigo} eliminada: ya no se asigna a los equipos (su historial queda en Git)", "ok")
    except RuntimeError as x:
        flash(str(x), "error")
    return redirect(url_for("consola.politicas", categoria=p.get("categoria")))


@bp.route("/politicas/<codigo>/solicitar", methods=["POST"])
@requiere_login
def politica_solicitar(codigo):
    """Aplicar o verificar la política: en los pendientes y desactualizados, en todos o en un equipo (ticket por inventario)."""
    from .politicas_seg import solicitar as solicitar_pol, tablero
    f = request.form
    if f.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(400)
    pols, _ = tablero(g.cliente["id"], equipos_visibles())
    p = next((x for x in pols if x["codigo"] == codigo), None)
    if not p:
        abort(404)
    modo, alcance = ("verificar" if f.get("modo") == "verificar" else "aplicar"), f.get("alcance", "pendientes")
    es = [x["equipo"] for x in p["equipos"] if (alcance == "todos" or (alcance == "pendientes" and x["estado"] != "al_dia")
                                                 or (alcance.isdigit() and x["equipo"]["id"] == int(alcance)))]
    if not es:
        msg = "No hay equipos pendientes ni desactualizados para esta política"
        if request.headers.get("X-Inline"):
            return {"error": msg}, 400
        flash(msg, "info")
        return redirect(request.referrer or url_for("consola.politicas"))
    try:
        ts = solicitar_pol(g.usuario, g.cliente, p, es, modo)
    except (PermissionError, ErrorAAP) as x:
        if request.headers.get("X-Inline"):
            return {"error": str(x)}, 400
        flash(str(x), "error")
        return redirect(request.referrer or url_for("consola.politicas"))
    if request.headers.get("X-Inline"):
        return {"tickets": [{"numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"])} for t in ts]}
    flash(f"{'Verificación' if modo == 'verificar' else 'Aplicación'} de {codigo} en {len(es)} equipo(s): " + ", ".join(t["numero"] for t in ts), "ok")
    return redirect(request.referrer or url_for("consola.politicas"))


# ------------------------------------------------------------------ seguridad: CVE cubiertos y pendientes por equipo
@bp.route("/seguridad/cves")
@requiere_login
def cves():
    from .compatibilidad import compatible
    from .cves import REVISAR, por_equipo, resumen
    eqs = equipos_visibles()
    a_rev = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], REVISAR)
    evaluables = [e for e in eqs if a_rev and compatible(a_rev, e)[0]]
    pe = por_equipo(g.cliente["id"], evaluables)
    vista = request.args.get("vista", "pendientes")
    pend = resumen(g.cliente["id"], evaluables, "pendiente")
    kpi = {"pendientes": len(pend), "criticos": len([x for x in pend if x["severidad"] == "crítica"]),
           "importantes": len([x for x in pend if x["severidad"] == "importante"]),
           "afectados": len([1 for x in pe.values() if x["pendientes"]]), "cubiertos": sum(x["cubiertos"] for x in pe.values()),
           "sin_evaluar": len([1 for x in pe.values() if not x["repos"]]), "equipos": len(evaluables)}
    ent_sel = request.args.get("entorno", "")
    conteo_ent = Counter(en for x in pend for en in x["entornos"])
    if ent_sel:
        pend = [x for x in pend if ent_sel in x["entornos"]]
    return render_template("cves.html", vista=vista, kpi=kpi, pend=pend, conteo_ent=conteo_ent, ent_sel=ent_sel, cub=resumen(g.cliente["id"], evaluables, "cubierto") if vista == "cubiertos" else [],
                           equipos=[(e, pe[e["id"]]) for e in evaluables])


@bp.route("/seguridad/cves/<cve>")
@requiere_login
def cve_detalle(cve):
    """Ficha de un CVE: referencia oficial (Red Hat, NVD, MITRE), entorno que impacta y las máquinas afectadas y protegidas."""
    from .compatibilidad import compatible
    from .cves import REVISAR, entorno, oficial
    if not re.match(r"^CVE-\d{4}-\d{4,7}$", cve):
        abort(404)
    eqs = {e["id"]: e for e in equipos_visibles()}
    a_rev = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], REVISAR)
    filas = q("SELECT * FROM cves_equipo WHERE cliente_id=? AND cve=?", g.cliente["id"], cve)
    if not filas:
        abort(404)
    afect, prot = {}, {}
    for r in filas:
        e = eqs.get(r["equipo_id"])
        if not e or (a_rev and not compatible(a_rev, e)[0]):
            continue
        (afect if r["estado"] == "pendiente" else prot).setdefault(e["id"], {"e": e, "paquetes": set(), "visto": r["visto"]})["paquetes"].add(r["paquete"])
    sev = filas[0]["severidad"]
    paquetes = sorted({(r["paquete"] or "").rsplit("-", 2)[0] for r in filas if r["paquete"]})
    entornos = sorted({entorno(r["paquete"]) for r in filas})
    return render_template("cve.html", cve=cve, sev=sev, paquetes=paquetes, entornos=entornos, of=oficial(cve),
                           afect=sorted(afect.values(), key=lambda x: x["e"]["nombre"]), prot=sorted(prot.values(), key=lambda x: x["e"]["nombre"]))


@bp.route("/seguridad/cves/solicitar", methods=["POST"])
@requiere_login
def cves_solicitar():
    """Revisar CVEs (lectura) o aplicar parches de seguridad (con aprobación en AAP), en todos, un equipo o los afectados por un CVE."""
    from .compatibilidad import compatible
    from .cves import PARCHAR, REVISAR, solicitar as solicitar_cve
    f = request.form
    if f.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(400)
    nombre = PARCHAR if f.get("accion") == "parchar" else REVISAR
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], nombre)
    eqs = [e for e in equipos_visibles() if a and compatible(a, e)[0]]
    alc, cve = f.get("alcance", "todos"), f.get("cve", "")
    if alc.isdigit():
        eqs = [e for e in eqs if e["id"] == int(alc)]
    elif cve:
        ids = {r["equipo_id"] for r in q("SELECT equipo_id FROM cves_equipo WHERE cliente_id=? AND cve=? AND estado='pendiente'", g.cliente["id"], cve)}
        eqs = [e for e in eqs if e["id"] in ids]
    if not eqs:
        flash("No hay equipos compatibles para esa acción", "info")
        return redirect(request.referrer or url_for("consola.cves"))
    params = {"cves": cve} if cve else {}
    motivo = (f"Parchar {cve or 'vulnerabilidades pendientes'}" if nombre == PARCHAR else "Revisar CVE") + f" en {len(eqs)} equipo(s)"
    try:
        ts = solicitar_cve(g.usuario, g.cliente, nombre, eqs, params, motivo)
        flash(motivo + ": " + ", ".join(t["numero"] for t in ts) + (" · requiere aprobación en AAP" if nombre == PARCHAR else ""), "ok")
    except (PermissionError, ErrorAAP) as x:
        flash(str(x), "error")
    return redirect(request.referrer or url_for("consola.cves"))


# ------------------------------------------------------------------ facturación: créditos del plan, consumo y tabla de créditos
@bp.route("/facturacion")
@requiere_login
def facturacion():
    from .areas import AREAS, area_de
    from .economia import UNIDADES, estadisticas, metricas, regla, resumen
    est_t = estadisticas(g.cliente["id"])
    mes = request.args.get("mes") or datetime.now(timezone.utc).strftime("%Y-%m")
    ts = tickets_visibles(5000)
    r = resumen(g.cliente, mes, ts)
    from .creditos import TIPOS, estado as estado_creditos, movimientos
    r["cr"], r["movimientos"] = estado_creditos(g.cliente), movimientos(g.cliente["id"], mes=mes)
    meses = sorted({(t["creado"] or "")[:7] for t in ts if t["creado"]} | {mes}, reverse=True)[:12]
    tabla = []
    for a in autos_visibles(todas=True) + q("SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=0", g.cliente["id"]):
        rg = regla(a)
        tabla.append(dict(rg, nombre=a["nombre"], nombre_aap=a["nombre_aap"], area=area_de(a), minutos=a["minutos_manual"], id=a["id"], met=metricas(a, est_t)))
    nombres = {k: n for k, n, _ in AREAS}
    tabla.sort(key=lambda x: ([k for k, _, _ in AREAS].index(x["area"]) if x["area"] in nombres else 99, -x["creditos"], x["nombre"]))
    return render_template("facturacion.html", r=r, mes=mes, meses=meses, tabla=tabla, areas=nombres, tipos_mov=TIPOS,
                           vista=request.args.get("vista", "consumo"), unidades=UNIDADES)


@bp.route("/alertas")
@requiere_login
def alertas():
    """Monitoreo › Alertas: las alertas se configuran en cada objeto (botón «Alertas» de su ficha) y viven en Ansible y
    Prometheus; aquí, el estado de todos los objetos: cuáles tienen alertas, cuáles no y los cambios en curso."""
    from . import alertas_objeto as ao
    ids_vis = {e["id"] for e in equipos_visibles()}
    filas = [x for x in ao.resumen_cliente(g.cliente["id"]) if x["id"] in ids_vis]
    tipo = request.args.get("tipo", "")
    estado_f = request.args.get("estado", "")
    cuenta = {"con": sum(1 for x in filas if x["n"]), "sin": sum(1 for x in filas if not x["n"]), "reglas": sum(x["n"] for x in filas)}
    # solo los objetos con alertas configuradas: mientras no haya ninguna, la lista queda vacía (se configuran en la ficha de cada objeto)
    lista = [x for x in filas if x["n"] and (not tipo or x["tipo"] == tipo)]
    lista.sort(key=lambda x: (-x["n"], x["nombre"]))
    tipos = ao.catalogo()["tipos"]
    return render_template("alertas_objetos.html", lista=lista, cuenta=cuenta, tipo=tipo, estado_f=estado_f, tipos=tipos,
                           por_tipo={k: sum(1 for x in filas if x["tipo"] == k and x["n"]) for k in tipos})


RAIZ_DATOS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos")
_cache_ind = {}


def _indicadores(con_segmentos=True):
    """Radar (equipos y segmentos) e indicadores del cliente; caché de 2 minutos (se consultan en varias páginas)."""
    import time
    from . import indicadores, radar
    k = (g.cliente["id"], g.usuario["id"], con_segmentos)
    c = _cache_ind.get(k)
    if c and time.time() - c[0] < 120:
        return c[1]
    eqs = equipos_visibles()
    hs = hallazgos_visibles(eqs)
    r_eq = radar.radar_equipos([dict(e, _datos=j(e["datos"], {})) for e in eqs], hs)
    r_seg = None
    if con_segmentos:
        try:
            r_seg = radar.radar_segmentos(g.cliente, eqs, hs)
        except Exception as e_:  # noqa: BLE001
            current_app.logger.warning("radar de segmentos: %s", e_)
    v = indicadores.calcular(g.cliente["id"], eqs, hs, r_eq, r_seg)
    out = {"eq": r_eq, "seg": r_seg, "v": v}
    _cache_ind[k] = (time.time(), out)
    return out


_kpi_c = {}


def _kpi_yml():
    """kpi-okr.yml leído una vez y releído solo si cambia."""
    import yaml
    f = os.path.join(RAIZ_DATOS, "kpi-okr.yml")
    mt = os.path.getmtime(f)
    if _kpi_c.get("mt") != mt:
        _kpi_c.update(mt=mt, d=yaml.safe_load(open(f, encoding="utf-8")) or {})
    return _kpi_c["d"]


def _kpi_de(clave, v):
    """KPI y OKR de una opción de Monitoreo con su valor actual y su avance hacia la meta."""
    d = _kpi_yml().get(clave)
    if not d:
        return None

    def avance(x):
        val, meta, mejor = v.get(x.get("valor")), x.get("meta"), x.get("mejor", "alto")
        if val is None or meta is None:
            return dict(x, actual=val, avance=None, ok=None)
        if mejor == "bajo":
            av = 100 if val <= meta else max(0, round(100 * (meta / val))) if val else 100
        else:
            av = min(100, round(100 * val / meta)) if meta else 100
        return dict(x, actual=val, avance=av, ok=(val <= meta) if mejor == "bajo" else (val >= meta))
    return {"clave": clave, "titulo": d["titulo"], "kpis": [avance(x) for x in d.get("kpis") or []],
            "okrs": [dict(o, resultados=[avance(r) for r in o.get("resultados") or []]) for o in d.get("okrs") or []]}


@bp.app_template_global("kpi_de")
def kpi_de_global(clave):
    """En las plantillas de Monitoreo: {{ kpi_panel(kpi_de('tickets')) }}."""
    try:
        return _kpi_de(clave, _indicadores(False)["v"])
    except Exception as e_:  # noqa: BLE001
        current_app.logger.warning("kpi %s: %s", clave, e_)
        return None


@bp.route("/monitoreo/radar")
@requiere_login
def radar_salud():
    """Monitoreo › Radar de salud: la característica principal de Monitoreo. Los 5 principios del parque y de los segmentos,
    cada uno con sus criterios (casos) y su barra de cumplimiento, y los equipos o segmentos que no lo cumplen."""
    from .topologia import estrella_svg
    ind = _indicadores()
    vista = request.args.get("vista", "equipos")
    r = ind["seg"] if vista == "segmentos" and ind["seg"] else ind["eq"]
    return render_template("radar.html", r=r, ind=ind, vista=vista, svg=estrella_svg(r["actual"], r["meta"], tam=430), kpi=_kpi_de("radar", ind["v"]))


@bp.route("/gobierno-ti", methods=["GET", "POST"])
@requiere_login
def gobierno_ti():
    """Gobierno › Gobierno de TI: árbol de dependencia (N0 → N4) para validar su cumplimiento de a poco."""
    from . import gobierno_ti as GT
    from .bd import ajuste
    cid = g.cliente["id"]
    from .semillas import limpia as _limpia
    foco = j(ajuste(f"gti_foco_{cid}") or "", {}) or ({"industria": "", "pais": ""} if _limpia() else {"industria": "banca", "pais": "mx"})
    if request.method == "POST":
        _escritura()
        f = request.form
        if f.get("accion") == "foco":
            foco = {"industria": f.get("industria", ""), "pais": f.get("pais", "")}
            ajuste(f"gti_foco_{cid}", json.dumps(foco))
        else:
            try:
                GT.evaluar(cid, f.get("nodo", ""), f.get("nivel"), f.get("estado", "sin_evaluar"), f.get("evidencia", ""), f.get("nota", ""), g.usuario["usuario"])
                auditar(g.usuario["usuario"], cid, "evaluar gobierno de TI", f.get("nodo", ""), {"nivel": f.get("nivel"), "estado": f.get("estado")})
                flash("Evaluación guardada", "ok")
            except ValueError as x:
                flash(str(x), "error")
        return redirect(url_for("consola.gobierno_ti") + (("#" + f.get("ancla")) if f.get("ancla") else ""))
    v = GT.vista(cid, foco.get("industria", ""), foco.get("pais", ""))
    return render_template("gobierno_ti.html", v=v, hist=GT.historial(cid), foco=foco)


@bp.route("/seguridad/mapa")
@requiere_login
def seguridad_mapa():
    """Seguridad › Mapa de seguridad: todo el ecosistema interconectado con su mapa de calor de vulnerabilidad y la medida
    contra NIST CSF 2.0 (perfil actual contra perfil objetivo)."""
    import yaml
    from . import seguridad_mapa as SM
    eqs = equipos_visibles()
    hs = hallazgos_visibles(eqs)
    nube = {e["id"] for e in eqs if seccion_eq(e) == "nubes"}
    m = SM.calcular(g.cliente, [dict(e, _datos=j(e["datos"], {})) for e in eqs], hs, nube)
    v = _indicadores()["v"]
    funciones = SM.nist(g.cliente, m, v)
    obj = yaml.safe_load(open(os.path.join(RAIZ_DATOS, "seguridad-objetivo.yml"), encoding="utf-8")) or {}
    for fn in funciones:
        fn["objetivo"] = int(obj.get(fn["id"], 3))
        fn["brecha"] = max(0, fn["objetivo"] - (fn["nivel"] or 1))
    brechas = sorted([dict(c, funcion=fn["nombre"], objetivo=fn["objetivo"]) for fn in funciones for c in fn["categorias"] if (c["nivel"] or 1) < fn["objetivo"]],
                     key=lambda c: ((c["nivel"] or 1) - c["objetivo"], c["pct"] or 0))
    actual = round(sum(fn["pct"] for fn in funciones) / len(funciones))
    nodos = [{"id": f"e{x['id']}", "eid": x["id"], "label": x["nombre"], "zona": x["zona"], "riesgo": x["riesgo"], "cat": x["cat"]} for x in m["equipos"]]
    nodos += [{"id": x["id"], "seg": x["nombre"], "label": x["nombre"], "zona": "aplicaciones", "riesgo": x["riesgo"], "cat": "aplicación"} for x in m["segmentos"]]
    por_nombre = {x["nombre"]: f"e{x['id']}" for x in m["equipos"]}
    aristas = [{"s": por_nombre[f["desde"]], "t": por_nombre[f["hacia"]], "tls": f["tls"], "p": f.get("puerto")} for f in m["flujos"]
               if f["desde"] in por_nombre and f["hacia"] in por_nombre]
    aristas += [{"s": f"e{i}", "t": x["id"], "seg": True} for x in m["segmentos"] for i in x["maquinas"]]
    return render_template("seguridad_mapa.html", m=m, funciones=funciones, brechas=brechas, actual=actual, NIVELES=SM.NIVELES,
                           grafo={"nodos": nodos, "aristas": aristas, "zonas": SM.ZONAS}, nivel_actual=SM.nivel(actual))


@bp.route("/monitoreo/arquitectura-modular")
@requiere_login
def arquitectura_modular():
    """Cómo se separa FaceOps en un núcleo y módulos de dominio, cada uno en su repositorio con su equipo dueño."""
    import fnmatch
    import yaml as _y
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    conf = _y.safe_load(open(os.path.join(base, "datos", "modulos.yml"))) or {}
    disp = {"app": sorted(os.listdir(os.path.join(base, "app"))), "templates": sorted(os.listdir(os.path.join(base, "app", "templates"))),
            "aap": sorted(x[:-4] for x in os.listdir(os.path.join(base, "aap")) if x.endswith(".yml"))}
    usados = {k: set() for k in disp}
    def medir(m):
        out = {}
        for k in disp:
            pats = [p_ if k != "app" or p_.endswith(".py") else p_ for p_ in (m.get(k) or [])]
            xs = sorted({x for p_ in pats for x in disp[k] if fnmatch.fnmatch(x, p_)})
            usados[k].update(xs)
            out[k] = xs
        return out
    from .grupos_dueno import grupos as _grupos
    try:
        gr = {x.get("rol") or x.get("nombre"): x for x in _grupos(g.cliente["id"])}
    except Exception:  # noqa: BLE001
        gr = {}
    if not g.usuario.get("es_admin") or __import__("app.semillas", fromlist=["x"]).limpia():   # vista privada (solo laboratorio): el avance y la estrategia de la primera versión
        abort(404)
    from .pruebas import estado as _est_p
    planes = set((_y.safe_load(open(os.path.join(base, "datos", "planes-prueba.yml"))) or {}).get("planes") or {})
    autos_pb = {}
    for a_ in q("SELECT * FROM automatizaciones WHERE cliente_id=?", g.cliente["id"]):
        pb_ = os.path.basename(j(a_.get("tecnico"), {}).get("playbook") or "").removesuffix(".yml").removesuffix(".yaml")
        if pb_:
            autos_pb.setdefault(pb_, []).append(a_)
    SEP = {"por separar": 0, "repositorio creado": 50, "separado": 100}
    def avance(m, arch):
        acc = [a_ for pb_ in arch["aap"] for a_ in autos_pb.get(pb_, [])]
        cert = sum(1 for a_ in acc if _est_p(a_)["estado"] == "certificada")
        con_plan = sum(1 for a_ in acc if a_["nombre_aap"] in planes)
        sep = SEP.get(m.get("separacion") or "por separar", 0)
        p_cert = round(100 * cert / len(acc)) if acc else 100
        p_plan = round(100 * con_plan / len(acc)) if acc else 100
        return {"pct": round(p_cert * 0.5 + p_plan * 0.25 + sep * 0.25), "acciones": len(acc), "certificadas": cert, "con_plan": con_plan,
                "p_cert": p_cert, "p_plan": p_plan, "sep": sep, "separacion": m.get("separacion") or "por separar",
                "faltan": sorted(a_["nombre"] for a_ in acc if _est_p(a_)["estado"] != "certificada")[:40]}
    nucleo = dict(conf.get("nucleo") or {}, archivos=medir(conf.get("nucleo") or {}))
    nucleo["avance"] = avance(nucleo, nucleo["archivos"])
    mods = []
    for m in conf.get("modulos") or []:
        ar_ = medir(m)
        mods.append(dict(m, archivos=ar_, grupo=gr.get(m.get("dueno")), avance=avance(m, ar_)))
    sin = {k: [x for x in disp[k] if x not in usados[k] and not x.startswith(("__pycache__", "."))] for k in disp}
    return render_template("arquitectura_modular.html", nucleo=nucleo, mods=mods, sin=sin, total={k: len(v) for k, v in disp.items()})


@bp.route("/monitoreo/kpi-okr")
@requiere_login
def kpi_okr():
    ind = _indicadores()
    import yaml
    claves = list((yaml.safe_load(open(os.path.join(RAIZ_DATOS, "kpi-okr.yml"), encoding="utf-8")) or {}).keys())
    return render_template("kpi_okr.html", bloques=[_kpi_de(k, ind["v"]) for k in claves])


@bp.route("/monitoreo/itil")
@requiere_login
def itil():
    """Monitoreo › ITIL (temporal): cuánto cumple FaceOps cada práctica de ITIL 4, medido con datos reales donde se puede."""
    import yaml
    v = _indicadores()["v"]
    ps = (yaml.safe_load(open(os.path.join(RAIZ_DATOS, "itil.yml"), encoding="utf-8")) or {}).get("practicas") or []
    for p in ps:
        for c in p["criterios"]:
            if "valor" in c:
                x = v.get(c["valor"])
                c["actual"] = x
                c["ok"] = None if x is None else (x <= c["maximo"] if "maximo" in c else x >= c.get("minimo", 0))
            else:
                c["ok"] = bool(c.get("cumple"))
        med = [c for c in p["criterios"] if c["ok"] is not None]
        p["pct"] = round(100 * sum(1 for c in med if c["ok"]) / len(med)) if med else None
        p["sin_dato"] = sum(1 for c in p["criterios"] if c["ok"] is None)
    vals = [p["pct"] for p in ps if p["pct"] is not None]
    grupos = {}
    for p in ps:
        grupos.setdefault(p["grupo"], []).append(p)
    return render_template("itil.html", grupos=grupos, total=round(sum(vals) / len(vals)) if vals else None, n=len(ps))


@bp.route("/monitoreo/aprobaciones-pendientes")
@requiere_login
def aprobaciones_pendientes():
    """Aprobaciones de AAP que esperan respuesta (lo que quedó del antiguo panel de alertas)."""
    from .alertas import leer
    dias = request.args.get("dias", "7")
    dias = int(dias) if dias in ("1", "7", "30") else 7
    try:
        r = leer(g.cliente, dias)
    except ErrorAAP as e:
        r = {"alertas": [], "errores": [str(e)], "cuenta": {}}
    origen = "aprobacion"
    lista = [x for x in r["alertas"] if x["origen"].startswith(origen)]
    por_origen = {o: sum(1 for x in r["alertas"] if x["origen"].startswith(o)) for o in ("", "eda", "trabajo", "aprobacion")}
    from .alertas import clave as clave_alerta
    for x in lista:
        x["clave"] = clave_alerta(x)
    return render_template("alertas.html", r=r, lista=lista, dias=dias, origen=origen, por_origen=por_origen)


@bp.route("/alertas/eliminar", methods=["POST"])
@requiere_login
def alertas_eliminar():
    """Elimina alertas de la vista (no borra nada en AAP): si vuelve a fallar después, la alerta reaparece."""
    from .alertas import descartar
    _escritura()
    claves = [k for k in request.form.getlist("clave") if k][:500]
    descartar(g.cliente["id"], claves, g.usuario["usuario"])
    auditar(g.usuario["usuario"], g.cliente["id"], "eliminar alertas", f"{len(claves)} alerta(s)", {"claves": claves[:20]})
    flash(f"{len(claves)} alerta{'s' if len(claves) != 1 else ''} eliminada{'s' if len(claves) != 1 else ''}: si vuelven a fallar, reaparecen", "ok")
    return redirect(request.referrer or url_for("consola.aprobaciones_pendientes"))


@bp.route("/certificados")
@requiere_login
def certificados():
    from .certificados_panel import leer
    r = leer(g.cliente, equipos_visibles())
    vista = "todos"
    filas = r["filas"]
    renovar = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "Certificados · Renovar por vencer")
    emitir = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "Certificados · Emitir para servicio")
    maq_cert = sorted([e for e in equipos_visibles() if (e["categoria"] or "servidor") == "servidor" and e["ip"] and e["estado"] != "retirado"
                       and "windows" not in (e["so"] or "").lower()], key=lambda e: e["nombre"])
    return render_template("certificados.html", maq_cert=maq_cert, r=r, filas=filas, vista=vista, renovar=renovar, emitir=emitir,
                           cuenta={"servicios": sum(1 for f in r["filas"] if f["tipo"] == "servidor" and f["estado"] not in ("reemplazado", "revocado")),
                                   "historial": sum(1 for f in r["filas"] if f["estado"] in ("reemplazado", "revocado")), "todos": len(r["filas"])})


@bp.route("/certificados/<serie>")
@requiere_login
def certificado(serie):
    from .certificados_panel import leer, buscar_serie, afectadas
    eqs = equipos_visibles()
    r = leer(g.cliente, eqs)
    f = buscar_serie(r, serie)
    if not f:
        abort(404)
    renovar = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "Certificados · Renovar por vencer")
    emitir = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], "Certificados · Emitir para servicio")
    return render_template("certificado.html", f=f, r=r, maquinas=afectadas(f, eqs), renovar=renovar, emitir=emitir)


@bp.route("/equipos/<int:eid>/certificados")
@requiere_login
def equipo_certificados(eid):
    from .certificados_panel import leer, de_equipo
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    try:
        r = leer(g.cliente, equipos_visibles())
    except Exception:  # noqa: BLE001
        return {"certificados": [], "error": "No se pudo leer el inventario de certificados", "solicitar": _solicitar_certificado(e)}
    return {"certificados": [{"cn": f["cn"], "servicio": f["servicio"], "estado": f["estado"], "dias": f["dias"], "nivel": f["nivel"],
                                  "url": url_for("consola.certificado", serie=f["serie"])} for f in de_equipo(r, e["nombre"])],
            "solicitar": _solicitar_certificado(e)}


def _solicitar_certificado(e):
    """Cómo pedir un certificado para este equipo: OpenShift lo emite directo (rutas *.apps); una máquina abre el formulario
    de «Emitir para servicio» (hay que decir para qué servicio y puerto). None si no aplica o el perfil no puede."""
    from .operacion import aplica_a_equipo
    if g.perfil["modo"] != "escritura":
        return None
    for nombre, directo in (("Certificados · OpenShift", True), ("Certificados · Emitir para servicio", False)):
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=? AND visible=1", g.cliente["id"], nombre)
        if a and aplica_a_equipo(g.usuario, g.cliente["id"], a, e):
            return {"nombre": nombre, "directo": directo, "url": url_for("consola.automatizacion", aid=a["id"]),
                    "ejecutar": url_for("consola.ejecutar_en_equipo", eid=e["id"], aid=a["id"]) if directo else None,
                    "detalle": "Certificado de la CA interna para las rutas *.apps del clúster (consola, OAuth y aplicaciones)" if directo
                    else "Certificado de la CA interna para un servicio de la máquina (se indica el servicio y su puerto)"}
    return None


@bp.route("/ciclo-vida")
@requiere_login
def ciclo_vida():
    from .ciclo_vida import CLASES, FASES, panel
    eqs = [dict(e, _datos=j(e["datos"], {})) for e in equipos_visibles()]
    r = panel(eqs, cliente_id=g.cliente["id"])
    fase, clase = request.args.get("fase", ""), request.args.get("clase", "")
    base = [x for x in r["grupos"] if not clase or x["clase"] == clase]
    cuenta_fase = {k: sum(1 for x in base if x["fase"] == k or (k == "riesgo" and x["riesgo"])) for k in ("riesgo", "fuera", "extendido", "mantencion", "completo", "desconocido")}
    grupos = [x for x in base if not fase or x["fase"] == fase or (fase == "riesgo" and x["riesgo"])]
    return render_template("ciclo_vida.html", r=r, grupos=grupos, fase=fase, clase=clase, CLASES=CLASES, FASES=FASES, cuenta_fase=cuenta_fase, n_base=len(base))


@bp.route("/ciclo-vida/<clase>/<producto>/<path:version>")
@requiere_login
def ciclo_vida_version(clase, producto, version):
    from .ciclo_vida import CLASES, FASES, panel, ruta_actualizacion, datos as datos_cv
    eqs = [dict(e, _datos=j(e["datos"], {})) for e in equipos_visibles()]
    r = panel(eqs, cliente_id=g.cliente["id"])
    infra = request.args.get("infra", "")
    gr = next((x for x in r["grupos"] if x["clase"] == clase and x["producto"] == producto and str(x["version"]) == version and x["infra"] == infra), None)
    if not gr:
        abort(404)
    prod = datos_cv().get(producto, {})
    fechas = (prod.get("versiones") or {}).get(gr["version"]) or {}
    return render_template("ciclo_vida_version.html", gr=gr, r=r, FASES=FASES, fechas=fechas, clase_nombre=dict(CLASES).get(clase, ""),
                           ruta=ruta_actualizacion(producto, gr["version"]),
                           otras=sorted(((k, v) for k, v in (prod.get("versiones") or {}).items()), key=lambda kv: [int(x) for x in re.findall(r"\d+", str(kv[0]))]))


@bp.route("/ciclo-vida/<clase>/<producto>/<path:version>/evaluar", methods=["POST"])
@requiere_login
def ciclo_vida_evaluar(clase, producto, version):
    """«Evaluar destino» en una caja de la ruta: un ticket por máquina del grupo con la automatización que corresponde a la
    plataforma (Leapp para RHEL, JBoss → EAP 8, versión del clúster para OpenShift o la evaluación genérica de destino)."""
    from .ciclo_vida import EVALUAR_DESTINO, panel, ruta_actualizacion
    from .operacion import solicitar_en_equipo
    _escritura()
    eqs = [dict(e, _datos=j(e["datos"], {})) for e in equipos_visibles()]
    infra = request.form.get("infra", "")
    gr = next((x for x in panel(eqs, cliente_id=g.cliente["id"])["grupos"] if x["clase"] == clase and x["producto"] == producto
               and str(x["version"]) == version and x["infra"] == infra), None)
    destino = request.form.get("destino", "")
    ruta = ruta_actualizacion(producto, version) if gr else None
    vuelta = url_for("consola.ciclo_vida_version", clase=clase, producto=producto, version=version, infra=infra or None)
    if not ruta or destino not in {x["version"] for x in ruta.get("versiones", []) if x["alcanzable"]}:
        flash("Ese destino no es alcanzable desde esta versión en un salto", "error")
        return redirect(vuelta)
    nombre, params = EVALUAR_DESTINO.get(producto, ("Ciclo de vida · Evaluar destino",
                                                    lambda v: {"cv_producto": producto, "cv_actual": version, "cv_destino": v}))
    params = params(destino)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], nombre)
    if not a:
        flash(f"Falta «{nombre}» en el catálogo del cliente", "error")
        return redirect(vuelta)
    hechos, errores = [], []
    for e in gr["equipos"]:
        try:
            t = solicitar_en_equipo(g.usuario, g.cliente, a, e, params, f"Evaluar {gr['nombre']} {version} → {destino} · {e['nombre']}")
            hechos.append(t["numero"])
        except (PermissionError, ErrorAAP, ValueError) as x:
            errores.append(f"{e['nombre']}: {x}")
    if hechos:
        flash(f"Evaluación de {gr['nombre']} {destino} con «{a['nombre']}»: {len(hechos)} ticket(s) · " + ", ".join(hechos), "ok")
    if errores:
        flash("No se pidió en: " + " · ".join(errores[:5]), "error")
    return redirect(vuelta)


@bp.route("/certificados/actualizar", methods=["POST"])
@requiere_login
def certificados_actualizar():
    """Pide a AAP una lectura nueva («Certificados · Inventario»); FaceOps no habla con la CA."""
    from .aap import AAP
    from .certificados_panel import INVENTARIO, _cache
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        abort(403)
    try:
        aap = AAP.de_cliente(g.cliente)
        jt = (aap.get("job_templates/", name=INVENTARIO).get("results") or [None])[0]
        if not jt:
            raise ErrorAAP(f"No existe «{INVENTARIO}» en AAP")
        jid = aap.lanzar("job", jt["id"], {"cert_avisar": True})
        auditar(g.usuario["usuario"], g.cliente["id"], "actualizar inventario de certificados", INVENTARIO, {"job": jid})
        _cache.pop(g.cliente["id"], None)
        flash(f"Lectura pedida a AAP (trabajo {jid}): en ~1 minuto se actualiza esta vista.", "ok")
    except ErrorAAP as e:
        flash(str(e), "error")
    return redirect(url_for("consola.certificados"))


@bp.route("/manifiestos/<nombre>.yml")
@requiere_login
def manifiesto_plantilla(nombre):
    import os
    if nombre not in ("openshift",):
        abort(404)
    r = make_response(open(os.path.join(os.path.dirname(__file__), "..", "datos", f"{nombre}-manifiesto.yml"), encoding="utf-8").read())
    r.headers["Content-Type"] = "text/yaml; charset=utf-8"
    r.headers["Content-Disposition"] = f'attachment; filename="{nombre}-manifiesto.yml"'
    return r


# ------------------------------------------------------------------ infraestructuras (on-premise y nubes)
def _infras_con_salud(infras):
    """Cada infraestructura con la salud consolidada de sus máquinas (x["res"])."""
    eqs_ = equipos_visibles()
    for x in infras:
        es_ = [e for e in eqs_ if (e.get("ubicacion") or "") == x["ubicacion"]]
        x["res"] = _resumen_grupo(es_) if es_ else None
    return infras


def _datos_plano():
    """Plano de control de la ejecución: instancias de AAP, nodos de ejecución por infraestructura y fuentes de eventos."""
    from .ejecucion import plano
    from .infraestructuras import lista
    eqs_ = equipos_visibles()
    infras = lista(g.cliente["id"], eqs_)
    eventos = []
    try:
        from .aap import AAP
        cli_ = AAP.de_cliente(g.cliente)
        instancias = cli_.todos("instances/")
        try:
            from .alertas import _eda
            from .ejecucion import fuentes_eventos
            eventos = fuentes_eventos(_eda(cli_, "activations/", page_size=200).get("results", []))
        except Exception:  # noqa: BLE001
            eventos = []
    except ErrorAAP:
        instancias = []
    return plano(g.cliente, eqs_, infras, instancias, eventos)


@bp.route("/arquitectura/manifiesto", methods=["GET", "POST"])
@requiere_login
def manifiesto_arquitectura():
    """Manifiesto de arquitectura en tres niveles: capas, conceptos y tecnologías aceptadas (con lo instalado y su ciclo de vida)."""
    from . import manifiesto as M
    if request.method == "POST" and request.form.get("accion") == "roadmap":
        _escritura()
        sk = request.form.get("skill", "")[:80]
        try:
            M.guardar_roadmap(g.cliente["id"], sk, request.form, g.usuario["usuario"])
            auditar(g.usuario["usuario"], g.cliente["id"], "roadmap de salida del manifiesto", sk,
                    {k: request.form.get(k) for k in ("decision", "reemplazo", "responsable", "fecha_objetivo", "avance")})
            flash("Roadmap actualizado: " + sk, "ok")
        except ValueError as x:
            flash(str(x), "error")
        return redirect(url_for("consola.manifiesto_arquitectura", nivel=3, _anchor="roadmap"))
    if request.method == "POST":
        _escritura()
        try:
            M.ajustar(g.cliente["id"], request.form.get("tecnologia", ""), request.form.get("estado", ""), request.form.get("versiones", ""),
                      request.form.get("motivo", ""), g.usuario["usuario"])
            auditar(g.usuario["usuario"], g.cliente["id"], "ajustar manifiesto de arquitectura", request.form.get("tecnologia", ""),
                    {"estado": request.form.get("estado"), "versiones": request.form.get("versiones"), "motivo": request.form.get("motivo")})
            flash("Manifiesto actualizado: " + request.form.get("tecnologia", ""), "ok")
        except ValueError as x:
            flash(str(x), "error")
        return redirect(url_for("consola.manifiesto_arquitectura", nivel=3, _anchor="t-" + request.form.get("tecnologia", "")))
    nivel = request.args.get("nivel", "1")
    nivel = nivel if nivel in ("1", "2", "3") else "1"
    m = M.manifiesto(g.cliente["id"])
    eqs = equipos_visibles()
    fuera = M.fuera_del_manifiesto(g.cliente["id"], eqs)   # con su roadmap de salida (resumen en todos los niveles)
    rm = M.roadmap(g.cliente["id"], fuera)
    if nivel == "3":
        from . import stack as S
        r = S.ultimo(g.cliente["id"]) or S.evaluar(g.cliente["id"], {})
        M.con_instalado(m, eqs, r.get("filas"))
    apps = [x["nombre"] for x in segmentos_visibles(g.usuario, g.cliente["id"]) if __import__("app.grupos_segmento", fromlist=["x"]).grupo_de(x) == "aplicaciones"]
    cuenta = Counter(t["estado"] for t in M.tecnologias(m))
    aceptadas = sorted({t["nombre"] for t in M.tecnologias(m) if t.get("estado") in ("aceptada", "evaluacion")})
    return render_template("manifiesto.html", m=m, nivel=nivel, estados=M.ESTADOS, fuera=fuera, apps=apps, cuenta=cuenta, rm=rm,
                           salida=M.SALIDA, avance=M.AVANCE, aceptadas=aceptadas)


@bp.route("/fabrica/politicas", methods=["GET", "POST"])
@bp.route("/arquitectura/politicas-desarrollo", methods=["GET", "POST"])
@requiere_login
def politicas_desarrollo():
    """Gobierno › Fábrica de software › Políticas de software: lo que todo desarrollo debe contener en OpenShift (por ahora Spring
    Boot); las usa el agente de la Fábrica para generar desarrollos y el pipeline verifica las automáticas."""
    from . import politicas_dev as PDV
    if request.method == "POST":
        _escritura()
        try:
            cod = PDV.guardar(g.cliente["id"], request.form, g.usuario["usuario"])
            auditar(g.usuario["usuario"], g.cliente["id"], "política de desarrollo", cod, {"titulo": request.form.get("titulo"), "motivo": request.form.get("motivo")})
            flash(f"Política {cod} guardada (nueva versión para el agente)", "ok")
        except ValueError as x:
            flash(str(x), "error")
        return redirect(url_for("consola.politicas_desarrollo", _anchor="p-" + (request.form.get("codigo") or "")))
    ps = PDV.lista(g.cliente["id"])
    f_area = request.args.get("area", "")
    cats = {}   # por área dueña (hacia el desarrollo)
    for p_ in ps:
        if not f_area or p_["area"] == f_area:
            cats.setdefault(p_["area"] or "Arquitectura", []).append(p_)
    ver = request.args.get("ver", "")
    hist = PDV.historial(g.cliente["id"], ver) if ver else []
    return render_template("politicas_desarrollo.html", cats=cats, ps=ps, niveles=PDV.NIVELES, verifica=PDV.VERIFICA, areas=PDV.AREAS, f_area=f_area,
                           contrato=PDV.contrato(g.cliente["id"]), ver=ver, hist=hist,
                           fabrica_url=os.environ.get("FACEOPS_FABRICA_URL", "") and url_for("consola.fabrica_abrir"))


@bp.route("/fabrica/desarrollo")
@requiere_login
def fabrica_desarrollo():
    """Gobierno › Fábrica de software › Desarrollo: el chat de la Fábrica (solo OpenShift) dentro de FaceOps."""
    return render_template("fabrica_desarrollo.html", fabrica_url=os.environ.get("FACEOPS_FABRICA_URL", "") and url_for("consola.fabrica_app", ruta=""))


@bp.route("/fabrica/app/", defaults={"ruta": ""}, methods=["GET", "POST"])
@bp.route("/fabrica/app/<path:ruta>", methods=["GET", "POST"])
@requiere_login
def fabrica_app(ruta):
    """La Fábrica servida a través de FaceOps (mismo sitio: sin cookies de terceros en el navegador). FaceOps la llama de servidor
    a servidor con el token compartido y el usuario de la sesión; el navegador nunca ve el token."""
    url, tok = os.environ.get("FACEOPS_FABRICA_URL", "").rstrip("/"), os.environ.get("FACEOPS_FABRICA_TOKEN", "")
    if not url or not tok or not re.fullmatch(r"(api/[a-z]+(/[A-Za-z0-9-]+)?)?", ruta):
        abort(404)
    if request.method == "POST" and g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    try:
        r = requests.request(request.method, f"{url}/{ruta}", timeout=60, verify=False, data=request.get_data() if request.method == "POST" else None,
                             headers={"X-Fabrica-Token": tok, "X-Fabrica-Usuario": g.usuario["usuario"], "Content-Type": request.headers.get("Content-Type", "application/json")})
    except requests.RequestException as x:
        return {"error": f"La Fábrica no responde ({type(x).__name__})"}, 502
    if request.method == "POST" and ruta == "api/desplegar":
        auditar(g.usuario["usuario"], g.cliente["id"], "fábrica: desplegar en desarrollo", "fabrica", {"estado": r.status_code})
    cuerpo = r.content
    if not ruta and "text/html" in r.headers.get("Content-Type", ""):   # el chat manda el token CSRF de FaceOps en sus POST
        cuerpo = cuerpo.replace(b"</head>", f'<script>window.FO_CSRF={json.dumps(session.get("csrf", ""))};</script></head>'.encode(), 1)
    return cuerpo, r.status_code, {"Content-Type": r.headers.get("Content-Type", "application/json")}


@bp.route("/arquitectura/fabrica/abrir")
@requiere_login
def fabrica_abrir():
    """Entra a la Fábrica de código con un enlace firmado de 60 segundos (la Fábrica no tiene usuarios propios)."""
    import hashlib
    import hmac
    import time
    url, tok = os.environ.get("FACEOPS_FABRICA_URL", "").rstrip("/"), os.environ.get("FACEOPS_FABRICA_TOKEN", "")
    if not url or not tok:
        flash("La Fábrica de código aún no está conectada a esta instancia", "error")
        return redirect(url_for("consola.politicas_desarrollo"))
    t = str(int(time.time()))
    f = hmac.new(tok.encode(), f"{g.usuario['usuario']}|{t}".encode(), hashlib.sha256).hexdigest()
    auditar(g.usuario["usuario"], g.cliente["id"], "entrar a la Fábrica de código", "fabrica")
    from urllib.parse import urlencode
    return redirect(f"{url}/entrar?" + urlencode({"u": g.usuario["usuario"], "t": t, "f": f}))


@bp.route("/arquitectura/politicas-desarrollo.md")
@requiere_login
def politicas_desarrollo_md():
    from . import politicas_dev as PDV
    return PDV.markdown(g.cliente["id"]), 200, {"Content-Type": "text/markdown; charset=utf-8"}


@bp.route("/gobierno/plano-de-control")
@requiere_login
def plano_control():
    return render_template("plano_control.html", pl=_datos_plano())


@bp.route("/infraestructuras", methods=["GET", "POST"])
@requiere_login
def infraestructuras():
    from .infraestructuras import PROVEEDORES, incorporar, lista
    if request.method == "POST":
        if g.perfil["modo"] != "escritura":
            abort(403)
        try:
            if request.form.get("tipo") == "fisico":   # servidores físicos de a uno, agrupados por fabricante
                from .infraestructuras import agregar_fisico
                n, nuevo = agregar_fisico(g.usuario, g.cliente, request.form)
                flash(f"{request.form.get('srv_nombre')} agregado a «{n}»" + (" (grupo nuevo: credencial e inventario creados en AAP)" if nuevo else ""), "ok")
                return redirect(url_for("consola.infraestructuras"))
            n = incorporar(g.usuario, g.cliente, request.form)
            flash(f"Infraestructura «{n}» incorporada: credencial e inventario creados en AAP. Usa «Probar y tomar sus máquinas».", "ok")
        except (ValueError, ErrorAAP) as e:
            flash(str(e), "error")
        v_ = request.form.get("volver", "")
        return redirect(v_ if v_.startswith("/") and not v_.startswith("//") else url_for("consola.infraestructuras"))
    from .infraestructuras import AMBITOS
    eqs_ = equipos_visibles()
    infras = lista(g.cliente["id"], eqs_)
    for x in infras:   # salud consolidada y recursos: capacidad total, asignado a las máquinas y disponible
        es_ = [e for e in eqs_ if (e.get("ubicacion") or "") == x["ubicacion"] and x["unidad"] == "equipo"]
        x["res"] = _resumen_grupo(es_) if es_ else None
        cap = x["detalle_d"].get("capacidad") or {}
        asig = {"cpu": sum(e.get("cpu") or 0 for e in es_), "ram": round(sum(e.get("ram_mb") or 0 for e in es_) / 1024),
                "ram_on": round(sum(e.get("ram_mb") or 0 for e in es_ if e.get("energia") == "encendido") / 1024),
                "disco": round(sum(sum(d.get("gb") or 0 for d in j(e.get("discos"), [])) for e in es_))}
        x["recursos"] = {"cap": cap, "asig": asig} if (cap or es_) else None
    ubis_ = {x["ubicacion"] for x in infras if x["unidad"] == "equipo"}
    radar_sec = _radar_de([e for e in eqs_ if (e.get("ubicacion") or "") in ubis_])
    con = request.args.get("conectar", type=int)
    inc_ = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='FaceOps · Incorporar máquinas'", g.cliente["id"])
    from .infraestructuras import NATURALEZAS, NUBE_PUBLICA, etiqueta_centro
    etiq_cd = {c: etiqueta_centro([x for x in infras if x["centro"] == c]) for c in {x["centro"] for x in infras}}
    orden = sorted(etiq_cd, key=lambda c: (etiq_cd[c] == "cloud", c))
    from .infraestructuras import FABRICANTES
    grupos_fis = [{"fabricante": j(x["detalle"], {}).get("fabricante", ""), "centro": x.get("centro_datos") or "", "nombre": x["nombre"]}
                  for x in q("SELECT * FROM infraestructuras WHERE cliente_id=? AND tipo='fisico'", g.cliente["id"])]
    return render_template("infraestructuras.html", fabricantes=FABRICANTES, grupos_fis=grupos_fis, inc_id=inc_["id"] if inc_ else None, radar_sec=radar_sec, infras=infras, proveedores=PROVEEDORES, ambitos=AMBITOS,
                           centros=orden, etiq_cd=etiq_cd, naturalezas=NATURALEZAS, nube_publica=NUBE_PUBLICA, boveda_ok=_boveda_disponible(),
                           nuevo=request.args.get("nuevo", ""), conectar=next((x for x in infras if x["id"] == con), None))


def _centros(infras, eqs_):
    """Centros de datos del cliente: sus infraestructuras, sus máquinas (las de cualquiera de sus infraestructuras o ubicadas
    en el centro), salud y recursos."""
    from .infraestructuras import etiqueta_centro
    out = []
    for cd in sorted({x["centro"] for x in infras}):
        grupo = [x for x in infras if x["centro"] == cd]
        ubis = {x["ubicacion"] for x in grupo if x["ubicacion"]}
        clus = {"ocp-" + x["detalle_d"]["cluster"] for x in grupo if x["tipo"] in ("openshift", "openshift-virt") and x["detalle_d"].get("cluster")}
        es_ = [e for e in eqs_ if (e.get("ubicacion") or "") in ubis or (e.get("ubicacion") or "").endswith(cd) or e.get("nombre") in clus]
        cap = [x["detalle_d"].get("capacidad") or {} for x in grupo if x["tipo"] == "kvm" and not x["detalle_d"].get("emulado")]
        out.append({"nombre": cd, "etiqueta": etiqueta_centro(grupo), "infras": grupo, "equipos": es_, "res": _resumen_grupo(es_) if es_ else None,
                    "cpu": sum(e.get("cpu") or 0 for e in es_), "ram": round(sum(e.get("ram_mb") or 0 for e in es_) / 1024),
                    "region": next((x["region"] for x in grupo if x.get("region")), ""), "cap": cap[0] if cap else {}})
    return sorted(out, key=lambda c: (c["etiqueta"] == "cloud", c["nombre"]))


@bp.route("/infraestructuras/centros")
@bp.route("/infraestructuras/centros/<path:centro>")
@requiere_login
def centros_datos(centro=None):
    """Centros de datos: una caja por centro (on-premise y nube) y, al entrar, todas sus máquinas por infraestructura."""
    from .infraestructuras import lista
    eqs_ = equipos_visibles()
    cds = _centros(lista(g.cliente["id"], eqs_), eqs_)
    sel = next((c for c in cds if c["nombre"] == centro), None) if centro else None
    if centro and not sel:
        abort(404)
    if sel:
        por_inf = []
        vistos = set()
        for x in sel["infras"]:
            es_ = [e for e in sel["equipos"] if (e.get("ubicacion") or "") == x["ubicacion"] or (x["tipo"] in ("openshift", "openshift-virt") and e.get("nombre") == "ocp-" + (x["detalle_d"].get("cluster") or ""))]
            vistos |= {e["id"] for e in es_}
            por_inf.append((x, sorted(es_, key=lambda e: (e.get("energia") != "encendido", e["nombre"]))))
        otros = [e for e in sel["equipos"] if e["id"] not in vistos]
        if otros:
            por_inf.append(({"nombre": "Red y otros equipos del centro", "tipo": "red", "id": None, "prov": {"nombre": ""}, "detalle_d": {}}, otros))
        sel["por_inf"] = por_inf
    return render_template("centros_datos.html", cds=cds, sel=sel, TIPOS_HIPERVISOR=TIPOS_HIPERVISOR)


@bp.route("/arquitectura/licencias", methods=["GET", "POST"])
@requiere_login
def licencias():
    """Gobierno › Arquitectura › Licencias: contratos de todos los fabricantes, dónde está registrado cada equipo y el cruce
    caso a caso entre lo contratado y lo efectivamente cubierto."""
    from .licencias import AUTO_LEVANTAR, REGLAS, agregar, caducidad, cruce
    if request.method == "POST" and request.form.get("accion") == "levantar":
        _escritura()
        a_ = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], AUTO_LEVANTAR)
        if not a_:
            flash(f"Falta «{AUTO_LEVANTAR}» en el catálogo (sincroniza con AAP)", "error")
            return redirect(url_for("consola.licencias"))
        from .operacion import solicitar
        try:
            t = solicitar(g.usuario, g.cliente, a_, "*", {}, "Levantar las licencias en uso y su caducidad desde cada plataforma")
            flash(f"{t['numero']} creado: al aprobarse lee las licencias de cada máquina y actualiza la caducidad", "ok")
            return redirect(url_for("consola.ticket", tid=t["id"]))
        except (PermissionError, ErrorAAP, ValueError) as x:
            flash(str(x), "error")
        return redirect(url_for("consola.licencias"))
    if request.method == "POST":
        if g.perfil["modo"] != "escritura":
            abort(403)
        try:
            agregar(g.cliente["id"], request.form)
            auditar(g.usuario["usuario"], g.cliente["id"], "licencia agregada", request.form.get("producto", ""), {"cantidad": request.form.get("cantidad")})
            flash("Licencia agregada", "ok")
        except ValueError as e:
            flash(str(e), "error")
        return redirect(url_for("consola.licencias"))
    r = cruce(g.cliente["id"], equipos_visibles())
    f_fab, f_est = request.args.get("fabricante", ""), request.args.get("estado", "")
    casos = [c for c in r["casos"] if (not f_fab or c["fabricante"] == f_fab) and (not f_est or c["estado"] == f_est)]
    cad = caducidad(g.cliente, r["licencias"])
    return render_template("licencias.html", r=r, casos=casos, f_fab=f_fab, f_est=f_est, reglas=REGLAS, cad=cad)


@bp.route("/arquitectura/homologacion")
@bp.route("/arquitectura/homologacion/<gid>")
@requiere_login
def homologacion(gid=None):
    """Gobierno › Arquitectura › Homologación de ambientes: la misma pieza (segmento × plataforma) comparada entre
    desarrollo, QA y producción, con sus desviaciones, severidad y recomendación."""
    from .homologacion import ETIQ_AMB, grupos
    segs = [dict(x) for x in q("SELECT * FROM segmentos WHERE cliente_id=?", g.cliente["id"])]
    gs = grupos(equipos_visibles(), segs)
    sel = next((x for x in gs if x["id"] == gid), None) if gid else None
    if gid and not sel:
        abort(404)
    auto = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Homologación · Relevar entorno'", g.cliente["id"])
    return render_template("homologacion.html", gs=gs, sel=sel, ETIQ_AMB=ETIQ_AMB, auto=auto)


@bp.route("/infraestructuras/stack", methods=["GET", "POST"])
@requiere_login
def stack_tecnologico():
    """Evaluación del stack tecnológico: barrido de versiones (ticket en AAP) y nivel de obsolescencia frente al ciclo de vida."""
    from . import stack as S
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], S.AUTOMATIZACION)
    if request.method == "POST":
        _escritura()
        if not a:
            flash(f"Falta «{S.AUTOMATIZACION}» en el catálogo (sincroniza con AAP)", "error")
            return redirect(url_for("consola.stack_tecnologico"))
        from .operacion import solicitar
        try:
            t = solicitar(g.usuario, g.cliente, a, "*", {}, "Barrido del ciclo de vida y su obsolescencia")
            flash(f"{t['numero']} creado: al aprobarse barre todas las máquinas y arma el informe", "ok")
            return redirect(url_for("consola.ticket", tid=t["id"]))
        except (PermissionError, ErrorAAP, ValueError) as x:
            flash(str(x), "error")
        return redirect(url_for("consola.stack_tecnologico"))
    r = S.ultimo(g.cliente["id"])
    if not r:   # sin barrido aún: lo que FaceOps ya sabe del inventario
        r = dict(S.evaluar(g.cliente["id"], {}), preliminar=True)
    return render_template("stack.html", r=r, a=a)


@bp.route("/infraestructuras/stack/<int:sid>.pdf")
@requiere_login
def stack_pdf(sid):
    from . import stack as S
    x = q1("SELECT * FROM stack_eval WHERE id=? AND cliente_id=?", sid, g.cliente["id"])
    p = S.ruta_informe(sid) if x else None
    if not p or not os.path.exists(p):
        abort(404)
    from flask import send_file
    return send_file(p, mimetype="application/pdf", download_name=f"stack-tecnologico-{sid}.pdf")


@bp.route("/infraestructuras/stack/racionalizacion", methods=["GET", "POST"])
@requiere_login
def stack_racionalizacion():
    """Racionalización del stack: uso, soporte y superposición de cada tecnología → mantener, invertir, migrar, reemplazar o retirar."""
    from . import racionalizacion as R
    from . import stack as S
    if request.method == "POST":
        _escritura()
        try:
            R.decidir(g.cliente["id"], request.form.get("clave", ""), request.form, g.usuario["usuario"])
            auditar(g.usuario["usuario"], g.cliente["id"], "decisión de stack", request.form.get("clave", "")[:120], {"decision": request.form.get("decision")})
            flash("Decisión registrada.", "ok")
        except ValueError as x:
            flash(str(x), "error")
        return redirect(url_for("consola.stack_racionalizacion") + "#t-" + re.sub(r"[^a-z0-9]+", "-", request.form.get("clave", "").lower()))
    return render_template("stack_racionalizacion.html", cats=_racionalizacion_uso())


def _clasifica_uso(cpu, ram, dsk):
    """Sano entre 20 % y 80 %; bajo 20 % en todo, sobredimensionado (bajo 5 %, muy sobredimensionado); sobre 80 % en algo, subdimensionado."""
    vals = [v for v in (cpu, ram, dsk) if v is not None]
    if not vals:
        return "sin datos"
    if max(vals) > 80:
        return "subdimensionado"
    if max(vals) < 5:
        return "muy sobredimensionado"
    if max(vals) < 20:
        return "sobredimensionado"
    return "sano"


def _racionalizacion_uso():
    """Tecnologías del cliente por categoría del menú: instancias, recursos asignados, uso real y uso de sus funcionalidades."""
    import yaml as _y
    from datetime import datetime as _d, timedelta as _td, timezone as _tz
    from .asistente import PLATAFORMAS
    nombre = {c: n for c, n, _ in PLATAFORMAS}
    func = _y.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "funcionalidades.yml"), encoding="utf-8")) or {}
    desde = (_d.now(_tz.utc) - _td(days=90)).strftime("%Y-%m-%d")
    usadas = [x["nombre_aap"].lower() for x in q("SELECT DISTINCT a.nombre_aap FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
                                                  "WHERE t.cliente_id=? AND t.estado='exitoso' AND t.creado>=?", g.cliente["id"], desde)]
    uso = {}
    grupos = defaultdict(lambda: defaultdict(list))   # categoría → tecnología → equipos
    for e, r in _riesgo_parque():
        d_ = j(e.get("datos"), {})
        u_ = uso_equipo(e, d_, j(e.get("discos"), []))
        dk = u_["discos"] or []
        tot = sum(x.get("gb") or 0 for x in dk)
        uso[e["id"]] = {"cpu": None if "cpu" in u_["emulado"] else u_["cpu_u"], "ram": u_["ram_u"],
                        "dsk": round(sum((x.get("gb") or 0) * (x.get("uso") or 0) / 100 for x in dk) * 100 / tot) if tot else None, "disco_gb": tot}
        sec = seccion_eq(e)
        if sec in ("redes", "storage", "contenedores", "estaciones"):
            cat = {"redes": "Redes", "storage": "Storage", "contenedores": "Contenedores", "estaciones": "Estaciones de trabajo"}[sec]
            grupos[cat][(e.get("so") or e.get("categoria") or "Otro").split(" (")[0]].append((e, None))
            continue
        grupos["Sistemas operativos"][_familia_so(e.get("so"))].append((e, "rhel" if re.match(r"(red ?hat|rhel)", (e.get("so") or "").lower()) else None))
        for c in set(_plataformas_de(e, r)):
            if c.startswith("so:") or c in ("rhel", "windows-server"):
                continue
            grupos["Bases de datos" if c in MOTORES_BD else "Plataformas"][nombre.get(c, c)].append((e, c))
    orden = ["Sistemas operativos", "Plataformas", "Bases de datos", "Redes", "Storage", "Contenedores", "Estaciones de trabajo"]
    out = []
    for cat in orden:
        tecs = []
        for tn, lst in sorted(grupos.get(cat, {}).items(), key=lambda kv: (-len(kv[1]), kv[0])):
            es = list({e["id"]: e for e, _ in lst}.values())
            cod = next((c for _, c in lst if c), None)
            prom = lambda k: (lambda v: round(sum(v) / len(v)) if v else None)([uso[e["id"]][k] for e in es if uso[e["id"]][k] is not None])
            cpu, ram, dsk = prom("cpu"), prom("ram"), prom("dsk")
            fs = func.get(cod) or []
            en_uso = []
            for f_ in fs:
                tipo_, _, val_ = f_["detecta"].partition(":")
                ok_ = False
                for e in es:
                    sv_ = " ".join(j(e.get("servicios"), [])).lower()
                    if tipo_ == "servicio" and val_.lower() in sv_ or tipo_ == "skill" and val_ in (e.get("skills") or []):
                        ok_ = True
                    elif tipo_ == "dato":
                        k_, _, v_ = val_.partition("=")
                        ok_ = ok_ or str(j(e.get("datos"), {}).get(k_)).lower() == v_.lower()
                if tipo_ == "auto":
                    ok_ = any(val_.lower() in x for x in usadas)
                if ok_:
                    en_uso.append(f_["n"])
            tecs.append({"nombre": tn, "instancias": len(lst), "maquinas": len(es), "cpu_asig": sum(e.get("cpu") or 0 for e in es),
                         "ram_asig": round(sum(e.get("ram_mb") or 0 for e in es) / 1024, 1), "disco_asig": round(sum(uso[e["id"]]["disco_gb"] for e in es)),
                         "cpu": cpu, "ram": ram, "dsk": dsk, "dimension": _clasifica_uso(cpu, ram, dsk),
                         "func_total": len(fs), "func_uso": en_uso, "func_no": [f_["n"] for f_ in fs if f_["n"] not in en_uso],
                         "usabilidad": round(100 * len(en_uso) / len(fs)) if fs else None,
                         "equipos": [(e["id"], e["nombre"]) for e in sorted(es, key=lambda x: x["nombre"])]})
        if tecs:
            out.append({"cat": cat, "tecs": tecs})
    return out


@bp.route("/infraestructuras/arquitectura")
@requiere_login
def ecosistema():
    """Diagrama de alto nivel del ecosistema del cliente, armado desde el inventario."""
    from .ecosistema import armar
    from .infraestructuras import NUBE_PUBLICA, lista
    eqs_ = equipos_visibles()
    from .operacion import estado_servicenow
    from .flujo_arq import armar as armar_flujo
    return render_template("ecosistema.html", d=armar(eqs_, lista(g.cliente["id"], eqs_), grupo_periferico, clase_eq, NUBE_PUBLICA),
                           flujo=armar_flujo(eqs_, grupo_periferico, set(filter(None, request.args.get("plegar", "").split(",")))), sn=estado_servicenow(g.cliente),
                           integ=_integ_diagrama())


def _integ_diagrama():
    """Filas del diagrama: las integraciones operativas del cliente, una por tecnología (AAP destacada)."""
    from . import conexiones as cx
    filas = []
    try:
        for gr in cx.catalogo(g.cliente):
            ops = [x["nombre"] for x in gr["items"] if x["estado"] == "operativa"]
            if ops:
                filas.append((gr["titulo"], " · ".join(ops), "p-aap" if gr["clave"] == "automatizacion" else "p-faceops"))
    except Exception:  # noqa: BLE001
        pass
    return filas


def _boveda_disponible():
    from .boveda import disponible
    try:
        return disponible()
    except Exception:  # noqa: BLE001
        return False


@bp.route("/infraestructuras/<int:iid>/conectar", methods=["POST"])
@requiere_login
def infraestructura_conectar(iid):
    from .infraestructuras import conectar
    if g.perfil["modo"] != "escritura":
        abort(403)
    try:
        n = conectar(g.usuario, g.cliente, iid, request.form)
        flash(f"Credencial de «{n}» guardada cifrada en AAP. Ahora «Probar acceso».", "ok")
    except (ValueError, ErrorAAP) as e:
        flash(str(e), "error")
        return redirect(url_for("consola.infraestructuras", conectar=iid) + "#conectar")
    return redirect(url_for("consola.infraestructuras"))


@bp.route("/infraestructuras/<int:iid>/probar", methods=["POST"])
@requiere_login
def infraestructura_probar(iid):
    from .infraestructuras import probar
    if g.perfil["modo"] != "escritura":
        abort(403)
    try:
        st, msg = probar(g.usuario, g.cliente, iid)
        if st == "conectada":   # toma las máquinas que AAP ve en esa infraestructura
            try:
                n_ = sincronizar(g.cliente, g.usuario["usuario"])
                msg += f" · máquinas en FaceOps: {n_}"
            except ErrorAAP as e_:
                msg += f" · no se pudieron traer las máquinas: {e_}"
        flash(f"Acceso {'correcto' if st == 'conectada' else 'con error'}: {msg}", "ok" if st == "conectada" else "error")
    except (ValueError, ErrorAAP) as e:
        flash(str(e), "error")
    v_ = request.form.get("volver") or request.referrer or ""
    from urllib.parse import urlsplit
    pv_ = urlsplit(v_)
    return redirect(pv_.path + ("?" + pv_.query if pv_.query else "") if pv_.path.startswith("/") and pv_.netloc in ("", request.host) else url_for("consola.infraestructuras"))


# ------------------------------------------------------------------ menú lateral (grupos desplegables, como la consola de OpenShift)
DEF_ARGS = {"vista": "aplicaciones"}


def menu_nav():
    if (g.get("perfil") or {}).get("modo") == "ejecutivo":
        return [{"t": "Resumen del contrato", "ic": "tarifas", "url": url_for("consola.resumen"), "abierto": True,
                 "on": request.endpoint == "consola.resumen", "hijos": [
                     {"t": f"Últimos {n} meses" if n > 1 else "Este mes", "url": url_for("consola.resumen", meses=n),
                      "on": request.endpoint == "consola.resumen" and request.args.get("meses", "3") == str(n)} for n in (1, 3, 6, 12)]}]
    from .politicas_seg import CATEGORIAS as CATS_POL
    activo = {"consola.deudas": "consola.radar_salud", "consola.alertas": "consola.radar_salud", "consola.kpi_okr": "consola.radar_salud", "consola.itil": "consola.radar_salud", "consola.seguridad_mapa": "consola.politicas", "consola.arquitectura": "consola.mapa", "consola.arquitectura_nueva": "consola.mapa", "consola.equipo": "consola.inventario",
              "consola.ticket": "consola.radar_salud", "consola.incidente": "consola.politicas", "consola.incidente_conocido": "consola.politicas", "consola.incidentes_diagnostico": "consola.politicas", "consola.incidentes": "consola.politicas", "consola.automatizacion": "consola.automatizaciones",
              "consola.cves": "consola.politicas", "consola.politica": "consola.politicas", "consola.certificados": "consola.politicas", "consola.certificado": "consola.politicas", "consola.ciclo_vida": "consola.ecosistema", "consola.ciclo_vida_version": "consola.ecosistema", "consola.stack_tecnologico": "consola.ecosistema",
              "consola.tickets": "consola.radar_salud", "consola.aprobaciones_pendientes": "consola.radar_salud", "consola.aprobaciones_traza": "consola.radar_salud"}.get(request.endpoint, request.endpoint)
    ep_ = request.endpoint or ""
    if ep_.startswith(("consola.devsecops", "consola.buenas_practicas", "consola.stack_")):
        activo = "consola.ecosistema"
    elif ep_.startswith("consola.respaldo"):
        activo = "consola.disponibilidad"
    if request.endpoint == "consola.equipo" and (request.view_args or {}).get("eid"):
        e_m = q1("SELECT * FROM equipos WHERE id=? AND cliente_id=?", request.view_args["eid"], g.cliente["id"])
        if e_m:
            activo = SECCION_EP.get(seccion_eq(e_m), "consola.inventario")
    from .areas import SECCIONES, seccion_de
    con = {seccion_de(a) for a in autos_visibles()} | {"nube"}
    g_autos = [("Todas", {})] + [(n, {"area": k}) for k, n, _ in SECCIONES if k in con]   # las mismas secciones del menú
    hay_fis_ = any(seccion_eq(x) == "inventario" and (x.get("forma") or "") != "virtual" for x in equipos_visibles())
    g_inv = [("Virtuales", {"forma": "virtual"})] + ([("Físicos", {"forma": "fisico"})] if hay_fis_ else [])
    from .grupos_segmento import GRUPOS, grupo_de
    segs_v = segmentos_visibles(g.usuario, g.cliente["id"])
    sel = request.args.get("segmento")
    grupo_act = request.args.get("grupo") or (grupo_de(next((x for x in segs_v if x["nombre"] == sel), {})) if sel else "") \
        or ("redes" if request.args.get("vista") == "infra" else "aplicaciones")
    g_seg = [("Segmentos de negocio", {})]
    for gk, gn, gic, _ in GRUPOS:   # categorías dentro de Segmentos de negocio
        g_seg.append((gn, {"grupo": gk, "_titulo": True}))
        g_seg += [(x["nombre"], {"grupo": gk, "segmento": x["nombre"], "_sub": True}) for x in segs_v if grupo_de(x) == gk]
        if gk == "redes":
            g_seg.append(("Centros de datos y redes", {"grupo": gk, "vista": "infra", "_sub": True}))
    if g.perfil["modo"] == "escritura":
        g_seg.append(("+ Solicitar nuevo", {"_ep": "consola.arquitectura_nueva"}))
    secciones = [
                 ("consola.automatizaciones", "Acciones", "autos", g_autos),
                 ("consola.infraestructuras", "Infraestructura", "infra", [("Visión global", {}), ("Centros de datos", {"_ep": "consola.centros_datos"}), ("Arquitectura", {"_ep": "consola.ecosistema"})]),
                 ("consola.inventario", "Servidores", "inventario", g_inv),
                 ("consola.redes", "Redes", "redes", [("Todos los equipos de red", {})]),
                 ("consola.storage", "Storage", "storage", [("Todos los equipos de storage", {})]),
                 ("consola.sistemas_operativos", "Sistemas operativos", "so", [("Todos los sistemas", {})]),
                 ("consola.bases_datos", "Bases de datos", "bd", [("Todas las bases de datos", {})]),
                 ("consola.plataformas_vista", "Plataformas", "plataformas", [("Todas las plataformas", {})]),
                 ("consola.contenedores", "Contenedores", "contenedores", [("Todos los clústeres", {})]),
                 ("consola.estaciones", "Estaciones de trabajo", "estaciones", [("Todas las estaciones", {})]),
                 ("consola.hipervisores", "Hipervisores", "hipervisores", [("Todos los hipervisores", {})]),
                 ("consola.nubes", "Nubes", "nubes", [("Todas las máquinas en nube", {})]),
                 ("consola.mapa", "Segmentos de negocio", "apps", [("Todos", {"vista": "aplicaciones"})]   # un solo punto: se filtra por grupo y
                  + [(gn, {"vista": "aplicaciones", "grupo": gk}) for gk, gn, _, _ in GRUPOS if any(grupo_de(x) == gk for x in segs_v)]),   # cada perfil ve solo sus segmentos
                 ("consola.gobierno_ti", "Gobierno de TI", "gobierno", [("Árbol de gobierno", {}), ("Gobierno de Arquitectura", {"_ancla": "n2"}), ("Industria y país", {"_ancla": "n3"}), ("Plano de control", {"_ep": "consola.plano_control"})]),
                 ("consola.politicas", "Seguridad", "gobierno", [("Mapa de seguridad", {"_ep": "consola.seguridad_mapa"}), ("Escaneo de seguridad", {"_ep": "consola.escaneo_seguridad"}), ("Políticas", {}), ("CVEs", {"_ep": "consola.cves"}),
                                                                ("Certificados", {"_ep": "consola.certificados"}),
                                                                ("Incidentes", {"_ep": "consola.incidentes"}),
                                                                ("VPN y accesos remotos", {"_ep": "consola.vpn_seguridad"})]),
                 ("consola.disponibilidad", "Disponibilidad", "gobierno", [("Producción", {}), ("QA", {"ambiente": "qa"}), ("Desarrollo", {"ambiente": "desarrollo"}),
                                                                      ("Respaldos y recuperación", {"_ep": "consola.respaldos"})]),
                 ("consola.ecosistema", "Arquitectura", "segmentos", [("Manifiesto de arquitectura", {"_ep": "consola.manifiesto_arquitectura"}), ("Arquitectura", {}), ("Ciclo de vida", {"_ep": "consola.stack_tecnologico"}), ("Racionalización del stack", {"_ep": "consola.stack_racionalizacion"}), ("Licencias", {"_ep": "consola.licencias"}), ("Políticas de desarrollo", {"_ep": "consola.politicas_desarrollo"}), ("Homologación de ambientes", {"_ep": "consola.homologacion"}),
                                                                      ("DevSecOps", {"_ep": "consola.devsecops", "_titulo": True}), ("Herramientas de CI/CD", {"_ep": "consola.devsecops", "_sub": True}), ("Diseño de pipelines", {"_ep": "consola.devsecops_catalogo", "_sub": True}),
                                                                      ("Buenas prácticas por tecnología", {"_ep": "consola.buenas_practicas"})]),
                 ("consola.fabrica_desarrollo", "Fábrica de software", "segmentos", [("Desarrollo", {}), ("Políticas de software", {"_ep": "consola.politicas_desarrollo"})]),
                 ("consola.integraciones", "Integraciones", "integraciones", [("Todas las integraciones", {}), ("Herramientas de tickets", {"cat": "tickets"})]),
                 ("consola.radar_salud", "Monitoreo", "alertas", [("Radar de salud", {}), ("Alertas", {"_ep": "consola.alertas"}), ("Productividad", {"_ep": "consola.inicio", "_ancla": "sec-productividad"}), ("Tickets", {"_ep": "consola.tickets", "_titulo": True}), ("Todos", {"_ep": "consola.tickets", "_sub": True}),
                                                             ("En ejecución", {"_ep": "consola.tickets", "estado": "en_ejecucion", "_sub": True}), ("Exitosos", {"_ep": "consola.tickets", "estado": "exitoso", "_sub": True}),
                                                             ("Fallidos", {"_ep": "consola.tickets", "estado": "fallido", "_sub": True}),
                                                             ("Aprobaciones pendientes", {"_ep": "consola.aprobaciones_pendientes"}), ("Trazabilidad de aprobaciones", {"_ep": "consola.aprobaciones_traza"}),
                                                             ("Control de deuda", {"_ep": "consola.deudas"}), ("KPI y OKR", {"_ep": "consola.kpi_okr"}),
                                                             ("ITIL (temporal)", {"_ep": "consola.itil"})] + ([] if __import__("app.semillas", fromlist=["x"]).limpia() else [("Arquitectura modular", {"_ep": "consola.arquitectura_modular"})])),
                 ("consola.cuenta_datos", "Mi perfil", "clientes", [("Mis datos", {}), ("Cerrar sesión", {"_ep": "consola.salir"})])]
    out = []
    for ep, t, ic, hijos in secciones:
        it = {"t": t, "ic": ic, "url": url_for(ep), "abierto": activo == ep, "on": activo == ep, "hijos": []}
        claves = {k for _, a in (hijos or []) for k in a if not k.startswith("_")}
        actuales = {k: v for k in claves if (v := request.args.get(k)) and DEF_ARGS.get(k) != v}
        for ht, args in hijos or []:
            hep = args.get("_ep", ep)
            limpios = {k: v for k, v in args.items() if not k.startswith("_")}
            url = url_for(hep, **limpios) + ("#" + args["_ancla"] if "_ancla" in args else "")
            if hep == ep:
                on = request.endpoint == hep and "_ancla" not in args and {k: v for k, v in limpios.items() if DEF_ARGS.get(k) != v} == actuales
            else:   # hijo de otra página (p. ej. Seguridad › CVEs): calza por sus parámetros
                on = request.endpoint == hep and all(request.args.get(k) == v for k, v in limpios.items())
            it["hijos"].append({"t": ht, "url": url, "on": on, "titulo": args.get("_titulo"), "sub": args.get("_sub")})
        out.append(it)
        EP_MENU.setdefault(ep, set()).add(t)
        for _, a_ in hijos or []:
            EP_MENU.setdefault(a_.get("_ep", ep), set()).add(t)
    acc = g.get("acceso")
    if acc is not None:   # grupos y roles: solo las funcionalidades de sus espacios; Gobierno, solo cluster-admin/viewer
        out = [it for it in out if _menu_permitido(it["t"], acc)]
    return out


EP_MENU = {}   # endpoint → títulos del menú donde aparece (para cerrar el acceso directo por URL)


def _menu_permitido(t, acc):
    from . import rbac
    if acc.get("admin_total"):
        return True
    if t in rbac.MENU_GOBIERNO:
        return acc["gobierno"]
    if t in rbac.MENU_ESPACIO:
        return acc["gobierno"] or rbac.ve(acc, rbac.MENU_ESPACIO[t])
    if t == "Acciones":
        return bool(acc["ns"])
    return True


@bp.before_request
def _guardia_espacios():
    """Acceso directo por URL a una funcionalidad que el usuario no tiene en su menú: no."""
    acc = g.get("acceso")
    if acc is None or acc.get("admin_total") or not request.endpoint:
        return None
    ts = EP_MENU.get(request.endpoint)
    if request.endpoint.startswith("consola.configuracion") and not acc["gobierno"]:
        abort(403)
    if ts and not any(_menu_permitido(t, acc) for t in ts):
        abort(403)
    return None


# ------------------------------------------------------------------ incidentes: base de conocimiento por producto y versión
def _kb_contexto():
    from . import kb
    contratados = kb.productos_cliente(g.cliente["id"])
    return kb, contratados


@bp.route("/incidentes", methods=["GET", "POST"])
@requiere_login
def incidentes():
    """Errores conocidos como tickets y un buscador inteligente: se pega el log o se describe el error y aparecen los tickets
    con mayor probabilidad de solución. Cada uno trae su probabilidad de ocurrencia y sus acciones preventivas."""
    try:
        kb, contratados = _kb_contexto()
    except Exception as e:
        flash(str(e), "error")
        return render_template("incidentes.html", sin_kb=True, filas=[], texto="", buscando=False, recientes=[], prods=[], autos=[], orden="", prod_nombre={})
    if request.method == "POST":   # registrar un error conocido nuevo
        _escritura()
        f = request.form
        if f.get("producto") in contratados and f.get("titulo", "").strip():
            pid = kb.aprender(f["producto"], f["titulo"].strip(), f.get("sintomas", "")[:3000], f.get("causa", ""), f.get("solucion", ""),
                              f.get("automatizacion", ""), g.usuario["usuario"])
            if pid:
                kb.ej("UPDATE problemas SET prob_ocurrencia=%s, severidad=%s WHERE id=%s", float(f.get("ocurrencia") or 10), f.get("severidad", "media"), pid)
                auditar(g.usuario["usuario"], g.cliente["id"], "nuevo error conocido", f["titulo"], {"producto": f["producto"]})
                return redirect(url_for("consola.incidente_conocido", pid=pid))
            flash("Ya existe un error conocido con ese título en ese producto", "error")
        return redirect(url_for("consola.incidentes"))
    texto = (request.args.get("q") or "").strip()
    orden = request.args.get("orden", "ocurrencia")
    filas = kb.buscar(texto, contratados, limite=100) if texto else kb.conocidos(contratados, orden)
    if texto and len(filas) < 5:   # al escribir: también los que contienen todas las palabras (sin tildes ni mayúsculas)
        import unicodedata
        def _n(x):
            return "".join(ch for ch in unicodedata.normalize("NFD", (x or "").lower()) if unicodedata.category(ch) != "Mn")
        pal = [w for w in re.split(r"\W+", _n(texto)) if len(w) > 2]
        ya = {p_["id"] for p_ in filas}
        for p_ in kb.conocidos(contratados, orden):
            hay = _n(" ".join(str(p_.get(k) or "") for k in ("numero", "titulo", "sintomas", "causa", "solucion", "tipo", "producto_nombre")))
            if p_["id"] not in ya and pal and all(w in hay for w in pal):
                p_.update(coincidencia=50, prob_solucion=round(50 * float(p_.get("confianza") or 0.8)))
                filas.append(p_)
    _seguir_incidentes(kb)
    apl = _aplicabilidad_errores()
    for p_ in filas:   # a qué máquinas aplica y cómo está ese riesgo en el parque (semáforo)
        x_ = apl.get(p_["id"], {"maquinas": 0, "activas": 0, "prevenidas": 0})
        p_.update(apl_n=x_["maquinas"], apl_activas=x_["activas"], apl_prev=x_["prevenidas"],
                  salud="gris" if not x_["maquinas"] else ("rojo" if x_["activas"] else ("verde" if x_["prevenidas"] == x_["maquinas"] else "amarillo")))
    # solo errores de componentes presentes hoy en alguna máquina del inventario: si se desinstala, sale de la lista
    filas = [p_ for p_ in filas if p_["apl_n"]]
    tipos_err = Counter((p_.get("tipo") or "otro") for p_ in filas)
    tipo_sel = request.args.get("tipo", "")
    if tipo_sel:
        filas = [p_ for p_ in filas if (p_.get("tipo") or "otro") == tipo_sel]
    # cajas: plataformas presentes hoy en el inventario (productos y sistemas operativos), agrupadas; clic → detalle
    nombres_ = {p["codigo"]: p["nombre"] for p in kb.productos()}
    nombres_.update({"so:ubuntu": "Ubuntu", "so:windows": "Windows", "so:linux": "Linux (otras distribuciones)"})
    plat = request.args.get("plataforma", "")
    por_plat = defaultdict(list)
    for e_, r_ in _riesgo_parque():
        activos_ = {f["id"] for f in r_.get("errores", []) if f["activa"]}
        for c_ in _plataformas_de(e_, r_):
            por_plat[c_].append({"e": e_, "activos": len([f for f in r_.get("errores", []) if f["activa"] and f["producto"] == nombres_.get(c_)]),
                                 "riesgo": r_.get("riesgo", 0), "ids_activos": activos_})
    cajas = []
    for c_, eqs_ in por_plat.items():
        fs = [p_ for p_ in filas if p_.get("producto") == c_]
        grupo = next((k for k, _, cods in GRUPOS_PLATAFORMA if c_ in cods), "otros")
        cajas.append({"codigo": c_, "nombre": nombres_.get(c_, c_), "grupo": grupo, "maquinas": len(eqs_), "errores": len(fs),
                      "rojo": sum(1 for p_ in fs if p_["salud"] == "rojo"), "amarillo": sum(1 for p_ in fs if p_["salud"] == "amarillo"),
                      "activas": sum(1 for x in eqs_ if x["activos"]), "soporte": c_ in contratados})
    cajas.sort(key=lambda c: (-c["rojo"], -c["amarillo"], c["nombre"]))
    grupos = [(k, n, [c for c in cajas if c["grupo"] == k]) for k, n, _ in GRUPOS_PLATAFORMA + [("otros", "Otras plataformas", set())]]
    grupos = [x for x in grupos if x[2]]
    detalle = None
    if plat:
        filas = [p_ for p_ in filas if p_.get("producto") == plat]
        maq = sorted(por_plat.get(plat, []), key=lambda x: (-x["activos"], -x["riesgo"], x["e"]["nombre"]))
        detalle = {"codigo": plat, "nombre": nombres_.get(plat, plat), "maquinas": maq, "soporte": plat in contratados,
                   "caja": next((c for c in cajas if c["codigo"] == plat), None)}
    vista_cajas = not texto and not plat and not tipo_sel
    if request.headers.get("X-Requested-With") == "fetch":   # buscador en vivo: solo la tabla
        return render_template("incidentes.html", solo_tabla=True, filas=filas, texto=texto, buscando=bool(texto), tipos_err=tipos_err, tipo_sel=tipo_sel, sin_kb=False, prods=[1], autos=[], orden=orden,
                               grupos=grupos, vista_cajas=vista_cajas, detalle=detalle, plat=plat)
    return render_template("incidentes.html", tipos_err=tipos_err, tipo_sel=tipo_sel, sin_kb=False, filas=filas, texto=texto, buscando=bool(texto), orden=orden,
                           grupos=grupos, vista_cajas=vista_cajas, detalle=detalle, plat=plat,
                           recientes=kb.incidentes(g.cliente["id"], 15), prods=[p for p in kb.productos() if p["codigo"] in contratados],
                           prod_nombre={p["codigo"]: p["nombre"] for p in kb.productos()},
                           autos=q("SELECT nombre_aap, nombre FROM automatizaciones WHERE cliente_id=? AND visible=1 ORDER BY nombre", g.cliente["id"]))


# Agrupación de las plataformas del inventario en cajas (incidentes): producto de la base de errores conocidos → grupo
GRUPOS_PLATAFORMA = [
    ("so", "Sistemas operativos", {"rhel", "windows-server", "so:ubuntu", "so:windows", "so:linux"}),
    ("contenedores", "Contenedores y orquestación", {"openshift", "kubernetes", "podman", "docker"}),
    ("middleware", "Servidores de aplicación y web", {"jboss-eap", "iis", "nginx", "tomcat", "httpd", "apache"}),
    ("datos", "Bases de datos y caché", {"postgresql", "redis", "mysql", "mariadb", "oracle", "sqlserver", "mongodb"}),
    ("mensajeria", "Mensajería e integración", {"kafka", "activemq", "rabbitmq"}),
    ("gestion", "Gestión y acción", {"satellite"}),
    ("red", "Redes y acceso remoto", {"wireguard"}),
]


def _riesgo_parque():
    """[(equipo, evaluación de riesgo)] de las máquinas con datos: una sola pasada para cajas, aplicabilidad y detalle."""
    if "_riesgo_parque" in g:
        return g._riesgo_parque
    from .riesgo import evaluar as evaluar_riesgo
    out = []
    for e in equipos_visibles():
        if e["estado"] != "con datos":
            continue
        try:
            r = evaluar_riesgo(g.cliente["id"], e)
        except Exception:  # noqa: BLE001
            r = None
        out.append((e, r or {}))
    g._riesgo_parque = out
    return out


def _plataformas_de(e, r):
    """Códigos de plataforma del equipo: los productos de la base de errores y, si su sistema operativo no tiene producto
    propio, el sistema operativo (so:ubuntu, so:windows…) para que también aparezca su caja."""
    ps = list(r.get("productos") or [])
    # lo instalado sobre la máquina (sus skills de producto), no solo lo que conoce la base de errores; los agentes no cuentan
    from .inventario import SOPORTE as _sop
    _mapa = {"postgres": "postgresql", "jboss": "jboss-eap", "eap": "jboss-eap", "vpn": "wireguard", "mssql": "sqlserver", "amq": "activemq"}
    for sk_ in (e.get("skills") or []):
        if sk_ not in _sop and sk_ not in AGENTES_SK and sk_ not in ("acceso",):
            c_ = _mapa.get(sk_, sk_)
            if c_ not in ps:
                ps.append(c_)
    if (e.get("categoria") or "") == "clúster openshift" and "openshift" not in ps:
        ps.append("openshift")
    crudo = e.get("datos") if isinstance(e.get("datos"), str) else json.dumps(e.get("datos") or {})
    if "lake-s3" in crudo or "demo-datalake" in crudo:   # data lake: S3 en el storage y Trino en el clúster
        ps.append("data-lake")
    so = (e.get("so") or "").lower()
    if not any(p in ("rhel", "windows-server") for p in ps) and so:
        ps.append("so:ubuntu" if "ubuntu" in so else ("so:windows" if "windows" in so else
                  ("rhel" if ("red hat" in so or so.startswith("rhel")) else "so:linux")))
    return ps


def _aplicabilidad_errores():
    """{id de error conocido: {maquinas, activas, prevenidas, equipos}} sobre las máquinas visibles del cliente."""
    out = defaultdict(lambda: {"maquinas": 0, "activas": 0, "prevenidas": 0, "equipos": []})
    for e, r in _riesgo_parque():
        for f in r.get("errores", []):
            x = out[f["id"]]
            x["maquinas"] += 1
            x["activas"] += 1 if f["activa"] else 0
            x["prevenidas"] += 1 if (f.get("cobertura") or {}).get("estado") == "cubierto" else 0
            x["equipos"].append(e["id"])
    return out


@bp.route("/incidentes/conocido/<int:pid>", methods=["GET", "POST"])
@requiere_login
def incidente_conocido(pid):
    """Ticket del error conocido: causa, solución (automatización correctiva), probabilidad de ocurrencia y acciones preventivas
    que llevan a tareas automatizadas. Desde aquí se aplica la solución en un equipo (queda la ocurrencia y su ticket)."""
    from .economia import catalogo_tareas
    from .operacion import aplica_a_equipo, solicitar, solicitar_en_equipo
    kb, contratados = _kb_contexto()
    p = kb.conocido(pid)
    if not p or p["producto"] not in contratados:
        abort(404)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], p["automatizacion"]) if p["automatizacion"] else None
    eqs = [e for e in equipos_visibles() if a and (not a["usa_limite"] or aplica_a_equipo(g.usuario, g.cliente["id"], a, e))] if a else []
    if request.method == "POST" and request.form.get("accion") == "prevenir":   # aplicar la prevención (si pide aprobación y no se da, queda como deuda)
        _escritura()
        from .operacion import solicitar_en_equipo
        ap = q1("SELECT * FROM automatizaciones WHERE id=? AND cliente_id=?", int(request.form.get("auto") or 0), g.cliente["id"])
        e = next((x for x in equipos_visibles() if x["nombre"] == request.form.get("equipo")), None)
        try:
            if not ap or not e:
                raise ValueError("Acción o equipo no válidos")
            t = solicitar_en_equipo(g.usuario, g.cliente, ap, e, {}, f"Prevención · {p['numero']} {p['titulo']} · {e['nombre']}")
            flash(f"Prevención solicitada: «{ap['nombre']}» en {e['nombre']}" + (" · espera aprobación (si no se aprueba, queda como deuda)" if ap["requiere_aprobacion"] else ""), "ok")
        except (PermissionError, ErrorAAP, ValueError) as x:
            flash(str(x), "error")
        return redirect(url_for("consola.incidente_conocido", pid=pid))
    afectados_ids = set(_aplicabilidad_errores().get(pid, {}).get("equipos", []))
    if request.method == "POST" and request.form.get("accion") == "remediar":   # aplicar la remediación en una máquina afectada
        _escritura()
        e = next((x for x in equipos_visibles() if x["nombre"] == request.form.get("equipo") and x["id"] in afectados_ids), None)
        try:
            if not a or not e:
                raise ValueError("Sin acción de remediación o la máquina no está afectada por este error")
            t = solicitar_en_equipo(g.usuario, g.cliente, a, e, dict(p.get("parametros") or {}), f"Remediación · {p['numero']} {p['titulo']} · {e['nombre']}")
            kb.ocurrio(pid)
            flash(f"Remediación «{a['nombre']}» solicitada en {e['nombre']} · ticket {t['numero']}"
                  + (" (espera aprobación)" if a["requiere_aprobacion"] else ""), "ok")
        except (PermissionError, ErrorAAP, ValueError) as x:
            flash(str(x), "error")
        return redirect(url_for("consola.incidente_conocido", pid=pid) + "#afectadas")
    if request.method == "POST":   # paso 1: verificar en el equipo que el incidente es este error conocido
        _escritura()
        integrados = {x["equipo"] for x in kb.integraciones(g.cliente["id"]) if x["producto"] == p["producto"] and x["estado"] == "integrado"}
        e = next((x for x in equipos_visibles() if x["nombre"] == request.form.get("equipo") and (x["nombre"] in integrados or x["id"] in afectados_ids)), None)
        if not e:
            flash(f"Elige un equipo con {p['producto_nombre']} integrado al soporte", "error")
            return redirect(url_for("consola.incidente_conocido", pid=pid))
        num = kb.numero(g.cliente["id"], g.cliente["codigo"])
        iid = kb.ocurrencia_nueva(g.cliente["id"], num, pid, e["nombre"], p["producto"], (request.form.get("texto") or p["titulo"]).strip(),
                                  g.usuario["usuario"])
        kb.ocurrio(pid)
        _verificar(kb, iid, p, e)
        return redirect(url_for("consola.incidente", iid=iid))
    tareas, comp = catalogo_tareas()
    del_cliente = {x["nombre_aap"]: x for x in q("SELECT id, nombre, nombre_aap FROM automatizaciones WHERE cliente_id=? AND visible=1", g.cliente["id"])}
    prev = []
    for x in p["preventivas"] or []:
        autos_t = [del_cliente[n] for n, cods in comp.items() if x.get("tarea") in cods and n in del_cliente]
        prev.append({"accion": x.get("accion", ""), "tarea": tareas.get(x.get("tarea"), {}).get("nombre", x.get("tarea", "")), "autos": autos_t[:4]})
    historial = kb.q("SELECT * FROM incidentes WHERE cliente_id=%s AND problema_id=%s ORDER BY creado DESC LIMIT 20", g.cliente["id"], pid)
    integrados = {x["equipo"] for x in kb.integraciones(g.cliente["id"]) if x["producto"] == p["producto"] and x["estado"] == "integrado"}
    from .deuda import cobertura
    cob = {e_: cobertura(g.cliente["id"], e_, p) for e_ in sorted(integrados)}
    # máquinas afectadas (el error aplica por producto y versión), con su estado, para actuar caso a caso
    afectadas = []
    for e_, r_ in _riesgo_parque():
        f_ = next((f for f in r_.get("errores", []) if f["id"] == pid), None)
        if f_:
            afectadas.append({"e": e_, "activa": f_["activa"], "cobertura": f_.get("cobertura") or {}, "exposicion": f_.get("exposicion", 0),
                              "remediable": bool(a) and (not a["usa_limite"] or aplica_a_equipo(g.usuario, g.cliente["id"], a, e_))})
    afectadas.sort(key=lambda x: (not x["activa"], -x["exposicion"], x["e"]["nombre"]))
    return render_template("incidente_conocido.html", cob=cob, afectadas=afectadas, p=p, a=a, eqs=sorted((x for x in equipos_visibles() if x["nombre"] in integrados), key=lambda x: x["nombre"]), prev=prev,
                           historial=historial, rango=kb._rango)


def _verificar(kb, iid, p, e):
    """Lanza la verificación con Ansible (solo lectura) de las revisiones del error conocido en el equipo."""
    from .operacion import solicitar_en_equipo
    if not p["verificacion"]:
        flash("Este error conocido no tiene verificación automática: confírmalo a mano antes de remediar", "info")
        return
    av = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Incidentes · Verificar error conocido'", g.cliente["id"])
    if not av:
        flash("Falta la acción «Incidentes · Verificar error conocido» en el catálogo del cliente", "error")
        return
    try:
        i = kb.q1("SELECT numero FROM incidentes WHERE id=%s", iid)
        t = solicitar_en_equipo(g.usuario, g.cliente, av, e, {"kb_codigo": p["numero"], "kb_titulo": p["titulo"],
                                                              "kb_verificaciones": p["verificacion"]}, f"{i['numero']} · verificar {p['numero']}")
        kb.verificacion_lanzada(g.cliente["id"], iid, t["id"], t["numero"])
        flash(f"Verificando en {e['nombre']} con Ansible", "ok")
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")


def _seguir_incidentes(kb):
    from .aap import AAP

    def artefactos(t):
        try:
            return AAP.de_cliente(g.cliente).get(f"jobs/{t['aap_job_id']}/").get("artifacts") or {}
        except ErrorAAP:
            return {}
    kb.seguir_verificaciones(g.cliente["id"], lambda tid: q1("SELECT * FROM tickets WHERE id=?", tid), artefactos)
    kb.seguir_soporte(g.cliente["id"], lambda tid: q1("SELECT * FROM tickets WHERE id=?", tid), artefactos)
    kb.seguir_tickets(g.cliente["id"], lambda tid: (q1("SELECT estado FROM tickets WHERE id=?", tid) or {}).get("estado"))


@bp.route("/incidentes/<int:iid>", methods=["GET", "POST"])
@requiere_login
def incidente(iid):
    """Ocurrencia de un error conocido: 1) Ansible verifica en el equipo, 2) si se confirma, se aplica la remediación, 3) cierre."""
    from .operacion import solicitar, solicitar_en_equipo
    kb, contratados = _kb_contexto()
    _seguir_incidentes(kb)
    i = kb.incidente(g.cliente["id"], iid)
    if not i:
        abort(404)
    p = kb.conocido(i["problema_id"]) if i["problema_id"] else None
    e = next((x for x in equipos_visibles() if x["nombre"] == i["equipo"]), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], p["automatizacion"]) if p and p["automatizacion"] else None
    if request.method == "POST":
        _escritura()
        acc = request.form.get("accion")
        if acc == "verificar" and p and e:
            _verificar(kb, iid, p, e)
        elif acc == "confirmar" and i["estado"] in ("por verificar", "no confirmado"):
            kb.ej("UPDATE incidentes SET estado='confirmado', nota=%s WHERE id=%s", f"Confirmado a mano por {g.usuario['usuario']}", iid)
        elif acc == "aplicar" and i["estado"] == "confirmado" and a:
            motivo = f"{i['numero']} · remediar {p['numero']} {p['titulo']}"
            try:
                t = solicitar_en_equipo(g.usuario, g.cliente, a, e, dict(p["parametros"] or {}), motivo) if (e and a["usa_limite"]) \
                    else solicitar(g.usuario, g.cliente, a, None, dict(p["parametros"] or {}), motivo)
                kb.solucion_lanzada(g.cliente["id"], iid, t["id"], t["numero"])
                flash(f"Remediación en curso: «{a['nombre']}»", "ok")
            except (PermissionError, ErrorAAP, ValueError) as x:
                flash(str(x), "error")
        elif acc in ("resuelto", "no resuelto"):
            kb.cerrar(g.cliente["id"], iid, acc, request.form.get("nota", ""))
        auditar(g.usuario["usuario"], g.cliente["id"], f"incidente · {acc}", i["numero"], {})
        return redirect(url_for("consola.incidente", iid=iid))
    vt = q1("SELECT * FROM tickets WHERE id=?", i["verif_ticket_id"]) if i.get("verif_ticket_id") else None
    tk = q1("SELECT * FROM tickets WHERE id=?", i["ticket_id"]) if i["ticket_id"] else None
    from .deuda import cobertura
    cob = cobertura(g.cliente["id"], i["equipo"], p) if p and i["equipo"] else None
    return render_template("incidente.html", i=i, p=p, a=a, e=e, vt=vt, tk=tk, cob=cob)


# ------------------------------------------------------------------ resumen ejecutivo del contrato (perfil ejecutivo y administradores)
@bp.route("/resumen")
@requiere_login
def resumen():
    from .resumen_ejecutivo import calcular
    n = request.args.get("meses", "3")
    n = int(n) if n in ("1", "3", "6", "12") else 3
    return render_template("resumen.html", r=calcular(g.cliente, n), n=n)


@bp.route("/resumen/reporte.csv")
@requiere_login
def resumen_csv():
    import csv
    import io
    from .resumen_ejecutivo import calcular
    n = request.args.get("meses", "3")
    n = int(n) if n in ("1", "3", "6", "12") else 3
    r = calcular(g.cliente, n)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow([f"Resumen del contrato · {g.cliente['nombre']} · {r['meses'][0]} a {r['meses'][-1]}"])
    w.writerow(["Área", "Segmentos de negocio", "Ejecuciones", "Máquinas", "Créditos", f"Costo ({r['moneda']})", "Horas liberadas",
                f"Ahorro ({r['moneda']})", "Retorno (ahorro ÷ costo)", "% del beneficio"])
    for a in r["areas"]:
        w.writerow([a["area"], ", ".join(a["segmentos"]), a["ejecuciones"], a["maquinas"], str(a["creditos"]).replace(".", ","),
                    str(a["costo"]).replace(".", ","), str(a["horas"]).replace(".", ","), str(a["ahorro"]).replace(".", ","),
                    (f"{a['retorno']:.1f}".replace(".", ",") if a["retorno"] else ""), f"{a['pct']:.1f}".replace(".", ",")])
    w.writerow([])
    w.writerow(["Cargos fijos (base del producto y Managed Service)", "", "", "", str(r["creditos_fijos"]).replace(".", ","),
                str(round(r["creditos_fijos"] * r["precio"], 2)).replace(".", ",")])
    w.writerow(["Total", "", r["exitosos"], "", str(round(r["creditos_ejecuciones"] + r["creditos_fijos"], 2)).replace(".", ","),
                str(r["costo"]).replace(".", ","), str(r["horas"]).replace(".", ","), str(r["ahorro"]).replace(".", ","),
                (f"{r['retorno']:.1f}".replace(".", ",") if r["retorno"] else "")])
    w.writerow([])
    w.writerow(["Mes", "Créditos de automatizaciones", "Créditos fijos", "Horas liberadas", f"Ahorro ({r['moneda']})"])
    for m in r["por_mes"]:
        w.writerow([m["mes"], str(m["creditos"]).replace(".", ","), str(m["fijos"]).replace(".", ","), str(m["horas"]).replace(".", ","),
                    str(m["ahorro"]).replace(".", ",")])
    auditar(g.usuario["usuario"], g.cliente["id"], "descargar reporte del contrato", "resumen", {"meses": n})
    from flask import Response
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=faceops-resumen-{g.cliente['codigo']}-{r['meses'][-1]}.csv"})



# ------------------------------------------------------------------ soporte por producto: integración y diagnóstico con Ansible
def _conf_integraciones():
    import yaml
    try:
        return (yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "integraciones.yml"))) or {}).get("productos") or {}
    except OSError:
        return {}


def _soporte(kb, contratados):
    """Por producto con soporte contratado: equipos que lo tienen y su estado de integración (sin integración no hay soporte)."""
    from .inventario import skills_de
    conf = _conf_integraciones()
    nombres = {p["codigo"]: p["nombre"] for p in kb.productos()}
    integ = {(x["producto"], x["equipo"]): x for x in kb.integraciones(g.cliente["id"])}
    out = []
    for cod in contratados:
        c = conf.get(cod)
        eqs = [e for e in equipos_visibles() if c and c.get("skill") in set(skills_de(e))] if c else []
        out.append({"codigo": cod, "nombre": nombres.get(cod, cod), "disponible": bool(c), "describe": (c or {}).get("describe", ""),
                    "equipos": [{"nombre": e["nombre"], "ip": e.get("ip"), "integ": integ.get((cod, e["nombre"]))} for e in eqs]})
    out.sort(key=lambda x: (not x["disponible"], x["nombre"]))
    return out


def _lanzar_soporte(kb, producto, equipo, cual):
    from .operacion import solicitar_en_equipo
    c = _conf_integraciones().get(producto) or {}
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], c.get(cual, ""))
    e = next((x for x in equipos_visibles() if x["nombre"] == equipo), None)
    if not a or not e:
        raise ValueError("No está disponible la acción o el equipo")
    return solicitar_en_equipo(g.usuario, g.cliente, a, e, {}, f"Soporte {producto} · {'integración' if cual == 'integrar' else 'diagnóstico'} de {equipo}")


@bp.route("/incidentes/soporte", methods=["POST"])
@requiere_login
def incidentes_soporte():
    _escritura()
    kb, contratados = _kb_contexto()
    f = request.form
    producto, equipo, accion = f.get("producto", ""), f.get("equipo", ""), f.get("accion", "")
    if producto not in contratados:
        abort(403)
    try:
        if accion == "integrar":
            t = _lanzar_soporte(kb, producto, equipo, "integrar")
            kb.integracion_lanzada(g.cliente["id"], producto, equipo, t["id"])
            flash(f"Integrando {producto} en {equipo} con Ansible", "ok")
        elif accion == "diagnosticar":
            integ = next((x for x in kb.integraciones(g.cliente["id"]) if x["producto"] == producto and x["equipo"] == equipo and x["estado"] == "integrado"), None)
            if not integ:
                raise ValueError(f"{producto} no está integrado en {equipo}: intégralo primero")
            t = _lanzar_soporte(kb, producto, equipo, "diagnosticar")
            did = kb.diagnostico_lanzado(g.cliente["id"], producto, equipo, t["id"], g.usuario["usuario"])
            auditar(g.usuario["usuario"], g.cliente["id"], "diagnosticar producto", f"{producto} · {equipo}", {})
            return redirect(url_for("consola.incidentes_diagnostico", did=did))
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.incidentes") + "#soporte")


@bp.route("/incidentes/diagnostico/<int:did>", methods=["GET", "POST"])
@requiere_login
def incidentes_diagnostico(did):
    kb, contratados = _kb_contexto()
    _seguir_incidentes(kb)
    d = kb.diagnostico(g.cliente["id"], did)
    if not d:
        abort(404)
    if request.method == "POST":   # el diagnóstico ya es la verificación en el equipo: la ocurrencia nace confirmada
        _escritura()
        pid = int(request.form.get("problema") or 0)
        h = next((x for x in (d["hallazgos"] or []) if x["id"] == pid), None)
        p = kb.conocido(pid) if h else None
        if p:
            num = kb.numero(g.cliente["id"], g.cliente["codigo"])
            iid = kb.ocurrencia_nueva(g.cliente["id"], num, pid, d["equipo"], d["producto"],
                                      "\n".join(h["senales"] + h["lineas"]) or p["titulo"], g.usuario["usuario"])
            kb.ej("UPDATE incidentes SET estado='confirmado', verif=%s, nota=%s WHERE id=%s",
                  json.dumps({"confirmado": True, "revisiones": [{"revisar": x, "confirma": True, "salida": ""} for x in h["senales"]] +
                              [{"revisar": "Línea del log", "confirma": True, "salida": x} for x in h["lineas"]]}, ensure_ascii=False),
                  "Confirmado por el diagnóstico de Ansible", iid)
            kb.ocurrio(pid)
            return redirect(url_for("consola.incidente", iid=iid))
    tk = q1("SELECT * FROM tickets WHERE id=?", d["ticket_id"]) if d["ticket_id"] else None
    from .deuda import cobertura
    cob = {h["id"]: cobertura(g.cliente["id"], d["equipo"], kb.conocido(h["id"]) or {}) for h in (d["hallazgos"] or [])}
    return render_template("incidentes_diagnostico.html", d=d, tk=tk, cob=cob)



# ------------------------------------------------------------------ deuda de aprobación (stoppers por responsable)
@bp.route("/inventario/<int:eid>/servicio", methods=["POST"])
@requiere_login
def equipo_servicio(eid):
    """Encender, apagar o reiniciar un servicio de la máquina (el patín de la lista de servicios): ticket a «Plataforma ·
    Controlar servicio» con la unidad elegida; pasa por su aprobación. Los servicios esenciales no se tocan desde aquí."""
    from .operacion import solicitar_en_equipo
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    unidad, accion = (request.form.get("unidad") or "").strip(), request.form.get("accion", "")
    if not e or accion not in ("encender", "apagar", "reiniciar"):
        return {"error": "Pedido no válido"}, 400
    if not any(x.get("n") == unidad for x in (j(e["datos"], {}).get("servicios_det") or [])) or not re.fullmatch(r"[A-Za-z0-9@._-]{1,120}", unidad):
        return {"error": f"{unidad} no está en los servicios inventariados de {e['nombre']}"}, 400
    if servicio_fijo(unidad):
        return {"error": servicio_fijo(unidad)}, 400
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Plataforma · Controlar servicio'", g.cliente["id"])
    if not a:
        return {"error": "Falta «Plataforma · Controlar servicio» en el catálogo (sincroniza con AAP)"}, 400
    try:
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, {"sp_unidades": [re.sub(r"\.service$", "", unidad)], "sp_accion": accion, "sp_plataforma": unidad},
                                f"{accion.capitalize()} el servicio {unidad} en {e['nombre']}")
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "id": t["id"]}


@bp.route("/inventario/<int:eid>/agente", methods=["POST"])
@requiere_login
def equipo_agente(eid):
    """Encender, apagar o reiniciar un agente (sus unidades de systemd) o quitarlo de la máquina: ticket con aprobación."""
    from .operacion import solicitar_en_equipo
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    ag = next((a for a in AGENTES if a["skill"] == request.form.get("agente")), None)
    accion = request.form.get("accion", "")
    if not e or not ag or accion not in ("encender", "apagar", "reiniciar", "quitar"):
        return {"error": "Pedido no válido"}, 400
    nom = "Sistema · Quitar agente" if accion == "quitar" else "Plataforma · Controlar servicio"
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], nom)
    if not a:
        return {"error": f"Falta «{nom}» en el catálogo (sincroniza con AAP)"}, 400
    if not _certificada(a):
        return {"error": f"«{a['nombre']}» aún no pasa su plan de prueba: no se puede lanzar"}, 400
    params = {"qa_agente": ag["skill"]} if accion == "quitar" else {"sp_unidades": ag["unidades"], "sp_accion": accion, "sp_plataforma": ag["nombre"]}
    try:
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, params, f"{'Quitar' if accion == 'quitar' else accion.capitalize()} el agente «{ag['nombre']}» en {e['nombre']}")
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "id": t["id"]}


@bp.route("/tickets/<int:tid>/tomar", methods=["POST"])
@requiere_login
def ticket_tomar(tid):
    """Un miembro del grupo dueño toma la aprobación pendiente (la responde él; queda medida su participación)."""
    from .aprobacion_areas import filtrar_tickets
    from .grupos_dueno import tomar
    if request.form.get("_csrf") != session.get("csrf"):
        abort(400)
    t = q1("SELECT * FROM tickets WHERE id=? AND cliente_id=?", tid, g.cliente["id"])
    if not t or not filtrar_tickets([t], g.usuario, g.cliente["id"]):
        abort(404)
    try:
        x = tomar(g.usuario, tid, forzar=bool(request.form.get("forzar")))
        flash(f"Tomaste la aprobación de {t['numero']}", "ok")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(request.form.get("volver") or (url_for("consola.ticket", tid=tid) + "#aprobar"))


@bp.route("/deudas", methods=["GET", "POST"])
@requiere_login
def deudas():
    """Tickets pendientes de aprobación (no vencen), por área: cada etapa acumula sus días al área que la tiene."""
    from . import aprobacion_areas as AA
    ts = tickets_visibles()
    if request.method == "POST":
        _escritura()
        f = request.form
        t = next((x for x in ts if x["id"] == int(f.get("ticket") or 0)), None)
        if not t or t["estado"] != "pendiente_aprobacion":
            abort(404)
        a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
        if not AA.puede_aprobar(g.usuario, g.cliente["id"], t, a):
            abort(403)
        aprobar_ = f.get("accion") == "aprobar"
        if not aprobar_ and not f.get("comentario", "").strip():
            flash("Al rechazar, deja el motivo", "error")
            return redirect(url_for("consola.deudas"))
        try:
            resolver_aprobacion(g.usuario, t["id"], aprobar_, f.get("comentario", ""))
            flash(f"{t['numero']}: {'aprobado' if aprobar_ else 'rechazado'}", "ok")
        except ErrorAAP as e:
            flash(f"AAP no aceptó la respuesta: {e}", "error")
        return redirect(url_for("consola.deudas"))
    pend = AA.pendientes(g.cliente["id"], ts)
    for p_ in pend:
        p_["area_n"] = AA.nombre(p_["area"])
        p_["puede"] = AA.puede_aprobar(g.usuario, g.cliente["id"], p_["t"])
    areas_ = [dict(x, area_n=AA.nombre(x["area"])) for x in AA.por_area(pend)]
    mi_area = AA.area_usuario(g.usuario, g.cliente["id"])
    from .grupos_dueno import participacion
    return render_template("deudas.html", part=participacion(g.cliente["id"]), pend=pend, areas_=areas_, mi_area=AA.nombre(mi_area) if mi_area else "",
                           dias_total=int(sum(x["dias"] for x in pend)))


@bp.route("/inventario/<int:eid>/alertas", methods=["POST"])
@requiere_login
def equipo_alertas(eid):
    """Botón «Alertas» del objeto: pide a Ansible (ticket a «Monitoreo · Configurar alertas del equipo») que deje configuradas
    en Prometheus las alertas marcadas en el flotante. La configuración vive en Ansible; FaceOps guarda solo su reflejo."""
    from . import alertas_objeto as ao
    from .operacion import solicitar
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e or not ao.tipo_de(e):
        abort(404)
    vuelta = url_for("consola.equipo", eid=eid) + "#alertas"
    ctx = ao.contexto(g.cliente["id"], e, True)
    if not ctx["disponible"]:
        flash(ctx["por_que"], "error")
        return redirect(vuelta)
    if ctx["en_curso"]:
        flash(f"Ya hay un cambio de alertas en curso para {e['nombre']} ({ctx['en_curso']['numero']})", "error")
        return redirect(vuelta)
    try:
        par = ao.pedido_de_formulario(e, request.form)
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], ao.AUTO)
        n_ = len(par["al_alertas"])
        t = solicitar(g.usuario, g.cliente, a, "", par,
                      request.form.get("motivo") or (f"Alertas de {e['nombre']}: {n_} activa{'s' if n_ != 1 else ''}" if n_ else f"Retirar las alertas de {e['nombre']}"))
    except (ValueError, PermissionError) as x:
        flash(str(x), "error")
        return redirect(vuelta)
    flash(f"Pedido {t['numero']}: Ansible configurará las alertas de {e['nombre']} en Prometheus" + (" (requiere aprobación)" if t["estado"] == "pendiente_aprobacion" else ""), "ok")
    return redirect(vuelta)


@bp.route("/inventario/<int:eid>/descripcion", methods=["POST"])
@requiere_login
def equipo_descripcion(eid):
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    d = (request.form.get("descripcion") or "").strip()[:120]
    ej("UPDATE equipos SET descripcion=? WHERE id=?", d, eid)
    auditar(g.usuario["usuario"], g.cliente["id"], "descripción del equipo", e["nombre"], {"descripcion": d})
    return redirect(url_for("consola.equipo", eid=eid))


def _limites_recursos(e):
    """Lo que se puede asignar a la máquina según la infraestructura donde vive: cores y memoria del anfitrión KVM (capacidad
    medida) menos lo que ya usan las otras máquinas encendidas; la máquina conserva lo suyo."""
    from .infraestructuras import lista
    eqs_ = equipos_visibles()
    infras = lista(g.cliente["id"], eqs_)
    inf = next((x for x in infras if x["ubicacion"] == e.get("ubicacion") and x["tipo"] == "kvm"), None)
    if not inf:
        return None
    d_ = inf.get("detalle_d") or {}
    cap = d_.get("capacidad") or next(((x.get("detalle_d") or {}).get("capacidad") for x in infras if x["tipo"] == "kvm"
                                       and (x.get("detalle_d") or {}).get("anfitrion") == d_.get("anfitrion") and (x.get("detalle_d") or {}).get("capacidad")), None) or {}
    cores = int(cap.get("cores") or 0)
    ram_tot = float(cap.get("ram_gb") or 0)
    ram_disp = float(cap.get("ram_disp_gb") if cap.get("ram_disp_gb") is not None else ram_tot)
    propia = (e.get("ram_mb") or 0) / 1024 if e.get("energia") == "encendido" else 0
    max_ram = max(1, int(ram_disp + propia - 4)) if ram_tot else 32   # deja 4 GB al anfitrión
    max_cpu = max(1, cores) if cores else 16
    cpus = [n for n in (1, 2, 4, 6, 8, 12, 16, 24, 32) if n <= max_cpu]
    rams = [n for n in (1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96) if n <= max_ram]
    return {"infra": inf["nombre"], "cores": cores, "ram_gb": ram_tot, "ram_disp_gb": ram_disp, "max_cpu": max_cpu, "max_ram": max_ram,
            "cpus": cpus or [1], "rams": rams or [1], "medido": cap.get("medido")}


def _hipervisor(e):
    """Hipervisor o plataforma donde vive la máquina (KVM, VMware, nube, OpenShift) y adónde lleva su enlace."""
    if (e.get("forma") or "") != "virtual":
        return None
    infras = q("SELECT * FROM infraestructuras WHERE cliente_id=?", g.cliente["id"])
    inf = next((x for x in infras if x["ubicacion"] == e.get("ubicacion") and x["tipo"] != "openshift"), None)
    if not inf:
        return None
    if inf["tipo"] in TIPOS_HIPERVISOR:   # ficha del hipervisor (datos generales, recursos y sus máquinas)
        return {"nombre": inf["nombre"], "url": url_for("consola.hipervisor", iid=inf["id"])}
    return {"nombre": inf["nombre"], "url": url_for("consola.infraestructuras") + f"#infra-{inf['id']}"}


def _cod_eq(x):
    from .identidad import codigo
    return codigo(x)


def _uso_seg(e):
    try:
        from .uso import por_segmento
        return por_segmento(g.cliente["id"], e)
    except Exception:  # noqa: BLE001
        return {"medido": False}


DATALAKE = "Data lake · Derivar segmento"
ENERGIA = "Infra · Encender o apagar equipo"


def uso_equipo(e, datos, discos):
    """Uso de CPU, memoria y discos para la cabecera. Orden: medido en la máquina → informado por el clúster OpenShift →
    EMULADO (valor estable por equipo, marcado como tal) mientras no exista la medición de fondo para ese tipo de equipo."""
    import hashlib
    h = int(hashlib.sha256(e["nombre"].encode()).hexdigest()[:8], 16)
    out = {"cpu_u": None, "carga": None, "ram_u": None, "ram_gb": None, "discos": discos, "emulado": set(), "fuente": {}}
    # 1.º el promedio de todas las muestras medidas en la máquina (medición horaria «Uso por segmento», últimos 15 días)
    ms = [j(r["datos"], {}) for r in q("SELECT datos FROM uso_muestras WHERE cliente_id=? AND equipo=? ORDER BY ts", e.get("cliente_id"), e["nombre"])] \
        if e.get("cliente_id") else []
    ms = [m for m in ms if m.get("ncpu") and m.get("mem_mb")]
    if ms:
        dias = max(1, round((ms[-1]["ts"] - ms[0]["ts"]) / 86400)) if len(ms) > 1 else 0
        txt = f"promedio de {len(ms)} muestra{'s' if len(ms) != 1 else ''}" + (f" ({dias} día{'s' if dias != 1 else ''})" if dias else "")
        cpu = sum(min(100.0, 100.0 * float(m.get("carga") or 0) / m["ncpu"]) for m in ms) / len(ms)
        ram = sum(100.0 * (m["mem_mb"] - (m.get("mem_disp_mb") or 0)) / m["mem_mb"] for m in ms) / len(ms)
        out.update(cpu_u=round(cpu), carga=f"carga media {sum(float(m.get('carga') or 0) for m in ms) / len(ms):.2f}",
                   ram_u=round(ram), ram_gb=round(ms[-1]["mem_mb"] * ram / 100 / 1024, 1))
        out["fuente"].update(cpu=txt, ram=txt)
    ocp = datos.get("ocp") or {}
    nodo = datos.get("nodo_ocp") or {}
    rec = ocp.get("recursos") or {}
    carga = datos.get("carga_1m")
    if out["cpu_u"] is not None:
        pass
    elif e.get("cpu") and carga is not None:
        out.update(cpu_u=min(100, round(100 * carga / e["cpu"])), carga=carga)
    else:
        u, t = (nodo.get("cpu_uso"), nodo.get("cpu")) if nodo else (rec.get("cpu_uso"), rec.get("cpu_total"))
        try:
            out.update(cpu_u=min(100, round(100 * float(u) / float(t))), carga=f"{float(u):.2f} cores")
            out["fuente"]["cpu"] = "métricas del clúster OpenShift"
        except (TypeError, ValueError, ZeroDivisionError):
            if e.get("cpu"):
                out.update(cpu_u=12 + h % 38, carga=None)
                out["emulado"].add("cpu")
    ram_t = e.get("ram_mb") or 0
    ram_l = datos.get("ram_disponible_mb") if datos.get("ram_disponible_mb") is not None else datos.get("ram_libre_mb")
    if out["ram_u"] is not None:
        pass
    elif ram_t and ram_l is not None:
        out.update(ram_u=round(100 * (ram_t - ram_l) / ram_t), ram_gb=round((ram_t - ram_l) / 1024, 1))
    else:
        u, t = (nodo.get("memoria_uso_gb"), nodo.get("memoria_gb")) if nodo else (rec.get("memoria_uso_gb"), rec.get("memoria_total_gb"))
        try:
            out.update(ram_u=round(100 * float(u) / float(t)), ram_gb=round(float(u), 1))
            out["fuente"]["ram"] = "métricas del clúster OpenShift"
        except (TypeError, ValueError, ZeroDivisionError):
            if ram_t:
                pct = 35 + (h >> 8) % 40
                out.update(ram_u=pct, ram_gb=round(ram_t / 1024 * pct / 100, 1))
                out["emulado"].add("ram")
    if not [d for d in discos if d.get("gb")] and (e.get("forma") or "") in ("virtual", "física", "fisica", "") \
            and (e.get("categoria") or "servidor") in ("servidor", "clúster openshift", "estación de trabajo"):
        gb = 120 if nodo or ocp else 50
        out["discos"] = [{"dev": "", "gb": gb, "uso": 30 + (h >> 16) % 45, "punto": "/"}]
        out["emulado"].add("discos")
    return out


def nodos_equipo_cluster(e):
    """Las máquinas del inventario que son los nodos de un clúster OpenShift."""
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    return [x for x in (_nodo_equipo(n) for n in ((d.get("ocp") or {}).get("nodos") or [])) if x]


def energia_posible(e):
    """Se enciende o apaga desde FaceOps: máquinas virtuales del anfitrión KVM (no vSphere ni equipos físicos o de red).
    Un clúster OpenShift se enciende o apaga a través de sus nodos (todos deben poder)."""
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    if (e.get("categoria") or "") == "clúster openshift":
        ns = nodos_equipo_cluster(e)
        return bool(ns) and len(ns) == len((d.get("ocp") or {}).get("nodos") or []) and all(energia_posible(x) for x in ns)
    return (e.get("forma") or "") == "virtual" and (e.get("categoria") or "servidor") in ("servidor", "estación de trabajo") \
        and not d.get("vsphere") and "vmware" not in (e.get("ubicacion") or "").lower() and e.get("estado") != "retirado" \
        and "kvm" not in (e.get("skills") or [])   # el anfitrión de la plataforma no se apaga desde aquí


@bp.route("/inventario/<int:eid>/energia", methods=["POST"])
@requiere_login
def equipo_energia(eid):
    """Encender, apagar (ordenado) o reiniciar la VM: ticket en AAP; en producción, con aprobación."""
    from .operacion import solicitar
    if request.form.get("_csrf") != session.get("csrf") or g.perfil["modo"] != "escritura":
        return {"error": "Necesitas perfil de escritura"}, 403
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    accion = request.form.get("accion", "")
    if not e or accion not in ("encender", "apagar", "reiniciar"):
        return {"error": "Equipo o acción no válidos"}, 400
    if not energia_posible(e):
        return {"error": "Este equipo no se enciende ni apaga desde FaceOps (solo máquinas virtuales del anfitrión KVM)"}, 400
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], ENERGIA)
    if not a:
        return {"error": f"Falta «{ENERGIA}» en el catálogo (sincroniza con AAP)"}, 400
    destinos = nodos_equipo_cluster(e) if (e.get("categoria") or "") == "clúster openshift" else [e]   # un clúster: cada uno de sus nodos
    try:
        for x_ in destinos:
            t = solicitar(g.usuario, g.cliente, a, (x_["segmentos"] or e["segmentos"] or [""])[0], {"en_equipo": x_["nombre"], "en_accion": accion},
                          f"{accion.capitalize()} {x_['nombre']}" + (f" (nodo del clúster {e['nombre']})" if x_ is not e else ""),
                          filtros={"ambientes": [e["ambiente"]]} if e.get("ambiente") else None)
    except (PermissionError, ErrorAAP, ValueError) as x:
        return {"error": str(x)}, 400
    return {"id": t["id"], "numero": t["numero"], "estado": t["estado"], "url": url_for("consola.ticket", tid=t["id"]), "auto": a["nombre"], "nombre_aap": a["nombre_aap"]}


@bp.route("/equipos/<int:eid>/evaluar-so", methods=["GET", "POST"])
@requiere_login
def equipo_evaluar_so(eid):
    """Evalúa ahora el sistema operativo del equipo para una posible actualización (solo lectura)."""
    from .operacion import solicitar_en_equipo
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap='Migración · Evaluar sistema operativo'", g.cliente["id"])
    if not e or not a:
        abort(404)
    if request.method == "GET":   # revisar antes de crear el ticket
        from .economia import estimar
        from .informe_so import secuencia
        dst = re.sub(r"[^0-9]", "", request.args.get("destino", ""))[:2]
        sec = secuencia(e)
        paso = next((p for p in sec if str(p.get("mayor")) == dst and not p.get("menor")), None) or next((p for p in sec if str(p.get("mayor")) == dst), None)
        camino = [p["version"] for p in sec if p.get("actual") or (p.get("mayor") and int(p["mayor"]) <= int(dst or 0))]
        return render_template("evaluar_so.html", e=e, a=a, guia=j(a.get("guia"), {}), destino=dst, destino_txt=(paso or {}).get("version", dst),
                               camino=camino, est=estimar(a, {}, e["nombre"]), correo=g.usuario.get("correo"))
    try:
        dst = re.sub(r"[^0-9]", "", request.form.get("destino", ""))[:2]
        params = {"mig_politica": request.form.get("politica", "minimo"), "mig_preupgrade": request.form.get("preupgrade", "false")} | ({"mig_destino": dst} if dst else {})
        t = solicitar_en_equipo(g.usuario, g.cliente, a, e, params, (request.form.get("motivo") or "").strip()[:200]
                                or (f"Evaluar impacto de llevar {e['nombre']} a RHEL {dst}" if dst else f"Evaluar actualización de {e['nombre']}"))
        flash(f"Evaluando el impacto en {e['nombre']} ({t['numero']}): al terminar llega el informe PDF a tu correo y queda en esta ficha", "ok")
    except (PermissionError, ErrorAAP, ValueError) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.equipo", eid=eid) + "#p-rhel")


@bp.route("/equipos/<int:eid>/informe-so/<int:iid>.pdf")
@requiere_login
def equipo_informe_so(eid, iid):
    """Informe PDF de impacto de migración del equipo (el mismo que se envía por correo)."""
    from flask import send_file
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    r = q1("SELECT * FROM informes_so WHERE id=? AND cliente_id=?", iid, g.cliente["id"])
    if not e or not r or r["equipo"] != e["nombre"] or not os.path.exists(r["archivo"] or ""):
        abort(404)
    return send_file(r["archivo"], mimetype="application/pdf", download_name=os.path.basename(r["archivo"]).split("-", 1)[1])
