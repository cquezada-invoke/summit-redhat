"""Informes en PDF del portal: los playbooks de análisis envían sus hallazgos al portal (POST /api/informe, solo desde la red
interna de AAP), el portal arma el PDF con la misma lógica de FaceOps (semáforo por severidad, qué se encontró, qué
significa y qué hacer, y un resumen ejecutivo escrito por Claude si hay clave de Anthropic) y lo envía por correo.
SMTP: FACEOPS_SMTP_HOST, _PUERTO, _USUARIO, _CLAVE, _DE (las mismas variables de FaceOps). IA: ANTHROPIC_API_KEY (opcional)."""
import json
import os
import smtplib
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from io import BytesIO

COLOR = {"critico": "#c9190b", "alto": "#ec7a08", "medio": "#c58c00", "bajo": "#2b9af3", "ok": "#3e8635"}
ETQ = {"critico": "Crítico", "alto": "Alto", "medio": "Medio", "bajo": "Bajo", "ok": "Sin observaciones"}
ORDEN = ["critico", "alto", "medio", "bajo", "ok"]
DIR = os.environ.get("PORTAL_INFORMES", "/tmp/informes")


def _resumen_ia(d):
    """Resumen ejecutivo y prioridades escritos por Claude a partir de los hallazgos (sin datos sensibles: solo hallazgos)."""
    clave = os.environ.get("ANTHROPIC_API_KEY", "")
    if not clave:
        return ""
    hallazgos = [{"seccion": s["titulo"], **{k: h.get(k) for k in ("sev", "titulo", "detalle")}} for s in d.get("secciones", []) for h in s.get("hallazgos", [])]
    pedido = ("Eres un arquitecto de Red Hat que escribe para la gerencia de TI de un cliente. Con estos hallazgos de un análisis automático "
              f"«{d.get('titulo')}», escribe en español: 1) un resumen ejecutivo de 4 a 6 líneas (estado general y riesgo de negocio), "
              "2) las 3 a 5 acciones prioritarias en orden, cada una con su porqué en una línea. Sin inventar datos que no estén en los hallazgos. "
              "Texto plano, sin markdown.\n\n" + json.dumps(hallazgos[:120], ensure_ascii=False))
    try:
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", method="POST", data=json.dumps({
            "model": os.environ.get("PORTAL_MODELO", "claude-sonnet-5-5"), "max_tokens": 900,
            "messages": [{"role": "user", "content": pedido}]}).encode(),
            headers={"x-api-key": clave, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return "".join(b.get("text", "") for b in json.load(r).get("content", []))
    except Exception:  # noqa: BLE001 - el informe sale igual, sin el resumen
        return ""


def pdf(d):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    st = getSampleStyleSheet()
    s_t = ParagraphStyle("t", parent=st["Title"], fontName="Helvetica-Bold", fontSize=18, alignment=TA_LEFT, spaceAfter=2)
    s_h = ParagraphStyle("h", parent=st["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, spaceBefore=10, spaceAfter=4)
    s_n = ParagraphStyle("n", parent=st["BodyText"], fontName="Helvetica", fontSize=9, leading=12)
    s_s = ParagraphStyle("s", parent=s_n, fontSize=8, textColor=colors.HexColor("#6a6e73"))
    esc = lambda x: str(x if x is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    todos = [h for s in d.get("secciones", []) for h in s.get("hallazgos", [])]
    cuenta = {k: sum(1 for h in todos if h.get("sev") == k) for k in ORDEN}
    out = [Paragraph(f"{esc(d.get('cliente', 'Claro Puerto Rico'))} · Portal de automatización (Ansible Automation Platform)", s_s),
           Paragraph(esc(d.get("titulo", "Informe")), s_t),
           Paragraph(f"{esc(d.get('alcance', ''))} · {datetime.now().strftime('%Y-%m-%d %H:%M')} · ejecución AAP #{esc(d.get('job', '-'))}", s_s),
           Spacer(1, 6)]
    t = Table([[Paragraph(f"<font color='{COLOR[k]}'>■</font> <b>{ETQ[k]}</b>", s_n) for k in ORDEN],
               [Paragraph(f"<b>{cuenta[k]}</b>", s_t) for k in ORDEN]], colWidths=[36 * mm] * 5)
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d2d2d2")), ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e5e5"))]))
    out.append(t)
    ia = _resumen_ia(d)
    if ia:
        out.append(Paragraph("Resumen ejecutivo", s_h))
        for parr in [p for p in ia.split("\n") if p.strip()]:
            out.append(Paragraph(esc(parr), s_n))
        out.append(Paragraph("Escrito por Claude (Anthropic) a partir de los hallazgos de este análisis; revise antes de decidir.", s_s))
    for s in d.get("secciones", []):
        out.append(Paragraph(esc(s["titulo"]), s_h))
        if s.get("que"):
            out.append(Paragraph(esc(s["que"]), s_s))
        hs = sorted(s.get("hallazgos", []), key=lambda h: ORDEN.index(h.get("sev", "bajo")) if h.get("sev") in ORDEN else 9)
        if not hs:
            out.append(Paragraph(f"<font color='{COLOR['ok']}'>●</font> Sin observaciones.", s_n))
        for h in hs[:60]:
            c = COLOR.get(h.get("sev"), "#151515")
            filas = [[Paragraph(f"<font color='{c}'>■</font> <b>{esc(h.get('titulo'))}</b> · <font color='{c}'>{ETQ.get(h.get('sev'), '')}</font>"
                                + (f" · <i>{esc(h.get('objeto'))}</i>" if h.get("objeto") else ""), s_n)]]
            if h.get("detalle"):
                filas.append([Paragraph(esc(h["detalle"]), s_s)])
            if h.get("recomendacion"):
                filas.append([Paragraph(f"<i>Qué hacer:</i> {esc(h['recomendacion'])}", s_n)])
            tb = Table(filas, colWidths=[180 * mm])
            tb.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d2d2d2")), ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(c)),
                                    ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 7)]))
            out.append(KeepTogether([tb, Spacer(1, 4)]))
    out += [Spacer(1, 8), Paragraph("Generado automáticamente por Ansible Automation Platform (análisis de solo lectura: no se cambió nada en los sistemas). "
                                    "Cada ejecución queda auditada en AAP con quién la pidió y quién la aprobó.", s_s)]
    buf = BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                      title=d.get("titulo", "Informe"), author="Portal de automatización · Invoke").build(out)
    return buf.getvalue(), cuenta


