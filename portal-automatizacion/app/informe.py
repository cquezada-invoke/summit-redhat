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
    hallazgos = [{"seccion": s["titulo"], "afectados": len(h.get("objetos") or []), **{k: h.get(k) for k in ("sev", "titulo", "detalle", "porque")}}
                 for s in d.get("secciones", []) for h in s.get("hallazgos", [])]
    if d.get("tipo") == "ejecucion":
        pedido = ("Eres un arquitecto de Red Hat que escribe para la gerencia de TI de un cliente. Este es el registro de una ejecución "
                  f"automática «{d.get('titulo')}» en Ansible Automation Platform (pasos, aprobación, resultado y mensajes). Escribe en español, "
                  "texto plano sin markdown: 1) en 3 a 5 líneas qué se hizo y con qué resultado, en lenguaje de negocio; 2) qué cambió en los "
                  "sistemas (o que no cambió nada); 3) si falló algo, la causa probable y qué hacer. Sin inventar datos.\n\n"
                  + json.dumps({"parametros": d.get("parametros"), "pasos": hallazgos}, ensure_ascii=False)[:30000])
    else:
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
    ejec = d.get("tipo") == "ejecucion"
    if ejec:
        out.pop()   # tabla de severidades: no aplica a un registro de ejecución
        if d.get("parametros"):
            out.append(Paragraph("Parámetros de la ejecución", s_h))
            out.append(Paragraph(esc(" · ".join(f"{k}: {v}" for k, v in d["parametros"].items()))[:3000], s_s))
    out.append(Spacer(1, 4))
    if not ejec:
      out.append(Paragraph("Cómo leer este informe: cada revisión tiene una severidad fija según su impacto (crítico: explotable o con "
                         "pérdida de servicio; alto: riesgo serio de seguridad; medio: buena práctica incumplida; bajo: informativo). Dentro de "
                         "cada sección los hallazgos van de más a menos grave, con los objetos afectados, por qué importa, qué hacer y su "
                         "referencia (CIS, NIST o Red Hat).", s_s))
    ia = _resumen_ia(d)
    if ia:
        out.append(Paragraph("Qué se hizo, en resumen" if ejec else "Resumen ejecutivo", s_h))
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
                filas.append([Paragraph(esc(h["detalle"]).replace("\n", "<br/>"), s_s)])
            if h.get("porque"):
                filas.append([Paragraph(f"<i>Por qué importa:</i> {esc(h['porque'])}", s_n)])
            if h.get("objetos"):
                obj = list(h["objetos"])
                filas.append([Paragraph(f"<b>Afectados ({len(obj)}):</b> " + esc(", ".join(obj[:150])) + (" …" if len(obj) > 150 else ""), s_s)])
            if h.get("recomendacion"):
                filas.append([Paragraph(f"<i>Qué hacer:</i> {esc(h['recomendacion'])}", s_n)])
            if h.get("referencia"):
                filas.append([Paragraph(f"Referencia: {esc(h['referencia'])}", s_s)])
            tb = Table(filas, colWidths=[180 * mm])
            tb.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d2d2d2")), ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(c)),
                                    ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 7)]))
            out.append(KeepTogether([tb, Spacer(1, 4)]))
    out += [Spacer(1, 8), Paragraph(("Registro generado automáticamente por Ansible Automation Platform al terminar la ejecución. " if ejec else
                                     "Generado automáticamente por Ansible Automation Platform (análisis de solo lectura: no se cambió nada en los sistemas). ") +
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


def _explicacion_ia(d):
    """Por qué el resultado, en lenguaje de negocio (Claude). Solo objetivo, parámetros y resultado: nada del proceso interno."""
    clave = os.environ.get("ANTHROPIC_API_KEY", "")
    if not clave:
        return ""
    pedido = ("Eres un arquitecto de Red Hat que escribe para el cliente (gerencia de TI). Una automatización terminó. Con su objetivo, "
              "sus parámetros y su resultado, escribe en español, texto plano sin markdown y sin hablar de pasos internos, logs ni de cómo "
              "se ejecutó: un párrafo de 3 a 5 líneas que explique qué significa este resultado y por qué salió así (por ejemplo, por qué "
              "no se limpió nada o por qué se renovó), y una línea final «Qué sigue:» con la acción recomendada o «nada que hacer». "
              "Sin inventar datos.\n\n" + json.dumps({k: d.get(k) for k in ("titulo", "objetivo", "que_hace", "parametros", "resultados", "estado")},
                                                   ensure_ascii=False)[:20000])
    try:
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", method="POST", data=json.dumps({
            "model": os.environ.get("PORTAL_MODELO", "claude-sonnet-5-5"), "max_tokens": 600,
            "messages": [{"role": "user", "content": pedido}]}).encode(),
            headers={"x-api-key": clave, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return "".join(b.get("text", "") for b in json.load(r).get("content", []))
    except Exception:  # noqa: BLE001 - el documento sale igual, sin la explicación
        return ""


def pdf_resultado(d):
    """Documento de resultado de una automatización: objetivo, qué hace, resultado y por qué."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    st = getSampleStyleSheet()
    s_t = ParagraphStyle("t", parent=st["Title"], fontName="Helvetica-Bold", fontSize=18, alignment=TA_LEFT, spaceAfter=2)
    s_h = ParagraphStyle("h", parent=st["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, spaceBefore=10, spaceAfter=4)
    s_n = ParagraphStyle("n", parent=st["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13)
    s_s = ParagraphStyle("s", parent=s_n, fontSize=8, textColor=colors.HexColor("#6a6e73"))
    esc = lambda x: str(x if x is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    color = {"ok": "#3e8635", "atencion": "#ec7a08", "critico": "#c9190b", "no_aprobada": "#6a6e73"}
    rot = {"ok": "Completada", "atencion": "Requiere atención", "critico": "Requiere atención", "no_aprobada": "No aprobada: no se ejecutó"}
    est = d.get("estado", "ok")
    out = [Paragraph(f"{esc(d.get('cliente', 'Claro Puerto Rico'))} · Portal de automatización (Ansible Automation Platform)", s_s),
           Paragraph(esc(d.get("titulo", "Resultado")), s_t),
           Paragraph(f"<font color='{color.get(est, '#151515')}'>■ <b>{rot.get(est, est)}</b></font> · {datetime.now().strftime('%Y-%m-%d %H:%M')}", s_n),
           Paragraph("Objetivo de este documento", s_h),
           Paragraph("Dejar constancia de qué se pidió a esta automatización, qué resultado entregó y por qué, para que cualquier persona del "
                     "equipo lo entienda sin entrar a Ansible.", s_n)]
    if d.get("objetivo") or d.get("que_hace"):
        out.append(Paragraph("Qué hace esta automatización", s_h))
        if d.get("objetivo"):
            out.append(Paragraph(f"<b>Para qué:</b> {esc(d['objetivo'])}", s_n))
        if d.get("que_hace"):
            out.append(Paragraph(f"<b>Cómo:</b> {esc(d['que_hace'])}", s_n))
    if d.get("parametros"):
        out.append(Paragraph("Qué se pidió", s_h))
        t = Table([[Paragraph(esc(k), s_s), Paragraph(esc(v), s_n)] for k, v in d["parametros"].items()], colWidths=[50 * mm, 130 * mm])
        t.setStyle(TableStyle([("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e5e5")), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d2d2d2"))]))
        out.append(t)
    out.append(Paragraph("Resultado", s_h))
    if not d.get("resultados"):
        out.append(Paragraph("No se ejecutó (la solicitud no fue aprobada)." if est == "no_aprobada" else "Sin datos de resultado.", s_n))
    for r in d.get("resultados", []):
        out.append(Paragraph(f"<b>{esc(r.get('titulo'))}</b>", s_n))
        if r.get("filas"):
            t = Table([[Paragraph(esc(f[0]), s_s), Paragraph(esc(f[1] if len(f) > 1 else ""), s_n)] for f in r["filas"]], colWidths=[50 * mm, 130 * mm])
            t.setStyle(TableStyle([("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e5e5")), ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d2d2d2"))]))
            out += [Spacer(1, 3), t, Spacer(1, 4)]
    ia = _explicacion_ia(d)
    if ia:
        out.append(Paragraph("Por qué este resultado", s_h))
        for parr in [p for p in ia.split("\n") if p.strip()]:
            out.append(Paragraph(esc(parr), s_n))
        out.append(Paragraph("Escrito por Claude (Anthropic) a partir del objetivo, los parámetros y el resultado.", s_s))
    out += [Spacer(1, 10), Paragraph(f"{esc(d.get('alcance', ''))} · ejecución AAP #{esc(d.get('job', '-'))}. Queda auditada en Ansible Automation Platform.", s_s)]
    buf = BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                      title=d.get("titulo", "Resultado"), author="Portal de automatización · Invoke").build(out)
    return buf.getvalue(), {}


def procesar(d):
    """Arma el PDF, lo guarda (descargable desde el portal) y lo envía al correo indicado."""
    contenido, cuenta = pdf_resultado(d) if d.get("tipo") == "ejecucion" else pdf(d)
    os.makedirs(DIR, exist_ok=True)
    nombre = f"{d.get('archivo', 'informe')}-{datetime.now().strftime('%Y%m%d-%H%M')}.pdf"
    with open(os.path.join(DIR, nombre), "wb") as f:
        f.write(contenido)
    para = (d.get("correo") or "").strip()
    resumen = " · ".join(f"{ETQ[k]}: {v}" for k, v in cuenta.items() if v)
    if d.get("tipo") == "ejecucion":
        resumen = {"ok": "completada", "atencion": "requiere atención", "no_aprobada": "no aprobada"}.get(d.get("estado"), "")
    envio = enviar(para, f"{d.get('titulo')} · {resumen or 'sin observaciones'}",
                   f"Adjunto el informe «{d.get('titulo')}» ({d.get('alcance', '')}).\n\n{resumen}\n\nGenerado por Ansible Automation Platform.",
                   contenido, nombre) if para else "sin correo indicado"
    return {"archivo": nombre, "envio": envio, "cuenta": cuenta}
