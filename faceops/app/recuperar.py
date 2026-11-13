"""Recuperar clave desde el login: enlace de un solo uso (1 hora) al correo del usuario.
Solo usuarios locales activos con correo; los de AD, LDAP o midPoint cambian su clave en su fuente. Si el usuario existe, la respuesta dice
a qué correo se envió (enmascarado: c******a@inv***.cl) o que el envío falló; si no existe, un mensaje genérico sin correo.
Si el usuario tiene doble factor, lo conserva: el enlace solo cambia la clave.
El correo lo envía FaceAdmin (FaceOps sube el pedido; FaceAdmin nunca entra a FaceOps); sin FaceAdmin, el SMTP propio de la instancia."""
import os
import time

import requests
from flask import flash, redirect, render_template, request, url_for

from .bd import ahora, auditar, q1
from .vistas import bp

HORAS = 1
_ult = {}   # ip -> [instantes] (máx. 5 pedidos cada 15 minutos por IP)


def _limite(ip):
    ahora_ = time.time()
    v = [t for t in _ult.get(ip, []) if ahora_ - t < 900]
    _ult[ip] = v + [ahora_]
    return len(v) >= 5


def enmascarar(correo):
    """c******a@inv***.cl: primera y última letra del usuario; del dominio, las 3 primeras letras y el sufijo final (.cl, .com)."""
    usr, _, dom = (correo or "").strip().rpartition("@")
    if not usr or not dom:
        return "***"
    usr = usr[0] + "*" * max(len(usr) - 2, 1) + (usr[-1] if len(usr) > 1 else "")
    nom, punto, suf = dom.rpartition(".")
    if not punto:
        nom, suf = dom, ""
    return f"{usr}@{nom[:3] if len(nom) > 3 else nom[:1]}***{punto}{suf}"


def enviado(est):
    """FaceAdmin responde «enviado»; el SMTP propio, «enviado a …». Cualquier otra cosa es un envío fallido."""
    return str(est or "").startswith("enviado")


def _enviar(u, ruta):
    url, tok = os.environ.get("FACEOPS_ADMIN_URL", "").rstrip("/"), os.environ.get("FACEOPS_ADMIN_TOKEN", "")
    if url and tok:
        try:
            r = requests.post(f"{url}/api/instancia/recuperar", json={"usuario": u["usuario"], "nombre": u["nombre"], "correo": u["correo"], "ruta": ruta},
                              headers={"X-FaceAdmin-Token": tok}, timeout=15, verify=False)
            return (r.json() or {}).get("estado", f"HTTP {r.status_code}")
        except (requests.RequestException, ValueError) as e:
            return f"FaceAdmin no respondió ({type(e).__name__})"
    pub = os.environ.get("FACEOPS_URL_PUBLICA", "").rstrip("/")
    if not pub:
        return "sin FaceAdmin ni URL pública configurada"
    from .correo import enviar
    return enviar(u["correo"], "Recuperar clave de FaceOps",
                  f"Hola {u['nombre']},\n\nPediste recuperar tu clave de FaceOps (usuario {u['usuario']}).\nDefine una nueva aquí (un solo uso, {HORAS} hora):\n"
                  f"{pub}{ruta}\n\nSi no lo pediste, ignora este correo: tu clave actual sigue igual.\n\nFaceOps · Invoke")


@bp.route("/ingresar/recuperar", methods=["GET", "POST"])
def recuperar():
    from .doble_factor import ip_cliente
    if request.method == "POST":
        ip = ip_cliente(request)
        dato = request.form.get("dato", "").strip().lower()[:120]
        generico = (f"Si el usuario existe y tiene correo registrado, le llegará un enlace para definir una clave nueva (válido {HORAS} hora). "
                    "Si entras con tu cuenta de la empresa (AD, LDAP o midPoint), cambia la clave allí.")
        if not dato:
            return redirect(url_for("consola.recuperar"))
        if _limite(ip):
            flash("Demasiados pedidos de recuperación desde tu conexión: espera 15 minutos e inténtalo de nuevo.", "error")
            return redirect(url_for("consola.recuperar"))
        u = q1("SELECT * FROM usuarios WHERE (LOWER(usuario)=? OR LOWER(correo)=?) AND activo=1 AND COALESCE(origen,'local')='local' "
               "AND COALESCE(correo,'')<>''", dato, dato)
        if u and u.get("expira") and u["expira"] < ahora():
            u = None
        if not u:
            auditar(dato[:60], None, "recuperación de clave sin usuario local", "sesión", {"ip": ip})
            flash(generico, "ok")
            return redirect(url_for("consola.ingresar"))
        from .activacion import nuevo
        est = _enviar(u, "/definir-clave/" + nuevo(u["usuario"], HORAS))
        auditar(u["usuario"], None, "recuperación de clave pedida", "sesión", {"ip": ip, "envio": str(est)[:120]})
        destino = enmascarar(u["correo"])
        if enviado(est):
            flash(f"Te enviamos el enlace para definir una clave nueva a {destino} (válido {HORAS} hora). "
                  "Si no lo ves en unos minutos, revisa spam.", "ok")
            return redirect(url_for("consola.ingresar"))
        flash(f"No se pudo enviar el correo de recuperación a {destino}: el servicio de correo de FaceOps no respondió o no está configurado. "
              "Inténtalo más tarde o pide a un administrador de FaceOps que te genere un enlace nuevo.", "error")
        return redirect(url_for("consola.recuperar"))
    return render_template("recuperar.html")
