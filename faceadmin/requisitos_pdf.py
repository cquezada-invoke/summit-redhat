"""Documento para el cliente: requisitos técnicos para instalar la PoC de FaceOps (prueba de concepto en las dependencias del cliente) (PDF)."""
import io
from datetime import date

MIN = {"cpu": 4, "ram_gb": 16, "disco_gb": 60}
CLIENTE = ("Consola FaceOps", "Base de datos de FaceOps", "Red Hat Ansible Automation Platform", "Base de datos de AAP", "Caché de AAP",
           "Entornos de ejecución y decisión", "Ansible Lightspeed y MCP", "Catálogo de acciones")


def pdf_requisitos(c, d, o, version, prods, fadmin=""):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"Requisitos técnicos (solo PoC) · FaceOps · {c['nombre']}", author="Invoke")
    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("h1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=18, leading=22, alignment=TA_LEFT, spaceAfter=2)
    H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, leading=16, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#151515"))
    P = ParagraphStyle("p", parent=ss["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13)
    S = ParagraphStyle("s", parent=P, fontSize=8.5, textColor=colors.HexColor("#6a6e73"))
    C = ParagraphStyle("c", parent=P, fontSize=9, leading=12)

    def tabla(filas, anchos, cab=True):
        t = Table([[Paragraph(str(x), C) for x in f] for f in filas], colWidths=anchos, repeatRows=1 if cab else 0)
        st = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d2d2d2")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
        if cab:
            st += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
        t.setStyle(TableStyle(st))
        return t

    e = []
    e.append(Paragraph("Requisitos técnicos para la PoC de FaceOps", H1))
    e.append(Paragraph(f"{c['nombre']} · PoC {d.get('numero') or ''} · FaceOps {version} · Invoke", S))
    e.append(Spacer(1, 6))
    aviso = Table([[Paragraph("<b>DOCUMENTO SOLO PARA LA PoC (PRUEBA DE CONCEPTO).</b> Describe una instalación mínima en una sola máquina para que el cliente conozca FaceOps "
                              "durante la PoC. <b>No es la arquitectura de una instalación productiva</b>: la instalación final puede requerir una arquitectura "
                              "más compleja (alta disponibilidad, componentes en máquinas separadas, segmentación de red, integración con la seguridad y la "
                              "operación del cliente), que define la preventa de Invoke junto al cliente.", P)]], colWidths=[174 * mm])
    aviso.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fdf0d5")), ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#c58c00")),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    e.append(aviso)
    e.append(Spacer(1, 8))
    e.append(tabla([["Cliente", c["nombre"]], ["PoC", f"{d.get('numero') or ''} · {d['semanas']} semana(s) · {d['desde']} al {d['hasta']}"],
                    ["Oportunidad", o.get("numero") or "—"], ["Versión de FaceOps a instalar", version], ["Fecha del documento", date.today().isoformat()]],
                   [55 * mm, 119 * mm], cab=False))
    e.append(Paragraph("Qué se instala", H2))
    e.append(Paragraph("En una máquina dedicada del cliente se instalan la consola FaceOps y Red Hat Ansible Automation Platform con sus bases de datos. "
                       "La instalación la lanza un comando que entrega Invoke; valida los requisitos, instala y, si algo falla, deshace todo lo que hizo "
                       "y deja la máquina como estaba. Toma entre 1 y 1,5 horas.", P))
    e.append(Spacer(1, 4))
    e.append(tabla([["Componente", "Versión"]] + [[p["prod"], p["version"]] for p in prods if p["prod"] in CLIENTE], [110 * mm, 64 * mm]))
    e.append(Paragraph("1 · Máquina", H2))
    e.append(tabla([["Requisito", "Detalle"],
                    ["Sistema operativo", "Red Hat Enterprise Linux 9, instalación mínima o servidor"],
                    ["Procesador", f"{MIN['cpu']} vCPU como mínimo"], ["Memoria", f"{MIN['ram_gb']} GB como mínimo"],
                    ["Disco", f"{MIN['disco_gb']} GB libres en / como mínimo (si el disco crece, el instalador extiende la partición)"],
                    ["SELinux", "En modo enforcing (recomendado)"],
                    ["Uso", "Máquina dedicada: el instalador puede reemplazar instalaciones previas de AAP, FaceOps o el usuario «aap»"],
                    ["Acceso", "Un usuario con sudo para pegar y ejecutar el comando de instalación"],
                    ["Nombre de host", "Un nombre de 60 caracteres o menos (si no, el instalador fija uno propio)"]], [45 * mm, 129 * mm]))
    red = [Paragraph("2 · Red: dominios a los que se conecta la máquina", H2),
           Paragraph("La máquina solo necesita salida HTTPS a estos dominios. No se pide abrir accesos entrantes.", P), Spacer(1, 3),
           tabla([["Dominio", "Puerto", "Para qué"],
                    [fadmin or "FaceAdmin de Invoke", "HTTPS 8843 (salida)", "validar la PoC, descargar el paquete de instalación (≈4 GB) y avisos de la instancia"],
                    ["cdn.redhat.com", "HTTPS 443 (salida)", "repositorios de paquetes de Red Hat Enterprise Linux"],
                    ["subscription.rhsm.redhat.com", "HTTPS 443 (salida)", "registro de la suscripción de Red Hat"]], [62 * mm, 34 * mm, 78 * mm])]
    e.append(KeepTogether(red))
    e.append(Paragraph("3 · Suscripciones de Red Hat", H2))
    e.append(Paragraph("<b>Las suscripciones las pone Invoke.</b> Ansible Automation Platform y Red Hat Enterprise Linux quedan cubiertos con las "
                       "suscripciones de Invoke como proveedor de Red Hat (CCSP): el cliente no necesita entregar nada para la PoC.", P))
    e.append(Spacer(1, 4))
    e.append(Paragraph("Solo si el cliente prefiere usar sus propias suscripciones (porque ya las tiene o quiere ponerlas), debe entregar a Invoke:", P))
    e.append(Spacer(1, 3))
    e.append(tabla([["Suscripción propia del cliente (opcional)", "Qué entregar a Invoke"],
                    ["Ansible Automation Platform", "Manifiesto de suscripción (.zip) del portal de Red Hat (access.redhat.com › Subscriptions › Subscription Allocations)"],
                    ["Red Hat Enterprise Linux", "ID de organización y nombre de una clave de activación (Subscriptions › Activation Keys). "
                     "Si la máquina ya está registrada en Red Hat, se usa su suscripción"]], [62 * mm, 112 * mm]))
    e.append(Paragraph("4 · Cómo sigue", H2))
    pasos = ["El cliente prepara la máquina y la red de este documento (y, solo si usará las suyas, entrega sus suscripciones).",
             "Invoke deja las suscripciones listas y genera el comando de instalación, propio de este cliente.",
             "El cliente pega el comando en la máquina como usuario con sudo: valida, instala y confirma.",
             "Al terminar, el técnico de Invoke a cargo recibe por correo su acceso de administrador y abre los accesos del cliente.",
             f"La PoC dura hasta el {d['hasta']}; para seguir operando se firma el contrato."]
    e.append(KeepTogether([Paragraph(f"{i}. {t}", P) for i, t in enumerate(pasos, 1)]))
    e.append(Spacer(1, 10))
    e.append(Paragraph("Documento solo para la PoC, generado por FaceAdmin · Invoke. Los valores pueden cambiar con la versión de FaceOps; el comando de "
                       "instalación valida los requisitos antes de hacer cualquier cambio. La arquitectura productiva se define en la preventa.", S))

    def pie(cv, dc):
        cv.saveState()
        cv.setFont("Helvetica", 7.5)
        cv.setFillColor(colors.HexColor("#6a6e73"))
        cv.drawString(18 * mm, 9 * mm, f"Invoke · FaceOps {version} · requisitos técnicos SOLO PARA PoC · {c['nombre']}")
        cv.drawRightString(A4[0] - 18 * mm, 9 * mm, f"página {dc.page}")
        cv.restoreState()
    doc.build(e, onFirstPage=pie, onLaterPages=pie)
    return buf.getvalue()
