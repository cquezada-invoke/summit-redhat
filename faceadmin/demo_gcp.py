"""FaceAdmin · Demo instantánea en GCP.

Una Demo (demos_nube.py) puede pedirse «instantánea»: FaceAdmin crea una VM en GCP desde la imagen dorada (AWX + EDA comunitarios,
FaceOps, Gitea, Vault, MinIO, Prometheus/Grafana y KVM con 2–3 VMs Rocky anidadas; ver herramientas/demo-gcp/) y gobierna su ciclo:

  pedida → creando → activa ⇄ apagada → vencida → eliminada        (además: eliminando, error)

- Duración: la pedida (por defecto «dias_def» = 7), con tope «dias_max» (14, el máximo de una Demo) desde que se crea; la fecha de expiración queda en FaceAdmin y como etiqueta en GCP.
  Al vencer se elimina todo (VM, discos, IP reservada; la bóveda y las credenciales viven dentro de la VM y se van con ella),
  con aviso por correo «aviso_horas» antes. Como red de seguridad, la VM nace con terminationTime=expiración (acción DELETE) en GCP.
- Horario hábil por demo (zona horaria del cliente, días y horas configurables): fuera de horario se apaga siempre.
- Encendido bajo demanda: botón en FaceAdmin o la URL pública «encender mi demo» (enlace con token que se entrega al cliente);
  opcionalmente arranque programado al inicio del horario.
- Inactividad: el agente de la VM informa el uso cada 5 min (POST /api/demo-gcp/<numero>/uso con su token); sin uso por
  «inactividad_min» la VM se apaga sola (el agente) y, si no, la apaga FaceAdmin.
- El ciclo lo ejecuta un hilo de FaceAdmin cada minuto (un solo dueño gracias a un candado de archivo)."""
import fcntl
import hashlib
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for

import gcp
from app import BD, ahora, app, bitacora, cliente_ok, ej, enviar_correo, q, q1, requiere_login, solo_invoke

RELOJ = time.time   # inyectable en las pruebas
ESTADOS = {"pedida": ("Pedida", "med"), "creando": ("Creando la VM", "med"), "activa": ("Encendida", "ok"), "apagada": ("Apagada", ""),
           "vencida": ("Vencida · eliminando", "mal"), "eliminando": ("Eliminando", "mal"), "eliminada": ("Eliminada", ""), "error": ("Error", "mal")}
VIVOS = ("creando", "activa", "apagada", "vencida", "eliminando", "error")
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
_MUTEX = threading.Lock()


def iniciar():
    with app.app_context():
        ej("CREATE TABLE IF NOT EXISTS demos_gcp (id INTEGER PRIMARY KEY, demo_id INTEGER UNIQUE NOT NULL, estado TEXT DEFAULT 'pedida', "
           "instancia TEXT DEFAULT '', ip TEXT DEFAULT '', url TEXT DEFAULT '', zona TEXT DEFAULT '', tipo TEXT DEFAULT '', imagen TEXT DEFAULT '', "
           "tz TEXT DEFAULT 'America/Santiago', dias_habiles TEXT DEFAULT '1-5', hora_ini INTEGER DEFAULT 8, hora_fin INTEGER DEFAULT 19, "
           "inactividad_min INTEGER DEFAULT 30, arranque_programado INTEGER DEFAULT 0, arranque_dia TEXT DEFAULT '', "
           "creada REAL, expira REAL, encendida_desde REAL, ultimo_uso REAL, ultimo_latido REAL, uso_detalle TEXT DEFAULT '', horas_uso REAL DEFAULT 0, "
           "motivo_apagado TEXT DEFAULT '', token_encender TEXT DEFAULT '', token_vm_hash TEXT DEFAULT '', usuario_cliente TEXT DEFAULT 'cliente', "
           "clave_cifrada TEXT DEFAULT '', clave_entregada REAL, correo_aviso TEXT DEFAULT '', aviso_enviado REAL, error TEXT DEFAULT '', "
           "eliminada REAL, por TEXT DEFAULT '')")


iniciar()


# ---------------------------------------------------------------- horario y utilidades
def _zona(tz):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(tz or "America/Santiago")
    except Exception:  # noqa: BLE001
        return ZoneInfo("America/Santiago")


