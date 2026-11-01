"""Envío de correos de FaceOps por SMTP (FACEOPS_SMTP_HOST, _PUERTO, _USUARIO, _CLAVE, _DE). Sin servidor configurado,
el aviso queda registrado como pendiente de envío y la operación sigue."""
import os


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
