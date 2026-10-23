#!/usr/bin/python3
# Panel de cumplimiento: recibe eventos del vigía, lanza la autocorrección, registra y notifica.
import difflib, html, json, os, smtplib, sqlite3, subprocess, threading, time
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
    if cfg()["usuarios"].get(ev.get("usuario"), {}).get("visitante"):
        ev["origen_ip"] = "máquina principal (stand)"
    with lock_db, db() as c:
        c.execute("INSERT INTO incidentes (id, host, usuario, como, origen_ip, terminal, comandos, archivos, detectado, estado) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (ev["id"], ev["host"], ev["usuario"], ev.get("como", ""), ev.get("origen_ip", ""), ev.get("terminal", ""),
                   json.dumps(ev.get("comandos", [])), json.dumps(ev.get("archivos", [])), ahora(), "detectado"))
    if cfg().get("autocorregir", True):
        threading.Thread(target=corregir, args=(ev["id"], ev["host"]), daemon=True).start()


def corregir(iid, host):
    lock = locks_host.setdefault(host, threading.Lock())
    CFG = cfg()
    with lock:
        actualizar(iid, estado="corrigiendo")
        cmd = ["ansible-playbook", "-i", CFG["inventario"], CFG["playbook"], "-l", host,
               "-e", json.dumps({"incidente_id": iid, "panel_url": "http://127.0.0.1:%d" % CFG["puerto"]})]
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
    actualizar(inc["id"], estado="restablecido" if cor else "sin cambios", correcciones=json.dumps(cor),
               diff=unificar(data.get("cambios", [])), restablecido=fin.isoformat(timespec="seconds"),
               segundos=round((fin - det).total_seconds(), 1))
    if cor:
        threading.Thread(target=notificar, args=(inc["id"],), daemon=True).start()


