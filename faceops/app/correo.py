"""Envío de correos de FaceOps por SMTP (FACEOPS_SMTP_HOST, _PUERTO, _USUARIO, _CLAVE, _DE). Sin servidor configurado,
el aviso queda registrado como pendiente de envío y la operación sigue."""
import os


def zona_usuario(u):
    """Zona horaria para escribirle a un usuario: la de su perfil, si no la de su navegador en el último ingreso, si no ''
    (= UTC con la etiqueta «UTC»)."""
    from .hora_local import zona_valida
    u = u or {}
    return zona_valida(u.get("zona_horaria")) or zona_valida(u.get("zona_login"))


def zona_de(para="", usuario_id=None):
    """Zona del destinatario de un correo (uno o varios separados por coma) o de un usuario por su id. Si los
    destinatarios conocidos no comparten zona, o no son usuarios de FaceOps (correo de un grupo), queda UTC."""
    from .bd import q, q1
    try:
        if usuario_id:
            return zona_usuario(q1("SELECT zona_horaria, zona_login FROM usuarios WHERE id=?", usuario_id))
        cs = [c.strip().lower() for c in (para or "").replace(";", ",").split(",") if c.strip()]
        if not cs:
            return ""
        zs = {zona_usuario(u) for u in q(f"SELECT zona_horaria, zona_login FROM usuarios WHERE LOWER(correo) IN ({','.join('?' * len(cs))})", *cs)}
        zs.discard("")
        return zs.pop() if len(zs) == 1 else ""
    except Exception:  # noqa: BLE001  sin columnas aún o sin BD: UTC
        return ""


def hora(v, para="", usuario_id=None, modo="fecha-hora"):
    """Hora para el cuerpo de un correo, en la zona del destinatario («… (America/Santiago, UTC−3)») o en UTC."""
    from .hora_local import en_zona
    return en_zona(v, zona_de(para, usuario_id), modo)


def configurado():
    return bool(os.environ.get("FACEOPS_SMTP_HOST"))


def enviar(para, asunto, cuerpo, adjunto=None, nombre_adjunto="adjunto.pdf"):
    if not configurado():
        return "pendiente: FaceOps aún no tiene servidor de correo"
    if not para:
        return "sin correo registrado"
    import smtplib
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"] = os.environ.get("FACEOPS_SMTP_DE", "faceops@invoke.cl")
    m["To"], m["Subject"] = para, asunto
    m.set_content(cuerpo)
    if adjunto:
        m.add_attachment(adjunto, maintype="application", subtype="pdf", filename=nombre_adjunto)
    try:
        with smtplib.SMTP(os.environ["FACEOPS_SMTP_HOST"], int(os.environ.get("FACEOPS_SMTP_PUERTO", "587")), timeout=20) as s:
            s.starttls()
            if os.environ.get("FACEOPS_SMTP_USUARIO"):
                s.login(os.environ["FACEOPS_SMTP_USUARIO"], os.environ.get("FACEOPS_SMTP_CLAVE", ""))
            s.send_message(m)
        return f"enviado a {para}"
    except Exception as x:  # noqa: BLE001
        return f"no se pudo enviar ({type(x).__name__})"
