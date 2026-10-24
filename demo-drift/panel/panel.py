#!/usr/bin/python3
# Panel de cumplimiento: recibe eventos del vigía, lanza la autocorrección, registra y notifica.
import base64, difflib, html, json, os, smtplib, sqlite3, ssl, subprocess, threading, time, urllib.error, urllib.request, uuid
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "config.json")
lock_cfg = threading.Lock()


def cfg():
    with lock_cfg:
        return json.load(open(CFG_PATH, encoding="utf-8"))


def guardar_cfg(c):
    with lock_cfg:
        tmp = CFG_PATH + ".tmp"
        json.dump(c, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        os.replace(tmp, CFG_PATH)


def fecha(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d-%m-%Y %H:%M:%S")
    except (TypeError, ValueError):
        return iso or ""
DB = os.path.join(BASE, "panel.db")
lock_db = threading.Lock()
locks_host = {}


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS incidentes (
            id TEXT PRIMARY KEY, host TEXT, usuario TEXT, como TEXT, origen_ip TEXT, terminal TEXT,
            comandos TEXT, archivos TEXT, detectado TEXT, estado TEXT, correcciones TEXT,
            diff TEXT, restablecido TEXT, segundos REAL, respuesta TEXT, respondido TEXT, correo TEXT)""")
        if "git" not in [r[1] for r in c.execute("PRAGMA table_info(incidentes)")]:
            c.execute("ALTER TABLE incidentes ADD COLUMN git TEXT")


def ahora():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def actualizar(iid, **campos):
    with lock_db, db() as c:
        sets = ", ".join(f"{k}=?" for k in campos)
        c.execute(f"UPDATE incidentes SET {sets} WHERE id=?", (*campos.values(), iid))


def obtener(iid):
    with db() as c:
        r = c.execute("SELECT * FROM incidentes WHERE id=?", (iid,)).fetchone()
    return dict(r) if r else None


def nuevo_evento(ev):
    g = gestion_activa().get(ev.get("host"))
    if not g:
        return  # servidor fuera de gestión: el panel no lo vigila
    if cfg()["usuarios"].get(ev.get("usuario"), {}).get("visitante"):
        ev["origen_ip"] = "máquina principal (stand)"
    with lock_db, db() as c:
        c.execute("INSERT INTO incidentes (id, host, usuario, como, origen_ip, terminal, comandos, archivos, detectado, estado) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (ev["id"], ev["host"], ev["usuario"], ev.get("como", ""), ev.get("origen_ip", ""), ev.get("terminal", ""),
                   json.dumps(ev.get("comandos", [])), json.dumps(ev.get("archivos", [])), ahora(), "detectado"))
    if g.get("nivel") == "registrar":
        actualizar(ev["id"], estado="registrando")
        threading.Thread(target=registrar_evento, args=(ev["id"], ev), daemon=True).start()
        return
    if cfg().get("autocorregir", True):
        threading.Thread(target=corregir, args=(ev["id"], ev["host"]), daemon=True).start()
    if g.get("nivel") == "alertar":  # en "alertar" también queda la foto en Git (nada se corrige)
        threading.Thread(target=registrar_evento, args=(ev["id"], ev), daemon=True).start()


def corregir(iid, host):
    lock = locks_host.setdefault(host, threading.Lock())
    CFG = cfg()
    g = gestion_activa().get(host, {})
    alertar = g.get("nivel") == "alertar"
    autorizados = SUDO_BASE + (["visitantes"] if g.get("visitantes") and g.get("sudo") else [])
    with lock:
        actualizar(iid, estado="revisando" if alertar else "corrigiendo")
        cmd = ["ansible-playbook", "-i", CFG["inventario"], CFG["playbook"], "-l", host,
               "-e", json.dumps({"incidente_id": iid, "panel_url": "http://127.0.0.1:%d" % CFG["puerto"],
                                 "modo": "alertar" if alertar else "corregir", "sudo_autorizados": autorizados})]
        if alertar:
            cmd.append("--check")
        r = subprocess.run(cmd, cwd=CFG["directorio_playbooks"], capture_output=True, text=True)
        if r.returncode != 0:
            actualizar(iid, estado="error", diff=(r.stdout + r.stderr)[-4000:])


def unificar(cambios):
    salida = []
    for c in cambios:
        for d in (c if isinstance(c, list) else [c]):
            if not isinstance(d, dict):
                continue
            antes, despues = d.get("before", ""), d.get("after", "")
            if isinstance(antes, dict) or isinstance(despues, dict):
                antes, despues = json.dumps(antes), json.dumps(despues)
            salida.extend(difflib.unified_diff(str(antes).splitlines(), str(despues).splitlines(),
                                               d.get("before_header", "antes"), d.get("after_header", "después"), lineterm="", n=1))
    return "\n".join(salida)


def resultado(data):
    inc = obtener(data["id"])
    if not inc:
        return
    det = datetime.fromisoformat(inc["detectado"])
    fin = datetime.now().astimezone()
    cor = data.get("correcciones", [])
    if not cor and inc["usuario"] == PROGRAMADA:
        with lock_db, db() as c:
            c.execute("DELETE FROM incidentes WHERE id=?", (inc["id"],))
        return
    alertar = data.get("modo") == "alertar"
    actualizar(inc["id"], estado=("alertado" if alertar else "restablecido") if cor else "sin cambios", correcciones=json.dumps(cor),
               diff=unificar(data.get("cambios", [])), restablecido=fin.isoformat(timespec="seconds"),
               segundos=round((fin - det).total_seconds(), 1))
    if cor:
        threading.Thread(target=notificar, args=(inc["id"],), daemon=True).start()


def notificar(iid):
    CFG = cfg()
    inc = obtener(iid)
    persona = CFG["usuarios"].get(inc["usuario"], {"nombre": inc["usuario"], "correo": ""})
    admin = gestion_activa().get(inc["host"], {}).get("correo") or CFG["correo_operaciones"]
    solo_admin = CFG.get("aviso_solo_admin", True)
    alertado = inc["estado"] == "alertado"
    accion = ('<b style="color:#b54708">NO fue corregido</b>: este servidor está en nivel “vigilar y alertar” y '
              'requiere revisión' if alertado else
              f'<b style="color:#067647">restablecido automáticamente en {inc["segundos"]} s</b>')
    url = CFG["url_publica"]
    diff_html = "".join(
        f'<div style="color:{"#b42318" if l.startswith("-") and not l.startswith("---") else "#067647" if l.startswith("+") and not l.startswith("+++") else "#555"};font-family:monospace;font-size:12px;white-space:pre">{html.escape(l)}</div>'
        for l in (inc["diff"] or "").splitlines())
    cor = "".join(f"<li>{html.escape(c)}</li>" for c in json.loads(inc["correcciones"] or "[]"))
    comandos = "<br>".join(html.escape(c) for c in json.loads(inc["comandos"] or "[]"))
    if solo_admin and not persona.get("visitante"):
        saludo = f"Hola {html.escape(CFG.get('nombre_admin', 'administrador'))},"
        intro = (f"<b>{html.escape(persona['nombre'])}</b> (<code>{inc['usuario']}</code>) hizo un cambio manual en "
                 f"<b>{inc['host']}</b> fuera del proceso estándar (Git + Ansible). El servidor {'' if alertado else 'ya fue '}"
                 f"{accion}. Revisa el detalle y decide si corresponde conversar con esta persona.")
        boton = "Registrar acción tomada"
        asunto = (f"[Alerta] {persona['nombre']} hizo un cambio no autorizado en {inc['host']}"
                  + (" (sin corregir)" if alertado else ""))
        para, copia = admin, ""
    else:
        saludo = f"Hola {html.escape(persona['nombre'])},"
        intro = (("Gracias por probar la demo de Invoke en el Red Hat Summit. " if persona.get("visitante") else "") + f"Se detectó un cambio manual tuyo en <b>{inc['host']}</b> fuera del proceso estándar (Git + Ansible). "
                 f"El servidor {'' if alertado else 'ya fue '}{accion}.")
        boton = "Explicar el motivo del cambio"
        asunto = f"[Acción requerida] Cambio no autorizado revertido en {inc['host']}"
        para, copia = persona["correo"] or admin, admin
    fila = lambda k, v: f'<tr><td style="padding:3px 12px 3px 0;color:#667085;vertical-align:top">{k}</td><td>{v}</td></tr>'
    cuerpo = f"""<div style="font-family:Arial,sans-serif;max-width:640px">
<div style="background:#0b1f3a;color:#fff;padding:16px 20px;font-size:18px"><b>Invoke</b> · Cumplimiento de configuración</div>
<div style="padding:20px;border:1px solid #e4e7ec">
<p>{saludo}</p><p>{intro}</p>
<table style="font-size:13px;border-collapse:collapse">
{fila("Originado por", f"{html.escape(persona['nombre'])} ({inc['usuario']}, actuó como {inc['como']})")}
{fila("Origen", inc['origen_ip'])}
{fila("Servidor", inc['host'])}
{fila("Comandos recientes", f"<code>{comandos}</code>")}
{fila("Archivos", ', '.join(json.loads(inc['archivos'])))}
{fila("Detectado", fecha(inc['detectado']))}</table>
<p><b>{"Diferencias detectadas" if alertado else "Correcciones aplicadas"}</b></p><ul style="font-size:13px">{cor}</ul>
<p><b>{"Cambios respecto del estándar" if alertado else "Diferencias revertidas"}</b></p><div style="background:#f9fafb;border:1px solid #e4e7ec;padding:10px">{diff_html}</div>
<p style="margin-top:20px"><a href="{url}/incidente/{iid}" style="background:#0b1f3a;color:#fff;padding:10px 16px;text-decoration:none;border-radius:4px">Ver en el panel</a>
&nbsp;<a href="{url}/responder/{iid}" style="color:#d92d20">{boton}</a></p>
<p style="font-size:12px;color:#667085">Si el cambio era necesario, debe solicitarse por Git ({CFG['repo_url']}) para que quede revisado y versionado.</p>
</div></div>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = CFG["correo_remitente"]
    msg["To"] = para
    if copia:
        msg["Cc"] = copia
    msg.attach(MIMEText(cuerpo, "html", "utf-8"))
    destinos = [d for d in {para, copia} if d]
    try:
        with smtplib.SMTP(CFG["smtp_host"], CFG["smtp_puerto"], timeout=15) as s:
            if CFG.get("smtp_tls"):
                s.starttls()
            s.sendmail(msg["From"], destinos, msg.as_string())
        actualizar(iid, correo=f"aviso enviado a {', '.join(destinos)}")
    except (OSError, smtplib.SMTPException) as e:
        actualizar(iid, correo=f"error: {e}")


# ---------- Gestión de servidores: qué servidores vigila el panel y con qué nivel ----------
SUDO_BASE = ["90-cloud-init-users", "admins", "grupo-admin", "operador"]
PROGRAMADA = "revisión programada"
lock_gestion = threading.Lock()
IP = {"nodo-nginx": 11, "nodo-so": 12, "nodo-postgres": 13, "nodo-tomcat": 14, "nodo-jboss": 15}


def defecto_so(c):
    return {"nivel": "corregir", "visitantes": True, "sudo": True, "grupo_admin": "wheel", "frecuencia": 0,
            "correo": c.get("correo_operaciones", ""), "estado": "activo", "desde": ahora()}


def gestion_activa():
    return {h: g for h, g in cfg().get("gestion", {}).items() if g.get("estado") in ("activo", "retirando")}


def cambiar_gestion(host, datos=None, quitar=False):
    with lock_gestion:
        c = cfg()
        ges = c.setdefault("gestion", {})
        if quitar:
            ges.pop(host, None)
        else:
            ges[host] = dict(ges.get(host, {}), **(datos or {}))
        guardar_cfg(c)


def migrar_gestion():
    c = cfg()
    if "gestion" not in c:
        c["gestion"] = {}  # al activar, reiniciar-panel.sh incorpora nodo-so con AAP
        guardar_cfg(c)


def aap(m, ruta, datos=None):
    a = cfg().get("aap", {})
    clave = next((l.split("=", 1)[1].strip() for l in open(os.path.expanduser(a.get("secretos", "~/demo-drift/cicd-secretos.txt")))
                  if l.startswith(a.get("clave_en_secretos", "panel_aap_clave") + "=")), "")
    url = a.get("url", "https://localhost/api/controller/v2") + "/" + ruta
    req = urllib.request.Request(url, method=m, data=json.dumps(datos).encode() if datos is not None else None, headers={
        "Content-Type": "application/json",
        "Authorization": "Basic " + base64.b64encode(f"{a.get('usuario', 'panel-drift')}:{clave}".encode()).decode()})
    with urllib.request.urlopen(req, context=ssl._create_unverified_context(), timeout=30) as r:
        b = r.read()
        return json.loads(b) if b else {}


def aap_id(ruta, nombre, extra=""):
    r = aap("GET", f"{ruta}/?name=" + urllib.request.quote(nombre) + extra)["results"]
    return r[0]["id"] if r else None


def operar(host, accion, datos):
    """Lanza en AAP la plantilla de incorporar o retirar y sigue el job hasta que termina."""
    a = cfg().get("aap", {})
    try:
        inv = aap_id("inventories", a.get("inventario", "Drift · Servidores"))
        hid = aap_id("hosts", host, f"&inventory={inv}")
        gid = aap_id("groups", "gestionados", f"&inventory={inv}")
        jt = aap_id("job_templates", a.get("plantilla_incorporar" if accion == "incorporar" else "plantilla_retirar",
                                           "Drift · Incorporar servidor" if accion == "incorporar" else "Drift · Retirar servidor"))
        variables = {"ansible_host": f"192.168.122.{IP.get(host, 0)}"}
        extra = {"servidor": host, "solicitado_por": datos.get("operador", "panel")}
        if accion == "incorporar":
            variables.update({"nivel_control": datos["nivel"], "visitantes_permitidos": datos["visitantes"],
                              "visitantes_sudo": datos["sudo"], "grupo_admin": datos["grupo_admin"],
                              "frecuencia_min": datos["frecuencia"], "correo_alertas": datos["correo"]})
            extra.update({k: v for k, v in variables.items() if k != "ansible_host"})
            aap("PATCH", f"hosts/{hid}/", {"variables": json.dumps(variables)})
            try:
                aap("POST", f"groups/{gid}/hosts/", {"id": hid})
            except urllib.error.HTTPError:
                pass
        job = aap("POST", f"job_templates/{jt}/launch/", {"limit": host, "extra_vars": extra})["id"]
        cambiar_gestion(host, {"job": job})
        while True:
            time.sleep(2)
            st = aap("GET", f"jobs/{job}/")["status"]
            if st in ("successful", "failed", "error", "canceled"):
                break
        if st != "successful":
            cambiar_gestion(host, {"estado": "error", "mensaje": f"El job {job} terminó en {st}"})
            return
        if accion == "incorporar":
            cambiar_gestion(host, {"estado": "activo", "desde": ahora(), "mensaje": "", "ultima_revision": ahora()})
            if datos.get("nivel") in ("registrar", "alertar"):  # foto inicial en Git
                persona = cfg()["usuarios"].get(datos.get("operador"), {})
                registrar_foto([host], f"{host} · foto inicial · incorporado por {datos.get('operador', 'panel')} · "
                               f"{datetime.now().strftime('%H:%M')}", persona.get("nombre", datos.get("operador", "panel")),
                               persona.get("correo", ""))
        else:
            try:
                aap("POST", f"groups/{gid}/hosts/", {"id": hid, "disassociate": True})
                aap("PATCH", f"hosts/{hid}/", {"variables": json.dumps({"ansible_host": variables["ansible_host"]})})
            except urllib.error.HTTPError:
                pass
            cambiar_gestion(host, quitar=True)
    except Exception as e:  # el panel debe seguir vivo aunque AAP no responda
        print("operar", host, accion, e, flush=True)
        cambiar_gestion(host, {"estado": "error", "mensaje": "No se pudo contactar a AAP"})


# ---------- Estado en Git: "Solo registrar" y propagación entre servidores ----------
JT_REGISTRAR, JT_PROPAGAR = "Estado · Registrar servidor", "Estado · Propagar referencia"
PROP = {}  # propagaciones en curso: id -> estado, resultados por servidor, commit


def lanzar_job(plantilla, extra, job_type=None):
    """Lanza una plantilla de AAP y espera a que termine. Devuelve (job, estado)."""
    jt = aap_id("job_templates", plantilla)
    datos = {"extra_vars": extra}
    if job_type:
        datos["job_type"] = job_type
    job = aap("POST", f"job_templates/{jt}/launch/", datos)["id"]
    while True:
        time.sleep(2)
        st = aap("GET", f"jobs/{job}/")["status"]
        if st in ("successful", "failed", "error", "canceled"):
            return job, st


def url_commit(sha):
    return cfg().get("gitea_estado", "") + "/commit/" + sha


def panel_aap():
    c = cfg()
    return c.get("panel_url_aap", "http://192.168.122.1:%d" % c["puerto"])


def registrar_foto(servidores, mensaje, autor_nombre, autor_correo, registro_id=""):
    try:
        job, st = lanzar_job(JT_REGISTRAR, {"servidores": servidores, "mensaje": mensaje, "autor_nombre": autor_nombre,
                                            "autor_correo": autor_correo or "registro-estado@invoke.local",
                                            "registro_id": registro_id, "panel_url": panel_aap()})
    except Exception as e:  # el panel debe seguir vivo aunque AAP no responda
        print("registrar", servidores, e, flush=True)
        job, st = None, "error"
    if st != "successful" and registro_id:
        inc = obtener(registro_id)
        if inc and inc["estado"] == "registrando":
            actualizar(registro_id, estado="error", diff=f"El registro en Git (job {job}) terminó en {st}")


def registrar_evento(iid, ev):
    persona = cfg()["usuarios"].get(ev.get("usuario"), {})
    cmds = [c.split("  ", 1)[-1] for c in ev.get("comandos", [])]
    archivos = ev.get("archivos", [])
    claves = set(archivos) | {os.path.basename(a) for a in archivos if os.path.basename(a)}
    # el comando que tocó los archivos cambiados; si no quedó registrado, se nombran los archivos
    principal = next((c for c in reversed(cmds) if any(k in c for k in claves)), None) \
        or ("modificó " + ", ".join(archivos) if archivos else "cambio manual")
    mensaje = f"{ev['host']} · {ev.get('usuario', '?')} · {principal[:90]} · {datetime.now().strftime('%H:%M')}"
    registrar_foto([ev["host"]], mensaje, persona.get("nombre", ev.get("usuario", "?")),
                   persona.get("correo", f"{ev.get('usuario', 'persona')}@invoke.local"), iid)


def resultado_registro(d):
    """Lo informa el playbook de registro al terminar el commit."""
    sha, sin = d.get("sha", ""), str(d.get("sin_cambios")).lower() == "true"
    url = url_commit(sha) if sha else ""
    rid = d.get("id") or ""
    if rid.startswith("prop-"):
        p = PROP.get(rid[5:])
        if p:
            p["commit"] = "" if sin else url
        return
    if rid:
        inc = obtener(rid)
        if inc:
            campos = {"git": "sin cambios" if sin else url}
            if inc["estado"] == "registrando":
                fin = datetime.now().astimezone()
                campos.update(estado="sin cambios" if sin else "registrado", restablecido=fin.isoformat(timespec="seconds"),
                              segundos=round((fin - datetime.fromisoformat(inc["detectado"])).total_seconds(), 1))
            actualizar(rid, **campos)
    if not sin:
        for h in d.get("servidores", []):
            if cfg().get("gestion", {}).get(h):
                cambiar_gestion(h, {"ultimo_commit": url, "ultimo_commit_hora": ahora()})


def pedir_propagar(f):
    c = cfg()
    ref = f.get("referencia", "")
    nodos = [n["nombre"] for n in c["nodos"]]
    dest = [d for d in f.get("destinos", []) if d in nodos and d != ref]
    elem = [e for e in f.get("elementos", []) if e in ("sshd_config", "sudoers", "opt_apps")]
    if ref not in nodos or not dest or not elem:
        return {"error": "Elige la referencia, al menos un destino y qué propagar"}
    pid = uuid.uuid4().hex[:8]
    modo = "aplicar" if f.get("modo") == "aplicar" else "vista"
    PROP[pid] = {"id": pid, "estado": "en curso", "modo": modo, "referencia": ref, "destinos": dest, "elementos": elem,
                 "resultados": {}, "commit": "", "job": None, "inicio": ahora()}
    persona = c["usuarios"].get(c.get("usuario_admin", "cquezada"), {})

    def correr():
        try:
            jt = aap_id("job_templates", JT_PROPAGAR)
            job = aap("POST", f"job_templates/{jt}/launch/", {
                "job_type": "check" if modo == "vista" else "run",
                "extra_vars": {"referencia": ref, "destinos": dest, "elementos": elem, "propagacion_id": pid,
                               "panel_url": panel_aap(), "solicitado_por": c.get("usuario_admin", "cquezada"),
                               "autor_nombre": persona.get("nombre", "panel"),
                               "autor_correo": persona.get("correo", "registro-estado@invoke.local")}})["id"]
            PROP[pid]["job"] = job
            while True:
                time.sleep(2)
                st = aap("GET", f"jobs/{job}/")["status"]
                if st in ("successful", "failed", "error", "canceled"):
                    break
            PROP[pid]["estado"] = "listo" if st == "successful" else f"error (job {job}: {st})"
        except Exception as e:
            print("propagar", e, flush=True)
            PROP[pid]["estado"] = "error: no se pudo contactar a AAP"

    threading.Thread(target=correr, daemon=True).start()
    return {"id": pid}


def resultado_propagacion(d):
    p = PROP.get(d.get("id", ""))
    if not p:
        return
    p["resultados"] = {h: {"cambios": r.get("cambios", []), "diff": unificar(r.get("diffs", []))}
                       for h, r in (d.get("resultados") or {}).items()}


def pedir_incorporar(f):
    c = cfg()
    host = f.get("servidor", "")
    if host not in [n["nombre"] for n in c["nodos"]]:
        return "Servidor desconocido"
    actual = c.get("gestion", {}).get(host, {})
    if actual.get("estado") in ("activo", "incorporando", "retirando"):
        return f"{host} ya está bajo gestión"
    visit = str(f.get("visitantes", "")).lower() in ("1", "true", "si", "sí", "on")
    datos = {"nivel": f.get("nivel") if f.get("nivel") in ("alertar", "registrar") else "corregir", "visitantes": visit,
             "sudo": visit and str(f.get("sudo", "")).lower() in ("1", "true", "si", "sí", "on"),
             "grupo_admin": (f.get("grupo_admin") or "wheel").strip()[:32], "frecuencia": int(f.get("frecuencia") or 0),
             "correo": (f.get("correo") or c.get("correo_operaciones", "")).strip()[:120],
             "operador": c.get("usuario_admin", "cquezada")}
    cambiar_gestion(host, dict(datos, estado="incorporando", job=None, mensaje="", pedido=ahora()))
    threading.Thread(target=operar, args=(host, "incorporar", datos), daemon=True).start()
    return ""


def pedir_retirar(host):
    g = cfg().get("gestion", {}).get(host)
    if not g:
        return f"{host} no está bajo gestión"
    if g.get("estado") == "error":
        cambiar_gestion(host, quitar=True)
        return ""
    if g.get("estado") != "activo":
        return f"{host} tiene una operación en curso"
    cambiar_gestion(host, {"estado": "retirando", "job": None})
    threading.Thread(target=operar, args=(host, "retirar", {"operador": cfg().get("usuario_admin", "cquezada")}), daemon=True).start()
    return ""


def reiniciar_gestion():
    c = cfg()
    so = c.get("nodo_visitante", "nodo-so")
    for n in c["nodos"]:
        h, g = n["nombre"], c.get("gestion", {}).get(n["nombre"])
        if h == so or (g and g.get("estado") in ("incorporando", "retirando")):
            continue
        # todo servidor distinto de nodo-so se retira con AAP (también limpia visitantes antiguos)
        cambiar_gestion(h, {"estado": "activo", "nivel": "corregir", "visitantes": False, "sudo": False,
                            "grupo_admin": "wheel", "frecuencia": 0} if not g or g.get("estado") == "error" else {})
        pedir_retirar(h)
    g = cfg().get("gestion", {}).get(so)
    if not g or g.get("estado") == "error" or any(g.get(k) != v for k, v in defecto_so(c).items() if k not in ("estado", "desde", "correo")):
        if g and g.get("estado") == "activo":
            cambiar_gestion(so, quitar=True)
        d = defecto_so(c)
        pedir_incorporar({"servidor": so, "nivel": d["nivel"], "visitantes": "si", "sudo": "si",
                          "grupo_admin": d["grupo_admin"], "frecuencia": 0, "correo": d["correo"]})


def revisiones_programadas():
    """Además del vigía en tiempo real, revisa cada N minutos los servidores que lo pidieron."""
    while True:
        time.sleep(20)
        try:
            for h, g in gestion_activa().items():
                if g.get("estado") != "activo" or not g.get("frecuencia"):
                    continue
                ult = g.get("ultima_revision") or g.get("desde")
                if ult and (datetime.now().astimezone() - datetime.fromisoformat(ult)).total_seconds() < g["frecuencia"] * 60:
                    continue
                cambiar_gestion(h, {"ultima_revision": ahora()})
                if g.get("nivel") == "registrar":
                    threading.Thread(target=registrar_foto, args=([h], f"{h} · revisión programada · {datetime.now().strftime('%H:%M')}",
                                                                  "registro-estado", ""), daemon=True).start()
                    continue
                iid = uuid.uuid4().hex[:10]
                with lock_db, db() as c:
                    c.execute("INSERT INTO incidentes (id, host, usuario, como, origen_ip, terminal, comandos, archivos, detectado, estado) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?)", (iid, h, PROGRAMADA, "AAP", "revisión cada %d min" % g["frecuencia"],
                                                               "", "[]", "[]", ahora(), "detectado"))
                threading.Thread(target=corregir, args=(iid, h), daemon=True).start()
        except Exception as e:
            print("revisión programada:", e, flush=True)


ESTILO = """<style>
:root{--bg:#f5f7fa;--card:#fff;--tx:#101828;--mut:#667085;--bd:#e4e7ec;--ok:#067647;--okbg:#ecfdf3;--warn:#b54708;--warnbg:#fffaeb;--bad:#b42318;--badbg:#fef3f2;--brand:#0b1f3a}
@media (prefers-color-scheme:dark){:root{--bg:#0c111d;--card:#161b26;--tx:#f5f5f6;--mut:#94969c;--bd:#2a303c;--okbg:#053321;--warnbg:#4e1d09;--badbg:#55160c;--ok:#47cd89;--warn:#fdb022;--bad:#f97066}}
*{box-sizing:border-box}body{margin:0;font-family:Inter,Arial,sans-serif;background:var(--bg);color:var(--tx)}
header{background:var(--brand);color:#fff;padding:14px 24px;display:flex;justify-content:space-between;align-items:center}
header b{font-size:18px}main{padding:20px 24px;max-width:1300px;margin:auto}
.nodos{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:22px}
.nodo{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px;border-left:6px solid var(--ok)}
.nodo.corrigiendo,.nodo.detectado{border-left-color:var(--warn)}.nodo.error{border-left-color:var(--bad)}
.nodo h3{margin:0 0 4px;font-size:15px}.nodo small{color:var(--mut)}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}
.restablecido,.sin{background:var(--okbg);color:var(--ok)}.detectado,.corrigiendo{background:var(--warnbg);color:var(--warn)}.error{background:var(--badbg);color:var(--bad)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--bd);border-radius:10px;overflow:hidden;font-size:13px}
th{text-align:left;color:var(--mut);font-weight:600;padding:10px;border-bottom:1px solid var(--bd)}td{padding:10px;border-bottom:1px solid var(--bd);vertical-align:top}
tr.fila{cursor:pointer}tr.fila:hover{background:var(--bg)}code{font-size:12px}.mut{color:var(--mut)}
pre{background:var(--bg);border:1px solid var(--bd);padding:12px;border-radius:8px;overflow:auto;font-size:12px}
.del{color:var(--bad)}.add{color:var(--ok)}a{color:inherit}
@media(max-width:700px){main{padding:12px 16px}td:nth-child(4),th:nth-child(4),td:nth-child(6),th:nth-child(6){display:none}}
</style>"""

PAGINA = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cumplimiento de configuración</title>""" + ESTILO + """<style>
.cab{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin:0 0 10px}
.cab h2{margin:0;font-size:17px}.cab p{margin:0;color:var(--mut);font-size:13px}
.nodo .acc{font-size:12px;color:var(--mut);margin:8px 0 0;line-height:1.5}
.chip{display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;border:1px solid var(--bd);margin:6px 4px 0 0}
.chip.corr{background:var(--okbg);color:var(--ok);border-color:transparent}.chip.alert{background:var(--warnbg);color:var(--warn);border-color:transparent}
.nodo .pie{display:flex;justify-content:space-between;align-items:center;margin-top:10px;gap:8px}
.btn{font:600 13px Inter,Arial,sans-serif;border-radius:6px;padding:7px 12px;cursor:pointer;border:1px solid var(--bd);background:var(--card);color:var(--tx)}
.btn.pri{background:var(--brand);color:#fff;border-color:var(--brand)}.btn.pel{color:var(--bad)}.btn.pel.conf{background:var(--bad);color:#fff;border-color:var(--bad)}
.agregar{border:2px dashed var(--bd);border-radius:10px;display:grid;place-items:center;min-height:150px;cursor:pointer;background:transparent;color:var(--mut);font:600 15px Inter,Arial,sans-serif}
.agregar:hover{border-color:var(--brand);color:var(--tx)}
.nodo.incorporando,.nodo.retirando{border-left-color:#2e90fa}.incorporando,.retirando,.revisando{background:#eff8ff;color:#175cd3}
.alertado{background:var(--warnbg);color:var(--warn)}
.chip.reg{background:#eff8ff;color:#175cd3;border-color:transparent}.registrado{background:#eff8ff;color:#175cd3}.registrando{background:#eff8ff;color:#175cd3}
.nodo .git{font-size:12px;margin-top:6px}.nodo .git a{color:#175cd3}
.btn.sec{font-size:12px;padding:5px 9px}
.prev{max-height:340px;overflow:auto;display:grid;gap:10px}.prev h4{margin:0 0 4px;font-size:13px}.prev ul{margin:0 0 6px;padding-left:18px;font-size:12px}
.prev pre{margin:0;max-height:200px}.nota{font-size:12px;color:var(--mut)}
dialog{border:1px solid var(--bd);border-radius:12px;padding:0;max-width:520px;width:calc(100% - 32px);background:var(--card);color:var(--tx)}
dialog::backdrop{background:rgba(16,24,40,.55)}
dialog form{padding:20px 22px;display:grid;gap:14px}dialog h3{margin:0}
fieldset{border:0;padding:0;margin:0;display:grid;gap:6px}legend{font-weight:600;font-size:13px;margin-bottom:6px}
.op{display:flex;gap:8px;flex-wrap:wrap}.op label{border:1px solid var(--bd);border-radius:8px;padding:8px 10px;cursor:pointer;font-size:13px;flex:1;min-width:150px}
.op label:has(input:checked){border-color:var(--brand);box-shadow:inset 0 0 0 1px var(--brand)}
.op small{display:block;color:var(--mut);font-weight:400;margin-top:2px}
select,input[type=email],input[type=text]{font:14px Inter,Arial,sans-serif;padding:8px;border:1px solid var(--bd);border-radius:6px;background:var(--card);color:var(--tx);width:100%}
.fila2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.fila2 label{display:grid;gap:6px;font-size:13px;font-weight:600}.chk{display:flex;gap:8px;align-items:center;font-size:14px}
.acciones{display:flex;justify-content:flex-end;gap:8px}
</style></head><body>
<header><b>Invoke · Cumplimiento de configuración</b><span><a href="/visitante" style="color:#fff;margin-right:16px">Visitantes</a><a href="/personas" style="color:#fff;margin-right:16px">Personas y correos</a><span id="reloj" style="color:#cbd5e1"></span></span></header>
<main>
<div class="cab"><h2>Servidores bajo gestión</h2><p>Agrega o quita servidores cuando quieras; Ansible aplica el nivel de control y los accesos que elijas.</p></div>
<div class="nodos" id="nodos"></div>
<div class="cab"><h2>Cambios detectados</h2></div>
<table><thead><tr><th>Detectado</th><th>Servidor</th><th>Usuario</th><th>Origen</th><th>Archivos</th><th>Comandos recientes</th><th>Estado</th><th>Tiempo</th><th>Seguimiento</th></tr></thead>
<tbody id="filas"></tbody></table></main>
<dialog id="dlg"><form id="frm">
<h3>Agregar servidor a la gestión</h3>
<fieldset><legend>Servidor</legend><select name="servidor" id="srv" required></select></fieldset>
<fieldset><legend>Nivel de control</legend><div class="op">
<label><input type="radio" name="nivel" value="corregir" checked> Vigilar y corregir<small>Revierte al estándar en segundos y avisa</small></label>
<label><input type="radio" name="nivel" value="alertar"> Vigilar y alertar<small>Solo avisa; una persona decide</small></label>
<label><input type="radio" name="nivel" value="registrar"> Solo registrar<small>Cada cambio queda como commit en Git; no corrige ni alerta</small></label></div></fieldset>
<fieldset><legend>Accesos</legend>
<label class="chk"><input type="checkbox" name="visitantes" id="vis" checked> Los visitantes pueden entrar</label>
<label class="chk"><input type="checkbox" name="sudo" id="sudo" checked> Con permisos de administrador (sudo)</label>
<div class="fila2"><label>Grupo administrador<select name="grupo_admin"><option value="wheel">wheel</option><option value="operaciones">operaciones</option><option value="dba">dba</option><option value="middleware">middleware</option></select></label>
<label>Revisión completa<select name="frecuencia"><option value="0">Solo en tiempo real</option><option value="5">Además cada 5 min</option><option value="15">Además cada 15 min</option></select></label></div></fieldset>
<fieldset><legend>Correo de alertas</legend><input type="email" name="correo" id="correo" required></fieldset>
<div class="acciones"><button type="button" class="btn" id="cancelar">Cancelar</button><button class="btn pri">Agregar con Ansible</button></div>
</form></dialog>
<dialog id="dlg2"><form id="frm2">
<h3>Usar <span id="pref"></span> como referencia</h3>
<p class="nota" style="margin:0">Los servidores destino quedan iguales a la referencia en lo que elijas. Cada uno conserva sus accesos de visitantes y su grupo administrador.</p>
<fieldset><legend>Servidores destino (RHEL similares)</legend><div class="op" id="pdest"></div></fieldset>
<fieldset><legend>Qué propagar</legend><div class="op">
<label><input type="checkbox" name="el" value="sshd_config" checked> sshd_config<small>Archivo completo</small></label>
<label><input type="checkbox" name="el" value="sudoers" checked> Permisos sudo<small>/etc/sudoers.d</small></label>
<label><input type="checkbox" name="el" value="opt_apps" checked> /opt/apps<small>Carpetas</small></label></div></fieldset>
<div id="pres" class="prev"></div>
<div class="acciones"><button type="button" class="btn" id="pcerrar">Cerrar</button><button type="button" class="btn" id="pvista">Vista previa</button><button type="button" class="btn pri" id="paplicar" disabled>Aplicar con Ansible</button></div>
</form></dialog>
<script>
const e=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const hora=s=>s?new Date(s).toLocaleTimeString('es-CL'):'';
let G={nodos:[],gestion:{}},confirmar=null;
const dlg=document.getElementById('dlg'),frm=document.getElementById('frm');
document.getElementById('vis').onchange=ev=>{const s=document.getElementById('sudo');s.disabled=!ev.target.checked;if(!ev.target.checked)s.checked=false};
document.getElementById('cancelar').onclick=()=>dlg.close();
function abrir(){const libres=G.nodos.filter(n=>!G.gestion[n.nombre]);if(!libres.length)return;
 document.getElementById('srv').innerHTML=libres.map(n=>`<option value="${e(n.nombre)}">${e(n.nombre)} · ${e(n.rol)}</option>`).join('');
 document.getElementById('correo').value=G.correo||'';dlg.showModal()}
frm.onsubmit=async ev=>{ev.preventDefault();const f=Object.fromEntries(new FormData(frm));
 f.visitantes=document.getElementById('vis').checked;f.sudo=document.getElementById('sudo').checked;
 const r=await (await fetch('/api/gestion/agregar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(f)})).json();
 if(r.error)alert(r.error);dlg.close();cargar()};
async function quitar(h){if(confirmar!==h){confirmar=h;pintar();setTimeout(()=>{if(confirmar===h){confirmar=null;pintar()}},4000);return}
 confirmar=null;await fetch('/api/gestion/quitar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({servidor:h})});cargar()}
const dlg2=document.getElementById('dlg2');let PR={ref:null,vista:null,timer:null};
document.getElementById('pcerrar').onclick=()=>{clearInterval(PR.timer);dlg2.close();cargar()};
function referencia(h){PR={ref:h,vista:null,timer:null};document.getElementById('pref').textContent=h;
 document.getElementById('pdest').innerHTML=(G.similares||[]).filter(n=>n!==h).map(n=>{const g=G.gestion[n];
  const aviso=g&&g.nivel==='corregir'?'<small>En “corregir”: Git manda y revertirá lo que difiera</small>':(g?'<small>Bajo gestión · '+e(g.nivel)+'</small>':'<small>Fuera de gestión</small>');
  return `<label><input type="checkbox" name="d" value="${e(n)}" checked> ${e(n)}${aviso}</label>`}).join('');
 document.getElementById('pres').innerHTML='';document.getElementById('paplicar').disabled=true;dlg2.showModal()}
function selProp(){const f=document.getElementById('frm2');return{referencia:PR.ref,
 destinos:[...f.querySelectorAll('input[name=d]:checked')].map(i=>i.value),elementos:[...f.querySelectorAll('input[name=el]:checked')].map(i=>i.value)}}
function pintaDiff(t){return(t||'').split('\\n').map(l=>`<div class="${l.startsWith('-')&&!l.startsWith('---')?'del':l.startsWith('+')&&!l.startsWith('+++')?'add':''}">${e(l)||'&nbsp;'}</div>`).join('')}
async function propagar(modo){const d=Object.assign(selProp(),{modo});
 const r=await (await fetch('/api/propagar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)})).json();
 const box=document.getElementById('pres');if(r.error){box.innerHTML=`<p class="badge error">${e(r.error)}</p>`;return}
 document.getElementById('paplicar').disabled=true;document.getElementById('pvista').disabled=true;
 clearInterval(PR.timer);PR.timer=setInterval(async()=>{const p=await (await fetch('/api/propagacion/'+r.id)).json();
  const titulo=modo==='vista'?'Vista previa (AAP en modo revisión, no cambia nada)':'Aplicado con Ansible';
  let h=`<div class="nota"><b>${titulo}</b> · ${p.job?`job <a href="${G.aap_ui}/execution/jobs/playbook/${p.job}/output" target="_blank">#${p.job}</a>`:'lanzando…'} · ${e(p.estado)}</div>`;
  h+=Object.entries(p.resultados||{}).map(([s,x])=>`<div><h4>${e(s)} · ${x.cambios.length?x.cambios.length+' diferencia(s)':'ya es igual a la referencia'}</h4>
   ${x.cambios.length?'<ul>'+x.cambios.map(c=>'<li>'+e(c)+'</li>').join('')+'</ul>':''}${x.diff?'<pre>'+pintaDiff(x.diff)+'</pre>':''}</div>`).join('');
  if(p.commit)h+=`<div class="nota">Registrado en Git: <a href="${e(p.commit)}" target="_blank">ver commit</a></div>`;
  document.getElementById('pres').innerHTML=h;
  if(p.estado!=='en curso'){clearInterval(PR.timer);document.getElementById('pvista').disabled=false;
   if(modo==='vista'&&p.estado==='listo')document.getElementById('paplicar').disabled=false}},1500)}
document.getElementById('pvista').onclick=()=>propagar('vista');
document.getElementById('paplicar').onclick=()=>propagar('aplicar');
let D=[];
function pintar(){
 const ges=G.gestion,lista=G.nodos.filter(n=>ges[n.nombre]);
 document.getElementById('nodos').innerHTML=lista.map(n=>{const g=ges[n.nombre];
  const u=D.find(i=>i.host===n.nombre);const est=u?u.estado:'';
  const activo=u&&['detectado','corrigiendo','revisando','error','alertado','registrando','registrado'].includes(est);
  let badge,clase='';
  if(g.estado==='incorporando'||g.estado==='retirando'){clase=g.estado;badge=`<span class="badge ${g.estado}">${g.estado==='incorporando'?'Ansible aplicando la política':'Ansible retirando'}${g.job?` · job <a href="${G.aap_ui}/execution/jobs/playbook/${g.job}/output" target="_blank">#${g.job}</a>`:''}</span>`}
  else if(g.estado==='error'){clase='error';badge=`<span class="badge error">${e(g.mensaje||'error')}</span>`}
  else{clase=activo?est:'';badge=`<span class="badge ${activo?est:'restablecido'}">${activo?est:'conforme'}</span>`}
  const acc=g.visitantes?('Visitantes: sí'+(g.sudo?', con sudo':', sin sudo')):'Visitantes: no';
  return `<div class="nodo ${clase}"><h3>${e(n.nombre)}</h3><small>${e(n.rol)}</small><br>
  <span class="chip ${g.nivel==='alertar'?'alert':g.nivel==='registrar'?'reg':'corr'}">${g.nivel==='alertar'?'Vigilar y alertar':g.nivel==='registrar'?'Solo registrar':'Vigilar y corregir'}</span>
  <div class="acc">${acc} · admins: ${e(g.grupo_admin)}<br>${g.frecuencia?'Revisión cada '+g.frecuencia+' min + tiempo real':'Revisión en tiempo real'}</div>
  <div class="pie">${badge}${g.estado==='activo'||g.estado==='error'?`<button class="btn pel ${confirmar===n.nombre?'conf':''}" onclick="quitar('${e(n.nombre)}')">${confirmar===n.nombre?'¿Confirmar?':'Quitar'}</button>`:''}</div>
  ${u&&g.estado==='activo'?`<small>Último: ${e(u.usuario)} · ${hora(u.detectado)}</small>`:''}
  ${G.gitea_estado&&g.estado==='activo'?`<div class="git"><a href="${G.gitea_estado}/commits/branch/main/${e(n.nombre)}" target="_blank">Historial en Git</a>${g.ultimo_commit?` · <a href="${e(g.ultimo_commit)}" target="_blank">último commit ${hora(g.ultimo_commit_hora)}</a>`:''}
   · <button class="btn sec" onclick="referencia('${e(n.nombre)}')">Usar como referencia</button></div>`:''}</div>`}).join('')
  +(G.nodos.some(n=>!ges[n.nombre])?`<button class="agregar" onclick="abrir()">＋ Agregar servidor</button>`:'');
 document.getElementById('filas').innerHTML=D.map(i=>`<tr class="fila" onclick="location='/incidente/${i.id}'">
  <td>${hora(i.detectado)}</td><td><b>${e(i.host)}</b></td><td>${e(i.usuario)} <span class="mut">(${e(i.como)})</span></td>
  <td>${e(i.origen_ip)}</td><td><code>${JSON.parse(i.archivos||'[]').map(e).join('<br>')}</code></td>
  <td><code>${JSON.parse(i.comandos||'[]').map(e).join('<br>')}</code></td>
  <td><span class="badge ${i.estado.split(' ')[0]}">${e(i.estado)}</span></td>
  <td>${i.segundos?i.segundos+' s':''}</td>
  <td>${i.git&&i.git.startsWith('http')?`<a href="${e(i.git)}" target="_blank" onclick="event.stopPropagation()">commit en Git</a><br>`:''}${i.respuesta?'✔ '+e(i.respuesta):(['restablecido','alertado'].includes(i.estado)?'<span class="mut">⏳ pendiente de '+e(i.usuario)+'</span>':'')}</td></tr>`).join('')
  ||'<tr><td colspan="9" class="mut">Sin cambios no autorizados registrados.</td></tr>';
}
async function cargar(){
 try{[D,G]=await Promise.all([(await fetch('/api/incidentes')).json(),(await fetch('/api/gestion')).json()])}catch(x){return}
 if(!dlg.open&&!dlg2.open)pintar();
 document.getElementById('reloj').textContent=new Date().toLocaleTimeString('es-CL');
}
cargar();setInterval(cargar,2000);
</script></body></html>"""



def pagina_detalle(inc):
    lineas = "".join(f'<div class="{"del" if l.startswith("-") and not l.startswith("---") else "add" if l.startswith("+") and not l.startswith("+++") else ""}">{html.escape(l) or "&nbsp;"}</div>'
                     for l in (inc["diff"] or "").splitlines())
    cor = "".join(f"<li>{html.escape(c)}</li>" for c in json.loads(inc["correcciones"] or "[]"))
    filas = [("Servidor", inc["host"]), ("Usuario", f'{inc["usuario"]} (actuó como {inc["como"]})'), ("Origen", inc["origen_ip"]),
             ("Terminal", inc["terminal"]), ("Comandos recientes", "<br>".join(html.escape(c) for c in json.loads(inc["comandos"] or "[]"))),
             ("Archivos", "<br>".join(json.loads(inc["archivos"] or "[]"))), ("Detectado", fecha(inc["detectado"])),
             ("Estado", f'<span class="badge {inc["estado"].split(" ")[0]}">{inc["estado"]}</span>'),
             ("Restablecido", f'{fecha(inc["restablecido"])} {"(" + str(inc["segundos"]) + " s)" if inc["segundos"] else ""}'),
             ("Correo", inc["correo"] or ""), ("Respuesta", html.escape(inc["respuesta"] or "pendiente")),
             ("Registro en Git", (f'<a href="{html.escape(inc["git"])}" target="_blank">ver commit</a>' if (inc.get("git") or "").startswith("http")
                                  else html.escape(inc.get("git") or "—")))]
    tabla = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in filas)
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Incidente {inc['id']}</title>{ESTILO}</head><body><header><b>Incidente {inc['id']}</b><a href="/" style="color:#fff">← Volver al panel</a></header>
<main><table>{tabla}</table><p><a href="/responder/{inc['id']}" style="display:inline-block;background:#d92d20;color:#fff;padding:10px 16px;text-decoration:none;border-radius:4px">Registrar explicación del cambio</a></p><h3>Correcciones aplicadas</h3><ul>{cor or '<li class="mut">—</li>'}</ul>
<h3>Diferencias revertidas</h3><pre>{lineas or 'Sin diferencias registradas'}</pre></main></body></html>"""


def pagina_responder(inc, enviado=False):
    cuerpo = ("<p>Gracias, tu explicación quedó registrada en el panel.</p>" if enviado else
              f"""<p>El cambio que hiciste en <b>{inc['host']}</b> ({', '.join(json.loads(inc['archivos']))}) fue revertido.
<br>Cuéntanos por qué fue necesario:</p><form method="post"><textarea name="motivo" rows="5" style="width:100%;max-width:600px" required></textarea>
<br><br><button style="background:#0b1f3a;color:#fff;border:0;padding:10px 16px;border-radius:4px">Enviar</button></form>""")
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Explicar cambio</title>{ESTILO}</head><body><header><b>Explicar cambio · {inc['id']}</b></header><main>{cuerpo}</main></body></html>"""


def pagina_personas(msg=""):
    c = cfg()
    filas = "".join(f"<tr><td>{html.escape(u)}</td><td>{html.escape(d['nombre'])}</td><td>{html.escape(d['correo'])}</td>"
                    f"<td><form method='post' style='margin:0'><input type='hidden' name='accion' value='borrar'><input type='hidden' name='usuario' value='{html.escape(u)}'>"
                    f"<button>Quitar</button></form></td></tr>" for u, d in sorted(c["usuarios"].items()))
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Personas y correos</title>{ESTILO}</head><body><header><b>Personas y correos</b><a href="/" style="color:#fff">← Volver al panel</a></header><main>
{f'<p class="badge restablecido">{html.escape(msg)}</p>' if msg else ''}
<h3>Administrador (recibe copia de todos los avisos)</h3>
<form method="post"><input type="hidden" name="accion" value="admin"><input name="correo" type="email" value="{html.escape(c['correo_operaciones'])}" required style="width:280px"> <button>Guardar</button></form>
<h3>Personas que pueden generar cambios</h3><p class="mut">El usuario es el de Linux con que entra a los servidores. El aviso le llega a su correo.</p>
<table><tr><th>Usuario</th><th>Nombre</th><th>Correo</th><th></th></tr>{filas}</table>
<h3>Agregar o actualizar</h3><form method="post"><input type="hidden" name="accion" value="agregar">
<input name="usuario" placeholder="usuario Linux" required> <input name="nombre" placeholder="Nombre" required> <input name="correo" type="email" placeholder="correo" required> <button>Guardar</button></form>
</main></body></html>"""


def accion_visitante(f):
    import re, secrets
    c = cfg()
    if f.get("accion") == "crear" and f.get("nombre") and f.get("correo"):
        correo = f["correo"].strip().lower()[:120]
        base = re.sub(r"[^a-z0-9_]", "", correo.split("@")[0]) or "visita"
        if not base[0].isalpha():
            base = "v" + base
        base = base[:24]
        usuario, n = base, 1
        while usuario in RESERVADOS or (usuario in c["usuarios"] and c["usuarios"][usuario].get("correo") != correo):
            n += 1
            usuario = f"{base}{n}"
        destinos = hosts_visitantes()
        if not destinos:
            return pagina_visitante("Ningún servidor gestionado permite visitantes. Agrega uno desde el panel.")
        clave = llave_visitante(usuario)
        if not clave:
            return pagina_visitante("No se pudo generar la llave del visitante, revisa el log del panel")
        malos = ejecutar_visitante(usuario, "present", clave, destinos)
        if len(malos) < len(destinos):
            c = cfg()
            c["usuarios"][usuario] = {"nombre": f["nombre"][:60], "correo": correo, "visitante": True}
            guardar_cfg(c)
            return pagina_visitante(f"No se pudo crear en: {', '.join(malos)}" if malos else "", cred=(usuario, f["nombre"]))
        return pagina_visitante("No se pudo crear la cuenta, revisa el log del panel")
    if f.get("accion") == "quitar" and c["usuarios"].get(f.get("usuario"), {}).get("visitante"):
        threading.Thread(target=ejecutar_visitante, args=(f["usuario"], "absent"), daemon=True).start()
        borrar_llave_visitante(f["usuario"])
        c["usuarios"].pop(f["usuario"], None)
        guardar_cfg(c)
        return pagina_visitante(f"Cuenta {f['usuario']} eliminada de todos los servidores")
    return pagina_visitante()


RESERVADOS = {"root", "ansible", "cquezada", "admin", "bin", "daemon", "nobody", "sshd", "postgres", "nginx", "tomcat", "jboss"}


def hosts_visitantes():
    return [h for h, g in gestion_activa().items() if g.get("estado") == "activo" and g.get("visitantes")]


DIR_LLAVES = os.path.expanduser("~/.ssh/visitantes")


def llave_visitante(usuario):
    """Genera (o regenera) el par de llaves del visitante en la VM y devuelve la llave pública. La privada no sale de la VM."""
    os.makedirs(DIR_LLAVES, mode=0o700, exist_ok=True)
    priv = os.path.join(DIR_LLAVES, usuario)
    borrar_llave_visitante(usuario)
    r = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"visitante-{usuario}", "-f", priv],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("ssh-keygen", r.stderr[-300:], flush=True)
        return ""
    os.chmod(priv, 0o600)
    return open(priv + ".pub").read().strip()


def borrar_llave_visitante(usuario):
    for ext in ("", ".pub"):
        try:
            os.remove(os.path.join(DIR_LLAVES, usuario + ext))
        except OSError:
            pass


def ejecutar_visitante(usuario, estado, clave="", hosts=None):
    # clave = llave pública del visitante (acceso solo por llave, sin contraseña)
    if estado == "present":
        remoto = (f"if id {usuario} >/dev/null 2>&1; then sudo usermod -aG visitantes {usuario}; else sudo useradd -m -G visitantes {usuario}; fi; "
                  f"sudo install -d -m 700 -o {usuario} -g {usuario} /home/{usuario}/.ssh && "
                  f"echo '{clave}' | sudo tee /home/{usuario}/.ssh/authorized_keys >/dev/null && "
                  f"sudo chown {usuario}:{usuario} /home/{usuario}/.ssh/authorized_keys && sudo chmod 600 /home/{usuario}/.ssh/authorized_keys")
    else:
        remoto = f"sudo pkill -KILL -u {usuario}; sudo userdel -rf {usuario}; true"
    malos = []

    def uno(host):
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, remoto],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            malos.append(host)
            print(host, r.stderr[-500:], flush=True)

    hilos = [threading.Thread(target=uno, args=(h,)) for h in (hosts or [n["nombre"] for n in cfg()["nodos"]])]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    return malos


def pagina_visitante(msg="", cred=None):
    c = cfg()
    vis = sorted((u, d) for u, d in c["usuarios"].items() if d.get("visitante"))
    filas = "".join(f"<tr><td>{html.escape(u)}</td><td>{html.escape(d['nombre'])}</td><td>{html.escape(d['correo'])}</td>"
                    f"<td><form method='post' style='margin:0'><input type='hidden' name='accion' value='quitar'>"
                    f"<input type='hidden' name='usuario' value='{html.escape(u)}'><button>Quitar acceso</button></form></td></tr>" for u, d in vis)
    caja = ""
    if cred:
        u, nombre = cred
        ges = gestion_activa()
        nodos = "".join(f"<tr><td><code class='cmd' style='font-size:15px'>ssh -i ~/.ssh/visitantes/{u} {u}@{n['nombre']}</code></td>"
                        f"<td><button type='button' onclick='copiar(this)'>Copiar</button></td><td class='mut'>{html.escape(n['rol'])}"
                        f"{' · con sudo' if ges[n['nombre']].get('sudo') else ' · sin sudo'}</td></tr>"
                        for n in c["nodos"] if n["nombre"] in hosts_visitantes())
        caja = f"""<div style="border:2px solid var(--ok);border-radius:10px;padding:18px;margin:16px 0;background:var(--card)">
<h2 style="margin-top:0">Listo, {html.escape(nombre)}</h2>
<p style="font-size:20px">Usuario: <b>{u}</b> &nbsp; Acceso: <b>llave personal</b> (sin contraseña)</p>
<p class="mut">Desde la terminal de la máquina principal, copia y pega el comando del servidor que quieras:</p>
<table style="max-width:900px">{nodos}</table>
<script>function copiar(b){{const c=b.closest('tr').querySelector('.cmd');const t=c.textContent;
const sel=()=>{{const r=document.createRange();r.selectNodeContents(c);const s=getSelection();s.removeAllRanges();s.addRange(r)}};
if(navigator.clipboard)navigator.clipboard.writeText(t).then(()=>{{b.textContent='Copiado'}},sel);else sel()}}</script>
<p class="mut">Una vez dentro, haz un cambio “a mano”, por ejemplo:</p>
<pre>sudo sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
echo '{u} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/atajo
sudo vi /etc/ssh/sshd_config</pre>
<p>Ansible lo detecta, lo revierte en segundos y te llega un correo con el detalle. Mira el <a href="/">panel</a>.</p></div>"""
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Visitantes</title>{ESTILO}</head><body><header><b>Prueba tú mismo · Visitantes</b><a href="/" style="color:#fff">← Volver al panel</a></header><main>
{f'<p class="badge error">{html.escape(msg)}</p>' if msg else ''}{caja}
<h3>Crear acceso para un visitante</h3><p class="mut">La cuenta se crea al instante en los servidores gestionados que permiten visitantes ({html.escape(', '.join(hosts_visitantes()) or 'ninguno por ahora')}), con el usuario de su correo y una llave personal (sin contraseña).</p>
<form method="post"><input type="hidden" name="accion" value="crear"><input name="nombre" placeholder="Nombre" required style="width:220px">
<input name="correo" type="email" placeholder="correo" required style="width:260px"> <button>Crear acceso</button></form>
<h3>Visitantes activos</h3><table><tr><th>Usuario</th><th>Nombre</th><th>Correo</th><th></th></tr>{filas or '<tr><td colspan="4" class="mut">Sin visitantes.</td></tr>'}</table>
</main></body></html>"""


class H(BaseHTTPRequestHandler):
    def _out(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _json(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            return self._out(200, PAGINA)
        if p == "/api/gestion":
            c = cfg()
            return self._out(200, json.dumps({"nodos": c["nodos"], "gestion": c.get("gestion", {}),
                                              "correo": c.get("correo_operaciones", ""),
                                              "aap_ui": c.get("aap", {}).get("ui", ""),
                                              "gitea_estado": c.get("gitea_estado", ""),
                                              "similares": c.get("similares", ["nodo-so", "nodo-nginx", "nodo-postgres"])}),
                             "application/json")
        if p.startswith("/api/propagacion/"):
            pr = PROP.get(p.rsplit("/", 1)[-1])
            return self._out(200 if pr else 404, json.dumps(pr or {}), "application/json")
        if p == "/visitante":
            return self._out(200, pagina_visitante())
        if p == "/personas":
            return self._out(200, pagina_personas())
        if p == "/api/incidentes":
            with db() as c:
                rows = [dict(r) for r in c.execute("SELECT * FROM incidentes")]
            rows.sort(key=lambda r: datetime.fromisoformat(r["detectado"]), reverse=True)
            rows = rows[:100]
            return self._out(200, json.dumps(rows), "application/json")
        for pref, fn in (("/incidente/", pagina_detalle), ("/responder/", pagina_responder)):
            if p.startswith(pref):
                inc = obtener(p[len(pref):])
                return self._out(200, fn(inc)) if inc else self._out(404, "No encontrado")
        self._out(404, "No encontrado")

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/eventos":
            nuevo_evento(self._json())
            return self._out(202, "{}", "application/json")
        if p == "/api/estado":
            resultado(self._json())
            return self._out(200, "{}", "application/json")
        if p == "/api/estado-registro":
            resultado_registro(self._json())
            return self._out(200, "{}", "application/json")
        if p == "/api/propagacion-resultado":
            resultado_propagacion(self._json())
            return self._out(200, "{}", "application/json")
        if p == "/api/propagar":
            return self._out(200, json.dumps(pedir_propagar(self._json())), "application/json")
        if p == "/api/gestion/agregar":
            err = pedir_incorporar(self._json())
            return self._out(200, json.dumps({"error": err}), "application/json")
        if p == "/api/gestion/quitar":
            err = pedir_retirar(self._json().get("servidor", ""))
            return self._out(200, json.dumps({"error": err}), "application/json")
        if p == "/api/gestion/reiniciar" and self.client_address[0] == "127.0.0.1":
            reiniciar_gestion()
            return self._out(200, "{}", "application/json")
        if p == "/visitante":
            n = int(self.headers.get("Content-Length", 0))
            return self._out(200, accion_visitante({k: v[0].strip() for k, v in parse_qs(self.rfile.read(n).decode()).items()}))
        if p == "/personas":
            n = int(self.headers.get("Content-Length", 0))
            f = {k: v[0].strip() for k, v in parse_qs(self.rfile.read(n).decode()).items()}
            c = cfg()
            if f.get("accion") == "admin" and f.get("correo"):
                c["correo_operaciones"] = f["correo"]; msg = "Administrador actualizado"
            elif f.get("accion") == "agregar" and f.get("usuario"):
                c["usuarios"][f["usuario"]] = {"nombre": f.get("nombre", f["usuario"]), "correo": f.get("correo", "")}; msg = f"{f['usuario']} guardado"
            elif f.get("accion") == "borrar":
                c["usuarios"].pop(f.get("usuario", ""), None); msg = f"{f.get('usuario')} quitado"
            else:
                msg = ""
            guardar_cfg(c)
            return self._out(200, pagina_personas(msg))
        if p.startswith("/responder/"):
            inc = obtener(p[len("/responder/"):])
            if not inc:
                return self._out(404, "No encontrado")
            n = int(self.headers.get("Content-Length", 0))
            motivo = parse_qs(self.rfile.read(n).decode()).get("motivo", [""])[0][:1000]
            actualizar(inc["id"], respuesta=motivo, respondido=ahora())
            return self._out(200, pagina_responder(inc, True))
        self._out(404, "No encontrado")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    init_db()
    migrar_gestion()
    threading.Thread(target=revisiones_programadas, daemon=True).start()
    c = cfg()
    if c.get("puerto_https") and os.path.exists(os.path.join(BASE, "panel.crt")):
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(os.path.join(BASE, "panel.crt"), os.path.join(BASE, "panel.key"))
        https = ThreadingHTTPServer(("0.0.0.0", c["puerto_https"]), H)
        https.socket = ctx.wrap_socket(https.socket, server_side=True)
        threading.Thread(target=https.serve_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", c["puerto"]), H).serve_forever()
