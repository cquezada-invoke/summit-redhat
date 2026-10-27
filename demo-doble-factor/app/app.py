#!/usr/bin/python3
# Demo doble factor · "Mi App": login simple contra Keycloak (OIDC + PKCE) y teléfono/bandeja simulados
# donde llegan los códigos por SMS y por correo. Solo librería estándar.
#   /mi-app/            inicio (botón Ingresar / saludo con los datos del usuario)
#   /mi-app/telefono    teléfono y bandeja simulados (se actualizan solos)
#   /interno/mensaje    POST desde Keycloak (solo desde la propia VM; Nginx no lo publica)
import base64, hashlib, html, json, os, secrets, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("APP_HOST", "192.168.122.1")
PUERTO = int(os.environ.get("APP_PUERTO", "8096"))
KC_PUBLICO = os.environ.get("KC_PUBLICO", "http://34.176.42.56:8090/doble-factor")
KC_INTERNO = os.environ.get("KC_INTERNO", "http://192.168.122.1:8095/doble-factor")
REALM = os.environ.get("REALM", "invoke-2fa")
CLIENTE = os.environ.get("CLIENTE", "mi-app")
APP = os.environ.get("APP_PUBLICA", "http://34.176.42.56:8090/mi-app")
INTERNOS = {"127.0.0.1", "192.168.122.1"}

MENSAJES, SESIONES, PENDIENTES = [], {}, {}
CANDADO = threading.Lock()

CSS = """
:root{--fondo:#f4f6fa;--tarjeta:#fff;--texto:#1d2433;--suave:#667085;--rojo:#ee0000;--borde:#e3e7ef;--verde:#12805c}
*{box-sizing:border-box}body{margin:0;font-family:"Red Hat Text",Arial,sans-serif;background:var(--fondo);color:var(--texto)}
header{background:#151515;color:#fff;padding:14px 24px;display:flex;align-items:center;gap:12px}
header b{color:var(--rojo)}main{max-width:980px;margin:32px auto;padding:0 16px}
.tarjeta{background:var(--tarjeta);border:1px solid var(--borde);border-radius:12px;padding:28px}
.boton{display:inline-block;background:var(--rojo);color:#fff;text-decoration:none;padding:12px 22px;border-radius:8px;font-weight:600}
.boton.gris{background:#4a5160}.suave{color:var(--suave)}table{border-collapse:collapse;margin:16px 0}
td{padding:6px 14px 6px 0;border-bottom:1px solid var(--borde)}td:first-child{color:var(--suave)}
.ok{color:var(--verde);font-weight:600}
"""


def pagina(titulo, cuerpo, extra=""):
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{titulo}</title>
<style>{CSS}{extra}</style></head><body><header><b>●</b> Invoke · Mi App <span class="suave" style="margin-left:auto;font-size:13px">Demo doble factor con Keycloak</span></header>
<main>{cuerpo}</main></body></html>"""


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def jwt_datos(token):
    p = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


class Manejador(BaseHTTPRequestHandler):
    server_version = "mi-app"

    def log_message(self, fmt, *args):
        pass

    def responder(self, codigo, cuerpo, tipo="text/html; charset=utf-8", cabeceras=None):
        datos = cuerpo.encode() if isinstance(cuerpo, str) else cuerpo
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (cabeceras or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(datos)

    def redirigir(self, url, cabeceras=None):
        c = {"Location": url}
        c.update(cabeceras or {})
        self.responder(302, "", cabeceras=c)

    def sesion(self):
        for parte in self.headers.get("Cookie", "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == "miapp":
                return v, SESIONES.get(v)
        return None, None

    # ---------- GET ----------
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        ruta = u.path.rstrip("/") or "/"
        if ruta in ("/mi-app", "/"):
            return self.inicio()
        if ruta == "/mi-app/ingresar":
            return self.ingresar()
        if ruta == "/mi-app/callback":
            return self.callback(q)
        if ruta == "/mi-app/salir":
            return self.salir()
        if ruta == "/mi-app/telefono":
            return self.responder(200, TELEFONO)
        if ruta == "/mi-app/telefono/mensajes":
            with CANDADO:
                return self.responder(200, json.dumps(MENSAJES[-30:]), "application/json")
        if ruta == "/mi-app/salud":
            return self.responder(200, "ok", "text/plain")
        self.responder(404, pagina("No encontrado", "<div class='tarjeta'>No encontrado</div>"))

    def inicio(self):
        _, s = self.sesion()
        if not s:
            cuerpo = f"""<div class="tarjeta"><h1 style="margin-top:0">Bienvenido a Mi App</h1>