def dias_set(txt):
    """«1-5» → {1..5}; «1,3,5» → {1,3,5} (1 = lunes … 7 = domingo)."""
    out = set()
    for p in (txt or "1-5").replace(" ", "").split(","):
        if "-" in p:
            a, b = p.split("-", 1)
            if a.isdigit() and b.isdigit():
                out |= set(range(int(a), int(b) + 1))
        elif p.isdigit():
            out.add(int(p))
    return {x for x in out if 1 <= x <= 7} or {1, 2, 3, 4, 5}


def en_horario(r, ts=None):
    t = datetime.fromtimestamp(ts if ts is not None else RELOJ(), _zona(r.get("tz")))
    return t.isoweekday() in dias_set(r.get("dias_habiles")) and int(r.get("hora_ini") or 0) <= t.hour < int(r.get("hora_fin") or 24)


def local(r, ts):
    return datetime.fromtimestamp(ts, _zona(r.get("tz"))).strftime("%Y-%m-%d %H:%M") if ts else ""


def horario_texto(r):
    ds = sorted(dias_set(r.get("dias_habiles")))
    dias = f"{DIAS[ds[0] - 1]}–{DIAS[ds[-1] - 1]}" if ds == list(range(ds[0], ds[-1] + 1)) else ", ".join(DIAS[d - 1] for d in ds)
    return f"{dias} {int(r['hora_ini']):02d}:00–{int(r['hora_fin']):02d}:00 ({r.get('tz')})"


def _h(x):
    return hashlib.sha256(x.encode()).hexdigest()


def _nombre_instancia(numero):
    return gcp.etiqueta(gcp.config()["prefijo"] + numero.lower())


def _act(rid, **cambios):
    ej(f"UPDATE demos_gcp SET {', '.join(k + '=?' for k in cambios)} WHERE id=?", *cambios.values(), rid)


def _demo(r):
    return q1("SELECT * FROM demos_nube WHERE id=?", r["demo_id"]) or {}


def _bit(r, accion, detalle=None):
    d = _demo(r)
    c = q1("SELECT codigo FROM clientes WHERE id=?", d.get("cliente_id")) or {}
    bitacora(f"demo · gcp · {accion}", c.get("codigo", "invoke"), dict(detalle or {}, demo=d.get("numero")))


def de_demo(did):
    """Registro GCP de la Demo enriquecido para la vista (o None si la Demo no es instantánea)."""
    r = q1("SELECT * FROM demos_gcp WHERE demo_id=?", did)
    if not r:
        return None
    now = RELOJ()
    r["est_t"] = ESTADOS.get(r["estado"], (r["estado"], ""))
    r["horario"] = horario_texto(r)
    r["en_horario"] = en_horario(r, now)
    r["expira_txt"] = local(r, r["expira"])
    r["quedan_h"] = max(0, int(((r["expira"] or now) - now) / 3600))
    r["ultimo_uso_txt"] = local(r, r["ultimo_uso"])
    r["latido_txt"] = local(r, r["ultimo_latido"])
    r["encendida_txt"] = local(r, r["encendida_desde"]) if r["estado"] == "activa" else ""
    r["horas"] = round((r["horas_uso"] or 0) + ((now - r["encendida_desde"]) / 3600 if r["estado"] == "activa" and r["encendida_desde"] else 0), 1)
    r["enlace"] = url_for("demo_gcp_publica", token=r["token_encender"], _external=True) if r["token_encender"] and r["estado"] in VIVOS else ""
    return r


app.jinja_env.globals["demo_gcp_de"] = de_demo
app.jinja_env.globals["demo_gcp_cfg"] = gcp.config
app.jinja_env.globals["demo_gcp_listo"] = lambda: gcp.sa_resumen() is not None and bool(gcp.config().get("proyecto"))