def enviar(para, asunto, cuerpo, adjunto, nombre):
    host = os.environ.get("FACEOPS_SMTP_HOST", "")
    if not host:
        return "sin servidor de correo configurado"
    m = EmailMessage()
    m["From"] = os.environ.get("FACEOPS_SMTP_DE", "automatizacion@invoke.cl")
    m["To"], m["Subject"] = para, asunto
    m.set_content(cuerpo)
    m.add_attachment(adjunto, maintype="application", subtype="pdf", filename=nombre)
    try:
        with smtplib.SMTP(host, int(os.environ.get("FACEOPS_SMTP_PUERTO", "587")), timeout=20) as s:
            s.starttls()
            if os.environ.get("FACEOPS_SMTP_USUARIO"):
                s.login(os.environ["FACEOPS_SMTP_USUARIO"], os.environ.get("FACEOPS_SMTP_CLAVE", ""))
            s.send_message(m)
        return f"enviado a {para}"
    except Exception as x:  # noqa: BLE001
        return f"no se pudo enviar ({type(x).__name__})"


def procesar(d):
    """Arma el PDF, lo guarda (descargable desde el portal) y lo envía al correo indicado."""
    contenido, cuenta = pdf(d)
    os.makedirs(DIR, exist_ok=True)
    nombre = f"{d.get('archivo', 'informe')}-{datetime.now().strftime('%Y%m%d-%H%M')}.pdf"
    with open(os.path.join(DIR, nombre), "wb") as f:
        f.write(contenido)
    para = (d.get("correo") or "").strip()
    resumen = " · ".join(f"{ETQ[k]}: {v}" for k, v in cuenta.items() if v)
    envio = enviar(para, f"{d.get('titulo')} · {resumen or 'sin observaciones'}",
                   f"Adjunto el informe «{d.get('titulo')}» ({d.get('alcance', '')}).\n\n{resumen}\n\nGenerado por Ansible Automation Platform.",
                   contenido, nombre) if para else "sin correo indicado"
    return {"archivo": nombre, "envio": envio, "cuenta": cuenta}
