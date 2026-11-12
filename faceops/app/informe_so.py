"""Informe de impacto de migración de sistema operativo (PDF): lo genera FaceOps cuando termina «Migración · Evaluar sistema
operativo» y lo envía por correo a quien la pidió. Trae las 12 categorías para evaluarlas a mano (lo que se encontró, qué
significa el cambio y una casilla para la decisión) y un instructivo según el salto de versión, desde plantillas
(datos/migracion/plantillas.yml) que se irán completando caso a caso."""
import os
import re
from datetime import datetime, timezone
from io import BytesIO

import yaml

from .bd import ahora, con, ej, q, q1

DIR = os.environ.get("FACEOPS_INFORMES", os.path.join(os.path.dirname(os.environ.get("FACEOPS_BD", "/var/lib/faceops/faceops.db")), "informes"))
PLANTILLAS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "migracion", "plantillas.yml")
ESQUEMA = """
CREATE TABLE IF NOT EXISTS informes_so (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, equipo TEXT NOT NULL, ticket_id INTEGER, archivo TEXT, origen TEXT, destino TEXT,
  para TEXT DEFAULT '', envio TEXT DEFAULT '', creado TEXT);
CREATE INDEX IF NOT EXISTS informes_so_eq ON informes_so(cliente_id, equipo);
"""
COLOR = {"rojo": "#c9190b", "amarillo": "#c58c00", "info": "#2b9af3", "verde": "#3e8635"}
ETQ = {"rojo": "bloquea o requiere trabajo", "amarillo": "revisar", "info": "a considerar", "verde": "sin observaciones"}


def iniciar():
    con().executescript(ESQUEMA)