# ---------------------------------------------------------------- operaciones
def pedir(d, f, por):
    """Pide la VM de la Demo `d` (fila de demos_nube). Crea el registro en «creando» y lanza la creación en segundo plano."""
    cfg = gcp.config()
    if q1("SELECT id FROM demos_gcp WHERE demo_id=? AND estado<>'eliminada'", d["id"]):
        raise ValueError("Esta Demo ya tiene una VM en GCP")
    if gcp.sa_resumen() is None or not cfg.get("proyecto"):
        raise ValueError("Falta configurar la cuenta de servicio y el proyecto de GCP (Demos y PoC › Demo instantánea en GCP)")
    try:
        hi, hf, ina = int(f.get("hora_ini") or cfg["hora_ini"]), int(f.get("hora_fin") or cfg["hora_fin"]), int(f.get("inactividad_min") or cfg["inactividad_min"])
    except ValueError as e:
        raise ValueError("Horario o minutos de inactividad inválidos") from e
    if not (0 <= hi < hf <= 24) or not (10 <= ina <= 240):
        raise ValueError("El horario debe ir de 0 a 24 con inicio menor que fin, y la inactividad entre 10 y 240 minutos")
    tz = (f.get("tz") or cfg["tz"]).strip()
    _zona(tz)
    now = RELOJ()
    try:
        dias = int(f.get("dias") or 0) or min(int(cfg.get("dias_def") or 7), _dias_demo(d) or 99)
    except ValueError as e:
        raise ValueError("Duración inválida") from e
    tope = min(int(cfg.get("dias_max") or 14), 14)
    if not 1 <= dias <= tope:
        raise ValueError(f"La demo dura entre 1 y {tope} días")
    expira = now + dias * 86400
    clave = secrets.token_urlsafe(12)
    rid = ej("INSERT INTO demos_gcp (demo_id, estado, instancia, zona, tipo, imagen, tz, dias_habiles, hora_ini, hora_fin, inactividad_min, "
             "arranque_programado, creada, expira, token_encender, usuario_cliente, clave_cifrada, correo_aviso, por) VALUES "
             "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             d["id"], "creando", _nombre_instancia(d["numero"]), cfg["zona"], cfg["tipo"], cfg["imagen"], tz,
             (f.get("dias_habiles") or cfg["dias_habiles"]).strip()[:20], hi, hf, ina, 1 if f.get("arranque_programado") else 0, now, expira,
             secrets.token_urlsafe(24), (f.get("usuario_cliente") or "cliente").strip()[:32] or "cliente", gcp.cifrar(clave),
             (f.get("correo_aviso") or "").strip()[:160], por)
    hasta = datetime.fromtimestamp(expira).date().isoformat()   # la Demo no puede durar más que la VM
    ej("UPDATE demos_nube SET hasta=CASE WHEN hasta>? THEN ? ELSE hasta END, entorno_nombre=?, entrega=? WHERE id=?", hasta, hasta,
       f"GCP · {_nombre_instancia(d['numero'])}", "Enlace «encender mi demo» y credenciales generadas por FaceAdmin (se muestran en la Demo)", d["id"])
    r = q1("SELECT * FROM demos_gcp WHERE id=?", rid)
    _bit(r, "pedida", {"instancia": r["instancia"], "expira": local(r, expira), "tipo": cfg["tipo"]})
    _en_fondo(rid)
    return rid


def _dias_demo(d):
    """Días que quedan de la Demo (desde hoy hasta su término, inclusive)."""
    try:
        from datetime import date
        hoy = datetime.fromtimestamp(RELOJ()).date()
        return max(1, (date.fromisoformat(d.get("hasta") or "") - hoy).days + 1)
    except ValueError:
        return 0


def _en_fondo(rid):
    if os.environ.get("FACEADMIN_DEMO_GCP_SINCRONO") == "1":
        with app.app_context():
            avanzar(rid)
        return

    def t():
        with app.app_context():
            try:
                avanzar(rid)
            except Exception:  # noqa: BLE001  el ciclo lo reintenta
                pass
    threading.Thread(target=t, daemon=True).start()