def notificar(iid):
    CFG = cfg()
    inc = obtener(iid)
    persona = CFG["usuarios"].get(inc["usuario"], {"nombre": inc["usuario"], "correo": ""})
    admin = CFG["correo_operaciones"]
    solo_admin = CFG.get("aviso_solo_admin", True)
    url = CFG["url_publica"]
    diff_html = "".join(
        f'<div style="color:{"#b42318" if l.startswith("-") and not l.startswith("---") else "#067647" if l.startswith("+") and not l.startswith("+++") else "#555"};font-family:monospace;font-size:12px;white-space:pre">{html.escape(l)}</div>'
        for l in (inc["diff"] or "").splitlines())
    cor = "".join(f"<li>{html.escape(c)}</li>" for c in json.loads(inc["correcciones"] or "[]"))
    comandos = "<br>".join(html.escape(c) for c in json.loads(inc["comandos"] or "[]"))
    if solo_admin and not persona.get("visitante"):
        saludo = f"Hola {html.escape(CFG.get('nombre_admin', 'administrador'))},"
        intro = (f"<b>{html.escape(persona['nombre'])}</b> (<code>{inc['usuario']}</code>) hizo un cambio manual en "
                 f"<b>{inc['host']}</b> fuera del proceso estándar (Git + Ansible). El servidor ya fue "
                 f"<b style=\"color:#067647\">restablecido automáticamente en {inc['segundos']} s</b>. "
                 f"Revisa el detalle y decide si corresponde conversar con esta persona.")
        boton = "Registrar acción tomada"
        asunto = f"[Alerta] {persona['nombre']} hizo un cambio no autorizado en {inc['host']}"
        para, copia = admin, ""
    else:
        saludo = f"Hola {html.escape(persona['nombre'])},"
        intro = (("Gracias por probar la demo de Invoke en el Red Hat Summit. " if persona.get("visitante") else "") + f"Se detectó un cambio manual tuyo en <b>{inc['host']}</b> fuera del proceso estándar (Git + Ansible). "
                 f"El servidor ya fue <b style=\"color:#067647\">restablecido automáticamente en {inc['segundos']} s</b>.")
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
{fila("Comando", f"<code>{comandos}</code>")}
{fila("Archivos", ', '.join(json.loads(inc['archivos'])))}
{fila("Detectado", fecha(inc['detectado']))}</table>
<p><b>Correcciones aplicadas</b></p><ul style="font-size:13px">{cor}</ul>
<p><b>Diferencias revertidas</b></p><div style="background:#f9fafb;border:1px solid #e4e7ec;padding:10px">{diff_html}</div>
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
<title>Cumplimiento de configuración</title>""" + ESTILO + """</head><body>
<header><b>Invoke · Cumplimiento de configuración</b><span><a href="/visitante" style="color:#fff;margin-right:16px">Visitantes</a><a href="/personas" style="color:#fff;margin-right:16px">Personas y correos</a><span id="reloj" style="color:#cbd5e1"></span></span></header>
<main><div class="nodos" id="nodos"></div>
<table><thead><tr><th>Detectado</th><th>Servidor</th><th>Usuario</th><th>Origen</th><th>Archivos</th><th>Comando</th><th>Estado</th><th>Tiempo</th><th>Seguimiento</th></tr></thead>
<tbody id="filas"></tbody></table></main>
<script>
const NODOS=__NODOS__;
const e=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const hora=s=>s?new Date(s).toLocaleTimeString('es-CL'):'';
async function cargar(){
 let d=[];try{d=await (await fetch('/api/incidentes')).json()}catch(x){return}
 document.getElementById('nodos').innerHTML=NODOS.map(n=>{
  const u=d.find(i=>i.host===n.nombre);const est=u?u.estado:'restablecido';
  const activo=u&&['detectado','corrigiendo','error'].includes(est);
  return `<div class="nodo ${activo?est:''}"><h3>${e(n.nombre)}</h3><small>${e(n.rol)}</small><br><br>
  <span class="badge ${activo?est:'restablecido'}">${activo?est:'conforme'}</span>
  ${u?`<br><small>Último: ${e(u.usuario)} · ${hora(u.detectado)}</small>`:''}</div>`}).join('');
 document.getElementById('filas').innerHTML=d.map(i=>`<tr class="fila" onclick="location='/incidente/${i.id}'">
  <td>${hora(i.detectado)}</td><td><b>${e(i.host)}</b></td><td>${e(i.usuario)} <span class="mut">(${e(i.como)})</span></td>
  <td>${e(i.origen_ip)}</td><td><code>${JSON.parse(i.archivos||'[]').map(e).join('<br>')}</code></td>
  <td><code>${JSON.parse(i.comandos||'[]').map(e).join('<br>')}</code></td>
  <td><span class="badge ${i.estado.split(' ')[0]}">${e(i.estado)}</span></td>
  <td>${i.segundos?i.segundos+' s':''}</td>
  <td>${i.respuesta?'✔ '+e(i.respuesta):(i.estado==='restablecido'?'<span class="mut">⏳ pendiente de '+e(i.usuario)+'</span>':'')}</td></tr>`).join('')
  ||'<tr><td colspan="9" class="mut">Sin cambios no autorizados registrados.</td></tr>';
 document.getElementById('reloj').textContent=new Date().toLocaleTimeString('es-CL');
}
cargar();setInterval(cargar,2000);
</script></body></html>"""


def pagina_detalle(inc):
    lineas = "".join(f'<div class="{"del" if l.startswith("-") and not l.startswith("---") else "add" if l.startswith("+") and not l.startswith("+++") else ""}">{html.escape(l) or "&nbsp;"}</div>'
                     for l in (inc["diff"] or "").splitlines())
    cor = "".join(f"<li>{html.escape(c)}</li>" for c in json.loads(inc["correcciones"] or "[]"))
    filas = [("Servidor", inc["host"]), ("Usuario", f'{inc["usuario"]} (actuó como {inc["como"]})'), ("Origen", inc["origen_ip"]),
             ("Terminal", inc["terminal"]), ("Comandos", "<br>".join(html.escape(c) for c in json.loads(inc["comandos"] or "[]"))),
             ("Archivos", "<br>".join(json.loads(inc["archivos"] or "[]"))), ("Detectado", fecha(inc["detectado"])),
             ("Estado", f'<span class="badge {inc["estado"].split(" ")[0]}">{inc["estado"]}</span>'),
             ("Restablecido", f'{fecha(inc["restablecido"])} {"(" + str(inc["segundos"]) + " s)" if inc["segundos"] else ""}'),
             ("Correo", inc["correo"] or ""), ("Respuesta", html.escape(inc["respuesta"] or "pendiente"))]
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
        clave = secrets.token_hex(4)
        malos = ejecutar_visitante(usuario, "present", clave)
        if len(malos) < len(c["nodos"]):
            c = cfg()
            c["usuarios"][usuario] = {"nombre": f["nombre"][:60], "correo": correo, "visitante": True}
            guardar_cfg(c)
            return pagina_visitante(f"No se pudo crear en: {', '.join(malos)}" if malos else "", cred=(usuario, clave, f["nombre"]))
        return pagina_visitante("No se pudo crear la cuenta, revisa el log del panel")
    if f.get("accion") == "quitar" and c["usuarios"].get(f.get("usuario"), {}).get("visitante"):
        threading.Thread(target=ejecutar_visitante, args=(f["usuario"], "absent"), daemon=True).start()
        c["usuarios"].pop(f["usuario"], None)
        guardar_cfg(c)
        return pagina_visitante(f"Cuenta {f['usuario']} eliminada de todos los servidores")
    return pagina_visitante()


RESERVADOS = {"root", "ansible", "cquezada", "admin", "bin", "daemon", "nobody", "sshd", "postgres", "nginx", "tomcat", "jboss"}


def ejecutar_visitante(usuario, estado, clave=""):
    if estado == "present":
        remoto = f"if id {usuario} >/dev/null 2>&1; then sudo usermod -aG visitantes {usuario}; else sudo useradd -m -G visitantes {usuario}; fi; echo '{usuario}:{clave}' | sudo chpasswd"
    else:
        remoto = f"sudo pkill -KILL -u {usuario}; sudo userdel -rf {usuario}; true"
    malos = []

    def uno(host):
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, remoto],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            malos.append(host)
            print(host, r.stderr[-500:], flush=True)

    hilos = [threading.Thread(target=uno, args=(n["nombre"],)) for n in cfg()["nodos"]]
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
        u, k, nombre = cred
        nodos = "".join(f"<tr><td><code style='font-size:16px'>ssh {u}@{n['nombre']}</code></td><td class='mut'>{html.escape(n['rol'])}</td></tr>" for n in c["nodos"])
        caja = f"""<div style="border:2px solid var(--ok);border-radius:10px;padding:18px;margin:16px 0;background:var(--card)">