def plantillas():
    with open(PLANTILLAS, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def saltos(plan):
    """Claves de plantilla según el camino: conversión, última menor y cada salto de versión mayor."""
    out = []
    camino = " ".join(plan.get("camino") or [])
    if plan.get("convertir"):
        out.append("convertir")
    if "versión menor" in camino:
        out.append("menor")
    for v in plan.get("saltos") or []:
        out.append(f"{v}-{v + 1}")
    return out


def _version_mayor(txt):
    m = re.search(r"(\d+)", str(txt or ""))
    return int(m.group(1)) if m else None


def secuencia(e, ev=None):
    """Versiones de RHEL desde la actual hasta la última: actual → última menor (si falta) → cada versión mayor siguiente."""
    from .inventario import sistema_de
    so = f"{e.get('so') or ''}"
    if sistema_de(e) != "linux":
        return []
    m = re.search(r"(\d+)\.(\d+)", so) or re.search(r"(\d+)(?:\.(\d+))?", e.get("so_version") or so)
    if not m:
        return []
    mayor, menor = int(m.group(1)), int(m.group(2) or 0)
    ultima = {6: 10, 7: 9, 8: 10}
    pasos = [{"version": f"{mayor}.{menor}", "actual": True}]
    if mayor in ultima and menor < ultima[mayor]:
        pasos.append({"version": f"{mayor}.{ultima[mayor]}", "mayor": mayor, "menor": True})
    for v in range(mayor + 1, 11):
        pasos.append({"version": f"{v}.{ultima[v]}" if v in ultima else str(v), "mayor": v})
    evaluado = _version_mayor((ev or {}).get("plan", {}).get("destino")) if ev else None
    for p in pasos:
        p["evaluado"] = bool(evaluado and p.get("mayor") == evaluado and not p.get("menor"))
    return pasos


# ------------------------------------------------------------------ PDF
def generar(cliente, equipo, ticket, ev):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    pl, plan = plantillas(), ev.get("plan") or {}
    claves = saltos(plan)
    gen = pl.get("general", {})
    st = getSampleStyleSheet()
    s_t = ParagraphStyle("t", parent=st["Title"], fontName="Helvetica-Bold", fontSize=18, alignment=TA_LEFT, spaceAfter=2)
    s_h = ParagraphStyle("h", parent=st["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#151515"))
    s_n = ParagraphStyle("n", parent=st["BodyText"], fontName="Helvetica", fontSize=9, leading=12)
    s_s = ParagraphStyle("s", parent=s_n, fontSize=8, textColor=colors.HexColor("#6a6e73"))
    s_b = ParagraphStyle("b", parent=s_n, fontName="Helvetica-Bold")
    esc = lambda x: str(x or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = []
    from .correo import hora   # en la zona de quien pidió el ticket (perfil o último ingreso) o en UTC
    out.append(Paragraph("FaceOps · Managed Service de Invoke", s_s))
    out.append(Paragraph(f"Impacto de migración · {esc(equipo)}", s_t))
    out.append(Paragraph(f"{esc(cliente.get('nombre'))} · ticket {esc(ticket.get('numero'))} · {esc(hora(datetime.now(timezone.utc), usuario_id=ticket.get('usuario_id')))}", s_s))
    out.append(Spacer(1, 6))
    fil = [["Versión actual", plan.get("origen", "—"), "Soporte", plan.get("soporte", "—")],
           ["Destino evaluado", plan.get("destino", "—"), "Acción", plan.get("accion", "—")],
           ["Camino", " → ".join(plan.get("camino") or []) or "—", "Semáforo", plan.get("semaforo", "—")]]
    t = Table([[Paragraph(f"<b>{esc(a)}</b>", s_s), Paragraph(esc(b), s_n), Paragraph(f"<b>{esc(c)}</b>", s_s), Paragraph(esc(d), s_n)] for a, b, c, d in fil],
              colWidths=[28 * mm, 70 * mm, 20 * mm, 62 * mm])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d2d2d2")), ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e5e5")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
                           ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f5f5f5"))]))
    out.append(t)
    for k in claves:
        if pl.get(k, {}).get("resumen"):
            out.append(Spacer(1, 4))
            out.append(Paragraph(f"<b>{esc(pl[k].get('titulo', k))}.</b> {esc(pl[k]['resumen'])}", s_n))
    out.append(Paragraph("Evaluación por categoría", s_h))
    out.append(Paragraph("Para cada categoría: lo que se encontró en el equipo, qué significa el cambio y la decisión de quien evalúa. "
                         "Marque una opción y anote lo que se hará antes de migrar.", s_s))
    for c in ev.get("categorias") or []:
        recs = [pl[k]["categorias"][c["nombre"]] for k in claves if (pl.get(k, {}).get("categorias") or {}).get(c["nombre"])]
        if not recs and (gen.get("categorias") or {}).get(c["nombre"]):
            recs = [gen["categorias"][c["nombre"]]]
        filas = [[Paragraph(f"<font color='{COLOR[c['estado']]}'>■</font> <b>{esc(c['nombre'])}</b> · <font color='{COLOR[c['estado']]}'>{ETQ[c['estado']]}</font>", s_n)],
                 [Paragraph(esc(c["que"]), s_s)]]
        if c.get("datos"):
            filas.append([Paragraph(" · ".join(f"<b>{esc(et)}:</b> {esc(v)}" for et, v in c["datos"]), s_n)])
        for h in c.get("hallazgos") or []:
            filas.append([Paragraph(f"<font color='{COLOR.get(h.get('nivel'), '#151515')}'>●</font> {esc(h.get('texto'))}", s_n)])
        for r in recs:
            filas.append([Paragraph(f"<i>Qué significa el cambio:</i> {esc(r)}", s_n)])
        filas.append([Paragraph("Decisión:  [   ] sin impacto     [   ] requiere trabajo previo     [   ] bloquea la migración", s_n)])
        filas.append([Paragraph("Notas: ____________________________________________________________________________________", s_s)])
        tb = Table(filas, colWidths=[180 * mm])
        tb.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d2d2d2")), ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(COLOR[c["estado"]])),
                                ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5), ("LEFTPADDING", (0, 0), (-1, -1), 7)]))
        out.append(KeepTogether([tb, Spacer(1, 5)]))
    out.append(Paragraph("Instructivo", s_h))
    n = 0
    for k in (claves or []) + ["general"]:
        p = pl.get(k) or {}
        if not p.get("instructivo"):
            continue
        out.append(Paragraph(f"<b>{esc(p.get('titulo', k))}</b>", s_b))
        for paso in p["instructivo"]:
            n += 1
            out.append(Paragraph(f"{n}. {esc(paso)}", s_n))
        out.append(Spacer(1, 4))
    out.append(Spacer(1, 8))
    out.append(Paragraph("Generado por FaceOps a partir de la evaluación «Migración · Evaluar sistema operativo» (sin cambios en el equipo). "
                         "Las recomendaciones provienen de plantillas por versión; confirme siempre con la documentación oficial de Red Hat "
                         "y ensaye en un clon antes de producción.", s_s))
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Impacto de migración · {equipo}", author="FaceOps · Invoke")
    doc.build(out)
    return buf.getvalue()