def _crear(r, cx):
    """Paso de creación: IP reservada → VM desde la imagen dorada con etiquetas y metadatos de la demo."""
    d = _demo(r)
    c = q1("SELECT codigo FROM clientes WHERE id=?", d.get("cliente_id")) or {}
    cfg = cx.cfg
    exp = datetime.fromtimestamp(r["expira"], timezone.utc)
    etiquetas = {"demo": gcp.etiqueta(d.get("numero", "")), "cliente": gcp.etiqueta(c.get("codigo", "")),
                 "expira": exp.strftime("%Y%m%d-%H%M"), "origen": "faceadmin"}
    ip = r["ip"]
    if not ip:
        try:
            ip = cx.reservar_ip(r["instancia"], dict(etiquetas, **{gcp.ETIQUETA: "si"}))
        except gcp.ErrorGCP as e:
            if "403" not in str(e):
                raise
            ip = ""   # sin permiso para reservar IP: IP efímera; la URL se actualiza en cada encendido
    url = f"https://faceops.{ip}.sslip.io" if ip else ""
    _act(r["id"], ip=ip, url=url)
    token_vm = secrets.token_urlsafe(32)
    meta = {"faceadmin-url": (cfg.get("faceadmin_url") or request_base()).rstrip("/"), "demo-numero": d.get("numero", ""), "demo-token": token_vm,
            "demo-ip": ip or "efimera", "demo-url": url, "demo-tz": r["tz"], "demo-horario": json.dumps({"dias": r["dias_habiles"], "ini": r["hora_ini"], "fin": r["hora_fin"]}),
            "demo-inactividad-min": r["inactividad_min"], "demo-expira": exp.isoformat()}
    if cx.instancia(r["instancia"]) is None:
        cx.crear(r["instancia"], etiquetas, meta, ip, vence_iso=exp.strftime("%Y-%m-%dT%H:%M:%SZ"))
    _act(r["id"], token_vm_hash=_h(token_vm), encendida_desde=RELOJ(), ultimo_uso=RELOJ())
    ej("UPDATE demos_nube SET entorno_url=?, estado=CASE WHEN estado='cerrada' THEN estado ELSE 'preparada' END WHERE id=?", url, r["demo_id"])
    _bit(r, "vm creada", {"instancia": r["instancia"], "ip": ip})


def request_base():
    try:
        return request.host_url
    except RuntimeError:
        return ""


def _detener(r, cx, motivo):
    cx.apagar(r["instancia"])
    horas = (r["horas_uso"] or 0) + ((RELOJ() - r["encendida_desde"]) / 3600 if r["encendida_desde"] else 0)
    _act(r["id"], estado="apagada", motivo_apagado=motivo, horas_uso=round(horas, 3), encendida_desde=None)
    _bit(r, "apagada", {"motivo": motivo})


def _eliminar(r, cx, motivo):
    cx.eliminar(r["instancia"])
    _act(r["id"], estado="eliminando", motivo_apagado=motivo)
    _bit(r, "eliminación pedida", {"motivo": motivo})


def _aviso(r):
    d = _demo(r)
    por = q1("SELECT usuario FROM usuarios WHERE usuario=?", r["por"]) or {}
    from app import hora_para   # zona del destinatario; si no se conoce, la zona configurada de la demo (con su etiqueta)
    for para in {x for x in (r["correo_aviso"], por.get("usuario") if "@" in (por.get("usuario") or "") else "") if x}:
        vence = hora_para(r["expira"], para, r.get("tz") or "")
        cuerpo = (f"La Demo {d.get('numero')} en GCP vence el {vence} y se eliminará por completo: la VM, sus discos y "
                  "todos los datos. Si necesitan continuar, conversemos el paso a una PoC o al contrato.\n\nFaceAdmin · Invoke")
        enviar_correo(d.get("cliente_id"), para, f"Tu Demo {d.get('numero')} vence el {vence.split(' ')[0]}", cuerpo)
    _act(r["id"], aviso_enviado=RELOJ())
    _bit(r, "aviso de vencimiento enviado", {"para": r["correo_aviso"]})