<p class="suave">Para entrar te pediremos tu usuario y contraseña, y luego un código que te llegará por SMS o por correo.</p>
<p><a class="boton" href="{APP}/ingresar">Ingresar</a></p>
<p class="suave" style="font-size:13px">¿Dónde llegan los códigos en la demo? <a href="{APP}/telefono" target="_blank">Teléfono y bandeja simulados</a></p></div>"""
            return self.responder(200, pagina("Mi App", cuerpo))
        d = s["datos"]
        metodo = {"sms": "código por SMS", "correo": "código por correo"}.get(s.get("metodo"), "—")
        filas = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>" for k, v in [
            ("Nombre", d.get("name", d.get("preferred_username"))), ("Usuario", d.get("preferred_username")),
            ("Correo", d.get("email", "—")), ("Segundo factor", metodo),
            ("Sesión iniciada", time.strftime("%H:%M:%S", time.localtime(s["desde"])))])
        cuerpo = f"""<div class="tarjeta"><h1 style="margin-top:0">Hola, {html.escape(d.get('given_name') or d.get('preferred_username', ''))} 👋</h1>
<p class="ok">✔ Entraste con usuario, contraseña y código de un solo uso.</p><table>{filas}</table>
<p><a class="boton gris" href="{APP}/salir">Cerrar sesión</a></p></div>"""
        self.responder(200, pagina("Mi App", cuerpo))

    def ingresar(self):
        estado, verificador = secrets.token_urlsafe(16), secrets.token_urlsafe(48)
        PENDIENTES[estado] = (verificador, time.time())
        reto = b64url(hashlib.sha256(verificador.encode()).digest())
        params = urllib.parse.urlencode({"client_id": CLIENTE, "response_type": "code", "scope": "openid profile email",
                                         "redirect_uri": f"{APP}/callback", "state": estado,
                                         "code_challenge": reto, "code_challenge_method": "S256", "ui_locales": "es"})
        self.redirigir(f"{KC_PUBLICO}/realms/{REALM}/protocol/openid-connect/auth?{params}")

    def callback(self, q):
        estado = (q.get("state") or [""])[0]
        if "error" in q or estado not in PENDIENTES:
            return self.responder(400, pagina("Error", f"<div class='tarjeta'>No se pudo ingresar: {html.escape(str(q.get('error_description', q.get('error', ['estado inválido']))[0]))}. <a href='{APP}/'>Volver</a></div>"))
        verificador, _ = PENDIENTES.pop(estado)
        datos = urllib.parse.urlencode({"grant_type": "authorization_code", "code": q["code"][0], "client_id": CLIENTE,
                                        "redirect_uri": f"{APP}/callback", "code_verifier": verificador}).encode()
        try:
            with urllib.request.urlopen(f"{KC_INTERNO}/realms/{REALM}/protocol/openid-connect/token", datos, timeout=10) as r:
                tok = json.load(r)
        except Exception as e:
            return self.responder(502, pagina("Error", f"<div class='tarjeta'>Keycloak no respondió: {html.escape(str(e))}</div>"))
        id_tok = jwt_datos(tok["id_token"])
        sid = secrets.token_urlsafe(24)
        SESIONES[sid] = {"datos": id_tok, "id_token": tok["id_token"], "desde": time.time(),
                         "metodo": ultimo_canal()}
        self.redirigir(f"{APP}/", {"Set-Cookie": f"miapp={sid}; Path=/mi-app; HttpOnly; SameSite=Lax"})

    def salir(self):
        sid, s = self.sesion()
        SESIONES.pop(sid, None)
        if not s:
            return self.redirigir(f"{APP}/")
        params = urllib.parse.urlencode({"id_token_hint": s["id_token"], "post_logout_redirect_uri": f"{APP}/"})
        self.redirigir(f"{KC_PUBLICO}/realms/{REALM}/protocol/openid-connect/logout?{params}",
                       {"Set-Cookie": "miapp=; Path=/mi-app; Max-Age=0"})

    # ---------- POST ----------
    def do_POST(self):
        if self.path != "/interno/mensaje" or self.client_address[0] not in INTERNOS:
            return self.responder(403, "no", "text/plain")
        largo = int(self.headers.get("Content-Length", "0") or 0)
        try:
            m = json.loads(self.rfile.read(min(largo, 20000)))
        except ValueError:
            return self.responder(400, "json", "text/plain")
        m = {k: (str(m.get(k))[:2000] if m.get(k) is not None else None) for k in ("canal", "destino", "asunto", "texto", "nota")}
        m["hora"] = time.strftime("%H:%M:%S")
        m["id"] = secrets.token_hex(4)
        with CANDADO:
            MENSAJES.append(m)
            del MENSAJES[:-100]
        self.responder(200, "ok", "text/plain")


def ultimo_canal():
    with CANDADO:
        return MENSAJES[-1]["canal"] if MENSAJES else None

TELEFONO = pagina("Teléfono y bandeja · demo", """
<div class="grid">
  <div><h2>📱 Teléfono (SMS)</h2><div class="telefono"><div class="notch"></div><div class="pantalla" id="sms"><p class="vacio">Sin mensajes</p></div></div></div>
  <div><h2>✉️ Bandeja de correo</h2><div class="tarjeta" id="correo" style="padding:0"><p class="vacio" style="padding:20px">Sin correos</p></div></div>