<h2 style="margin-top:0">Listo, {html.escape(nombre)}</h2>
<p style="font-size:20px">Usuario: <b>{u}</b> &nbsp; Clave: <b>{html.escape(k)}</b></p>
<p class="mut">Desde la terminal de la máquina principal, entra al servidor que quieras:</p>
<table style="max-width:700px">{nodos}</table>
<p class="mut">Una vez dentro, haz un cambio “a mano”, por ejemplo:</p>
<pre>sudo sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
echo '{u} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/atajo
sudo vi /etc/ssh/sshd_config</pre>
<p>Ansible lo detecta, lo revierte en segundos y te llega un correo con el detalle. Mira el <a href="/">panel</a>.</p></div>"""
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Visitantes</title>{ESTILO}</head><body><header><b>Prueba tú mismo · Visitantes</b><a href="/" style="color:#fff">← Volver al panel</a></header><main>
{f'<p class="badge error">{html.escape(msg)}</p>' if msg else ''}{caja}
<h3>Crear acceso para un visitante</h3><p class="mut">La cuenta se crea al instante en todos los servidores, con el usuario de su correo y una clave aleatoria.</p>
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
            return self._out(200, PAGINA.replace("__NODOS__", json.dumps(cfg()["nodos"])))
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
    ThreadingHTTPServer(("0.0.0.0", cfg()["puerto"]), H).serve_forever()