def avanzar(rid, cx=None):
    """Un paso del ciclo de vida para una demo. Idempotente: el ciclo la llama cada minuto."""
    with _MUTEX:
        r = q1("SELECT * FROM demos_gcp WHERE id=?", rid)
        if not r or r["estado"] in ("pedida", "eliminada"):
            return r and r["estado"]
        cx = cx or gcp.Compute()
        now = RELOJ()
        try:
            # 1) vencimiento: todo se elimina
            if r["estado"] == "eliminando":
                if cx.estado(r["instancia"]) is None:
                    try:
                        cx.liberar_ip(r["instancia"])
                    except gcp.ErrorGCP as e:
                        if "in use" in str(e).lower() or "RESOURCE_IN_USE" in str(e):
                            return r["estado"]
                        raise
                    _act(rid, estado="eliminada", eliminada=now, clave_cifrada="", token_vm_hash="", token_encender="", encendida_desde=None, error="")
                    ej("UPDATE demos_nube SET estado='cerrada', cerrada=? WHERE id=? AND estado<>'cerrada'", ahora(), r["demo_id"])
                    _bit(r, "eliminada", {"instancia": r["instancia"], "ip": r["ip"]})
                return q1("SELECT estado FROM demos_gcp WHERE id=?", rid)["estado"]
            if now >= (r["expira"] or now + 1):
                _act(rid, estado="vencida")
                _eliminar(r, cx, "venció el plazo máximo")
                return "eliminando"
            if not r["aviso_enviado"] and now >= r["expira"] - int(cx.cfg.get("aviso_horas") or 24) * 3600:
                _aviso(r)
            # 2) creación
            if r["estado"] in ("creando", "error") and not r["token_vm_hash"]:
                _crear(r, cx)
                r = q1("SELECT * FROM demos_gcp WHERE id=?", rid)
            st = cx.estado(r["instancia"])
            if st == "RUNNING":
                ipx = cx.ip_externa(r["instancia"])
                if ipx and ipx != r["ip"]:
                    _act(rid, ip=ipx, url=f"https://faceops.{ipx}.sslip.io")
                    ej("UPDATE demos_nube SET entorno_url=? WHERE id=?", f"https://faceops.{ipx}.sslip.io", r["demo_id"])
                    r = q1("SELECT * FROM demos_gcp WHERE id=?", rid)
            if r["estado"] in ("creando", "error"):
                if st == "RUNNING":
                    _act(rid, estado="activa", error="", encendida_desde=now, ultimo_uso=now)
                    _bit(r, "activa", {"url": r["url"]})
                    r = q1("SELECT * FROM demos_gcp WHERE id=?", rid)
                elif st is None and now - r["creada"] > 1800:
                    _act(rid, estado="error", error="La VM no apareció en GCP 30 minutos después de pedirla")
                    return "error"
                else:
                    return r["estado"]
            # 3) encendida: horario e inactividad
            if r["estado"] == "activa":
                if st in ("TERMINATED", "STOPPED", "SUSPENDED") or st is None:
                    horas = (r["horas_uso"] or 0) + ((now - r["encendida_desde"]) / 3600 if r["encendida_desde"] else 0)
                    _act(rid, estado="apagada", motivo_apagado="se apagó sola (agente de inactividad)" if st else "la VM no existe en GCP",
                         horas_uso=round(horas, 3), encendida_desde=None)
                    return "apagada"
                if not en_horario(r, now):
                    _detener(r, cx, "fuera del horario hábil")
                    return "apagada"
                base = max(r["ultimo_uso"] or 0, r["encendida_desde"] or 0)
                if now - base > int(r["inactividad_min"]) * 60:
                    _detener(r, cx, f"sin uso por {r['inactividad_min']} minutos")
                    return "apagada"
                return "activa"
            # 4) apagada: refleja un encendido externo y el arranque programado
            if r["estado"] == "apagada":
                if st == "RUNNING":
                    _act(rid, estado="activa", encendida_desde=now, ultimo_uso=now, motivo_apagado="")
                    return "activa"
                hoy = datetime.fromtimestamp(now, _zona(r["tz"])).date().isoformat()
                if r["arranque_programado"] and en_horario(r, now) and r["arranque_dia"] != hoy:
                    _act(rid, arranque_dia=hoy)
                    encender(rid, "arranque programado", cx)
                    return "activa"
            return r["estado"]
        except gcp.ErrorGCP as e:
            _act(rid, error=str(e)[:400])
            return "error-transitorio"