</div>
<p class="suave" style="font-size:13px">Simulación para la demo: los códigos que envía Keycloak aparecen aquí al instante. El correo además se envía de verdad si el SMTP del realm está configurado.</p>
<script>
const e = s => (s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const resaltar = s => e(s).replace(/\\b(\\d{4,8})\\b/, '<b class="codigo">$1</b>');
let visto = "";
async function cargar(){
  try{
    const r = await fetch("telefono/mensajes", {cache:"no-store"}); const m = await r.json();
    const firma = m.map(x=>x.id).join(); if (firma === visto) return; visto = firma;
    const sms = m.filter(x=>x.canal==="sms").reverse(), cor = m.filter(x=>x.canal==="correo").reverse();
    document.getElementById("sms").innerHTML = sms.length ? sms.map(x=>`<div class="burbuja"><div class="de">Invoke · ${e(x.destino)} · ${e(x.hora)}</div>${resaltar(x.texto)}</div>`).join("") : '<p class="vacio">Sin mensajes</p>';
    document.getElementById("correo").innerHTML = cor.length ? cor.map(x=>`<div class="mail"><div class="cab"><b>${e(x.asunto)}</b><span>${e(x.hora)}</span></div><div class="de">Para: ${e(x.destino)} · <i>${e(x.nota)}</i></div><pre>${resaltar(x.texto)}</pre></div>`).join("") : '<p class="vacio" style="padding:20px">Sin correos</p>';
  }catch(err){}
}
cargar(); setInterval(cargar, 1500);
</script>""", """
.grid{display:grid;grid-template-columns:320px 1fr;gap:28px}@media(max-width:760px){.grid{grid-template-columns:1fr}}
h2{font-size:17px}.telefono{width:300px;height:560px;background:#111;border-radius:42px;padding:14px;position:relative}
.notch{width:110px;height:22px;background:#111;border-radius:0 0 14px 14px;position:absolute;left:50%;transform:translateX(-50%);top:14px;z-index:2}
.pantalla{background:#eef1f6;border-radius:30px;height:100%;overflow:auto;padding:42px 12px 12px}
.burbuja{background:#fff;border-radius:16px;padding:10px 12px;margin-bottom:10px;font-size:14px;box-shadow:0 1px 2px #0001}
.de{font-size:11px;color:var(--suave);margin-bottom:4px}.codigo{font-size:20px;letter-spacing:3px;color:var(--rojo)}
.mail{border-bottom:1px solid var(--borde);padding:14px 18px}.mail .cab{display:flex;justify-content:space-between;gap:10px}
.mail pre{white-space:pre-wrap;font-family:inherit;margin:8px 0 0}.vacio{color:var(--suave);text-align:center}
""")

if __name__ == "__main__":
    print(f"Mi App en http://{HOST}:{PUERTO}/mi-app/  (Keycloak {KC_PUBLICO}, realm {REALM})", flush=True)
    ThreadingHTTPServer((HOST, PUERTO), Manejador).serve_forever()