# ------------------------------------------------------------------ envío
def enviar(para, asunto, cuerpo, adjunto, nombre):
    """Envía por SMTP si está configurado (FACEOPS_SMTP_HOST, _PUERTO, _USUARIO, _CLAVE, _DE). Devuelve el estado del envío."""
    host = os.environ.get("FACEOPS_SMTP_HOST", "")
    if not host:
        return "sin servidor de correo configurado: descárgalo desde FaceOps"
    if not para:
        return "quien lo pidió no tiene correo registrado"
    import smtplib
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"] = os.environ.get("FACEOPS_SMTP_DE", "faceops@invoke.cl")
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
    except Exception as x:  # noqa: BLE001 - el informe queda descargable aunque el correo falle
        return f"no se pudo enviar ({type(x).__name__})"


def desde_ticket(t):
    """Termina la evaluación: un informe por equipo evaluado, guardado y enviado a quien la pidió."""
    from .evaluacion_so import de_equipo
    cli = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"]) or {}
    u = q1("SELECT nombre, correo FROM usuarios WHERE id=?", t.get("usuario_id")) or {}
    os.makedirs(DIR, exist_ok=True)
    for equipo in [x for x in (t.get("limite") or "").split(":") if x]:
        ev = de_equipo(t["cliente_id"], equipo)
        if not ev or ev.get("ticket_id") != t["id"]:
            continue
        pdf = generar(cli, equipo, t, ev)
        nombre = f"impacto-migracion-{equipo}-{t['numero']}.pdf"
        ruta = os.path.join(DIR, f"{t['cliente_id']}-{nombre}")
        with open(ruta, "wb") as f:
            f.write(pdf)
        plan = ev.get("plan") or {}
        envio = enviar(u.get("correo"), f"FaceOps · Impacto de migración de {equipo}: {plan.get('origen', '')} → {plan.get('destino', '')}",
                       f"Hola {u.get('nombre') or ''},\n\nAdjunto el informe de impacto de migración de {equipo} ({plan.get('origen', '')} → "
                       f"{plan.get('destino', '')}), ticket {t['numero']}. Trae las 12 categorías para evaluar a mano y el instructivo del cambio.\n\nFaceOps",
                       pdf, nombre)
        ej("INSERT INTO informes_so(cliente_id, equipo, ticket_id, archivo, origen, destino, para, envio, creado) VALUES (?,?,?,?,?,?,?,?,?)",
           t["cliente_id"], equipo, t["id"], ruta, plan.get("origen", ""), plan.get("destino", ""), u.get("correo") or "", envio, ahora())


def ultimo(cliente_id, equipo):
    return q1("SELECT i.*, t.numero FROM informes_so i LEFT JOIN tickets t ON t.id=i.ticket_id WHERE i.cliente_id=? AND i.equipo=? "
              "ORDER BY i.id DESC LIMIT 1", cliente_id, equipo)