def encender(rid, quien, cx=None):
    """Enciende la VM si la demo no venció y está en horario hábil. Devuelve un texto para el usuario."""
    r = q1("SELECT * FROM demos_gcp WHERE id=?", rid)
    now = RELOJ()
    if not r or r["estado"] not in ("apagada", "activa"):
        raise ValueError("La demo no está disponible para encenderse" + (f" (estado: {r['estado']})" if r else ""))
    if now >= r["expira"]:
        raise ValueError("La demo ya venció")
    if not en_horario(r, now):
        raise ValueError(f"La demo solo se enciende en horario hábil: {horario_texto(r)}")
    if r["estado"] == "activa":
        _act(rid, ultimo_uso=now)
        return "La demo ya está encendida"
    (cx or gcp.Compute()).encender(r["instancia"])
    _act(rid, estado="activa", encendida_desde=now, ultimo_uso=now, motivo_apagado="", error="")
    _bit(r, "encendida", {"por": quien})
    return "Encendiendo la demo: queda lista en 2–4 minutos"


def apagar(rid, quien, cx=None):
    r = q1("SELECT * FROM demos_gcp WHERE id=?", rid)
    if not r or r["estado"] != "activa":
        raise ValueError("La demo no está encendida")
    _detener(r, cx or gcp.Compute(), f"apagada a mano por {quien}")


def eliminar_ya(rid, quien, cx=None):
    r = q1("SELECT * FROM demos_gcp WHERE id=?", rid)
    if not r or r["estado"] in ("eliminada", "eliminando"):
        raise ValueError("La demo ya está eliminada o en eliminación")
    _eliminar(r, cx or gcp.Compute(), f"eliminada a mano por {quien}")
    _en_fondo(rid)


def ciclo(cx=None):
    """Recorre las demos vivas. Sin demos vivas no llama a GCP."""
    ids = [x["id"] for x in q(f"SELECT id FROM demos_gcp WHERE estado IN ({','.join('?' * len(VIVOS))})", *VIVOS)]
    if not ids:
        return {}
    cx = cx or gcp.Compute()
    return {i: avanzar(i, cx) for i in ids}


def _hilo():
    """Un solo dueño del ciclo por máquina (candado de archivo), cada 60 s."""
    try:
        fh = open(os.path.join(os.path.dirname(BD), "demo_gcp.candado"), "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    while True:
        time.sleep(60)
        try:
            with app.app_context():
                ciclo()
        except Exception:  # noqa: BLE001  se reintenta en el siguiente minuto
            pass


if os.environ.get("FACEADMIN_DEMO_GCP_CICLO", "1") == "1":
    threading.Thread(target=_hilo, daemon=True, name="demo-gcp-ciclo").start()


# ---------------------------------------------------------------- rutas (FaceAdmin)
@app.route("/demos-nube/<int:did>/gcp", methods=["POST"])
@requiere_login
def demo_gcp_accion(did):
    solo_invoke()
    d = q1("SELECT * FROM demos_nube WHERE id=?", did) or abort(404)
    cliente_ok(d["cliente_id"])
    acc, u = request.form.get("accion"), g.usuario["usuario"]
    r = q1("SELECT * FROM demos_gcp WHERE demo_id=? AND estado<>'eliminada'", did)
    try:
        if acc == "crear":
            pedir(d, request.form, u)
            flash("Demo instantánea pedida: la VM se crea desde la imagen dorada y queda lista en minutos", "ok")
        elif not r:
            flash("Esta Demo no tiene VM en GCP", "error")
        elif acc == "encender":
            flash(encender(r["id"], u), "ok")
        elif acc == "apagar":
            apagar(r["id"], u)
            flash("Demo apagada", "ok")
        elif acc == "eliminar":
            eliminar_ya(r["id"], u)
            flash("Eliminando la VM, sus discos y su IP", "ok")
        elif acc == "horario":
            hi, hf, ina = int(request.form.get("hora_ini")), int(request.form.get("hora_fin")), int(request.form.get("inactividad_min"))
            if not (0 <= hi < hf <= 24) or not (10 <= ina <= 240):
                raise ValueError("Horario o inactividad fuera de rango")
            _zona(request.form.get("tz"))
            _act(r["id"], tz=request.form.get("tz", r["tz"]).strip(), dias_habiles=(request.form.get("dias_habiles") or "1-5").strip()[:20], hora_ini=hi,
                 hora_fin=hf, inactividad_min=ina, arranque_programado=1 if request.form.get("arranque_programado") else 0,
                 correo_aviso=(request.form.get("correo_aviso") or "").strip()[:160])
            _bit(r, "horario actualizado", {"dias": request.form.get("dias_habiles"), "ini": hi, "fin": hf, "inactividad": ina})
            flash("Horario guardado (la VM lo aplica en el próximo minuto)", "ok")
        elif acc == "clave":
            flash(f"Credenciales de la demo · usuario: {r['usuario_cliente']} · clave: {gcp.descifrar(r['clave_cifrada'])}", "ok")
            _bit(r, "credenciales consultadas", {"por": u})
        elif acc == "sincronizar":
            _en_fondo(r["id"])
            flash("Estado consultado a GCP", "ok")
    except (ValueError, TypeError, gcp.ErrorGCP) as e:
        flash(str(e), "error")
    return redirect(url_for("demo_nube", did=did))


@app.route("/configuracion/demo-gcp", methods=["GET", "POST"])
@requiere_login
def demo_gcp_config():
    """Proyecto, zona, imagen dorada y tipo de máquina; cuenta de servicio (JSON cifrado en archivo 600) con «Probar conexión»."""
    solo_invoke()
    if request.method == "POST":
        f, acc = request.form, request.form.get("accion")
        try:
            if acc == "clave":
                arch = request.files.get("sa")
                texto = (arch.read().decode("utf-8", "replace") if arch and arch.filename else "") or (f.get("sa_texto") or "")
                if not texto.strip():
                    raise gcp.ErrorGCP("Adjunta el archivo JSON de la cuenta de servicio")
                proyecto, correo = gcp.guardar_sa(texto.strip())
                bitacora("demo gcp · cuenta de servicio cargada", "invoke", {"proyecto": proyecto, "cuenta": correo})
                flash(f"Cuenta de servicio guardada cifrada ({correo}). Prueba la conexión.", "ok")
            elif acc == "borrar_clave":
                gcp.borrar_sa()
                bitacora("demo gcp · cuenta de servicio eliminada", "invoke", {})
                flash("Clave de la cuenta de servicio eliminada de FaceAdmin", "ok")
            elif acc == "probar":
                pasos = gcp.Compute().probar()
                ok = all(x[1] for x in pasos)
                flash(("Conexión correcta: " if ok else "La conexión falló: ") + " · ".join(f"{t} {'✓' if b else '✗'}{(' (' + m + ')') if m else ''}" for t, b, m in pasos),
                      "ok" if ok else "error")
                bitacora("demo gcp · prueba de conexión", "invoke", {"ok": ok})
            else:
                n = {k: (f.get(k) or "").strip() for k in ("proyecto", "zona", "imagen", "tipo", "red", "prefijo", "faceadmin_url", "tz", "dias_habiles")}
                for k in ("disco_gb", "inactividad_min", "dias_max", "dias_def", "aviso_horas", "hora_ini", "hora_fin"):
                    n[k] = int(f.get(k) or 0)
                n["arranque_programado"] = bool(f.get("arranque_programado"))
                if not (1 <= n["dias_max"] <= 14 and 1 <= n["dias_def"] <= n["dias_max"] and 30 <= n["disco_gb"] <= 500 and 10 <= n["inactividad_min"] <= 240 and 0 <= n["hora_ini"] < n["hora_fin"] <= 24):
                    raise gcp.ErrorGCP("Revisa los rangos: duración 1–14 días (por defecto ≤ máximo), disco 30–500 GB, inactividad 10–240 min, horario 0–24")
                if not gcp.nombre_valido(n["prefijo"].rstrip("-")):
                    raise gcp.ErrorGCP("El prefijo debe ser minúsculas, dígitos y guiones")
                _zona(n["tz"])
                gcp.guardar_config(n)
                bitacora("demo gcp · configuración", "invoke", {k: v for k, v in n.items()})
                flash("Configuración guardada", "ok")
        except (ValueError, gcp.ErrorGCP) as e:
            flash(str(e), "error")
        return redirect(url_for("demo_gcp_config"))
    filas = q("SELECT g.*, d.numero FROM demos_gcp g JOIN demos_nube d ON d.id=g.demo_id ORDER BY g.id DESC LIMIT 50")
    for r in filas:
        r["est_t"] = ESTADOS.get(r["estado"], (r["estado"], ""))
        r["expira_txt"] = local(r, r["expira"])
    return render_template("demo_gcp_config.html", cfg=gcp.config(), sa=gcp.sa_resumen(), filas=filas)


# ---------------------------------------------------------------- rutas públicas (cliente y VM)
@app.route("/demo/<token>", methods=["GET", "POST"])
def demo_gcp_publica(token):
    """«Encender mi demo»: el cliente entra con su enlace (sin cuenta en FaceAdmin) y enciende la VM en horario hábil."""
    if len(token) < 20:
        abort(404)
    r = q1("SELECT * FROM demos_gcp WHERE token_encender=? AND estado<>'eliminada'", token) or abort(404)
    if request.method == "POST":
        try:
            flash(encender(r["id"], "cliente (enlace)"), "ok")
        except (ValueError, gcp.ErrorGCP) as e:
            flash(str(e), "error")
        return redirect(url_for("demo_gcp_publica", token=token))
    d = _demo(r)
    return render_template("demo_gcp_publica.html", r=de_demo(r["demo_id"]), d=d, c=q1("SELECT nombre FROM clientes WHERE id=?", d.get("cliente_id")) or {})


def _vm_auth(numero):
    d = q1("SELECT id FROM demos_nube WHERE numero=?", numero) or abort(404)
    r = q1("SELECT * FROM demos_gcp WHERE demo_id=? AND estado<>'eliminada'", d["id"]) or abort(404)
    tok = request.headers.get("X-Demo-Token", "")
    if not tok or not r["token_vm_hash"] or not secrets.compare_digest(_h(tok), r["token_vm_hash"]):
        abort(403)
    return r


@app.route("/api/demo-gcp/<numero>/uso", methods=["POST"])
def api_demo_gcp_uso(numero):
    """Latido del agente de la VM: {activo, sesiones, http}. Responde si debe apagarse (fuera de horario, vencida o sin uso)."""
    r = _vm_auth(numero)
    x = request.get_json(silent=True) or {}
    now = RELOJ()
    cambios = {"ultimo_latido": now, "uso_detalle": json.dumps({k: x.get(k) for k in ("sesiones", "http", "ssh", "cpu")}, ensure_ascii=False)[:300]}
    if x.get("activo"):
        cambios["ultimo_uso"] = now
    _act(r["id"], **cambios)
    r = q1("SELECT * FROM demos_gcp WHERE id=?", r["id"])
    base = max(r["ultimo_uso"] or 0, r["encendida_desde"] or 0)
    motivo = ("vencida" if now >= r["expira"] else "fuera de horario" if not en_horario(r, now)
              else "sin uso" if now - base > int(r["inactividad_min"]) * 60 else "")
    return jsonify({"apagar": bool(motivo), "motivo": motivo, "expira": int(r["expira"]), "inactividad_min": r["inactividad_min"],
                    "horario": {"tz": r["tz"], "dias": r["dias_habiles"], "ini": r["hora_ini"], "fin": r["hora_fin"]}})


@app.route("/api/demo-gcp/<numero>/credenciales", methods=["GET"])
def api_demo_gcp_credenciales(numero):
    """Una sola vez, en el primer arranque: el usuario y la clave del cliente para que la VM los cree (FaceOps, AWX, Gitea, Grafana)."""
    r = _vm_auth(numero)
    if r["clave_entregada"]:
        return jsonify({"error": "las credenciales ya se entregaron"}), 410
    _act(r["id"], clave_entregada=RELOJ())
    _bit(r, "credenciales entregadas a la VM", {})
    return jsonify({"usuario": r["usuario_cliente"], "clave": gcp.descifrar(r["clave_cifrada"])})
