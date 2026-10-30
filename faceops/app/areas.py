"""Área de cada automatización (cómo se separan en FaceOps): por el equipo que la usa, no por la demo."""
import re

AREAS = [  # clave, nombre, descripción (en este orden se muestran)
    ("infraestructura", "Infraestructura", "Servidores, sistemas operativos, centro de datos, alta disponibilidad y registro en Red Hat"),
    ("redes", "Redes", "Routers y firewall gestionados desde Git: accesos, configuración, firmware y redes para OpenShift"),
    ("seguridad", "Seguridad y cumplimiento", "Drift y estado aprobado en Git, endurecimiento, bloqueo de amenazas y certificados"),
    ("devsecops", "DevSecOps", "Aplicaciones: despliegues, configuración como código, autoservicio y vuelta atrás"),
    ("identidades", "Identidades", "Directorios, IdM, Keycloak, midPoint y gobierno de identidades"),
    ("nube", "Nube", "Recursos en la nube: VMs, costos, huérfanos y reglas peligrosas"),
    ("operacion", "Operación y observabilidad", "Monitoreo, diagnóstico y autorremediación del día a día"),
    ("arquitectura", "Arquitectura", "Segmentos de negocio y su arquitectura"),
]
NOMBRES = {k: n for k, n, _ in AREAS}

EXPLICITAS = {
    "FaceOps · Nuevo segmento de negocio": "arquitectura",
    "DC · Base de los equipos": "seguridad", "Seguridad · Revisar CVEs": "seguridad", "Seguridad · Aplicar parches de seguridad": "seguridad", "Vault · Preparar servidor": "seguridad", "FaceOps · Aplicar política de seguridad": "seguridad", "Remediación · Renovar certificado": "seguridad",
    "Remediación · Volver a la versión validada": "devsecops", "Remediación · Rollback con aprobación": "devsecops",
    "Plataforma · Actualizar firmware": "redes", "Nube · Vigilar firewall": "nube",
    "DC · Satellite": "infraestructura", "FaceOps · Respaldo continuo en Git": "infraestructura",
    "FaceOps · Recuperar servidor": "infraestructura", "FaceOps · Eliminar servidor": "infraestructura", "FaceOps · Volver a una versión": "infraestructura", "DC · Registrar en Red Hat": "infraestructura",
}
REGLAS = [  # (patrón sobre nombre_aap y nombre, área)
    (r"^red ·|acceso de red|firmware", "redes"),
    (r"^nube ·", "nube"),
    (r"drift|^estado ·|cumplimiento|bloquear|seguridad|hardening|certific", "seguridad"),
    (r"active directory|idm|directory server|midpoint|keycloak|iga|identidad", "identidades"),
    (r"^jboss ·|autoservicio|^plataforma ·|despleg|rollback", "devsecops"),
    (r"monitoreo|exportador|^remediación ·|diagnost|reiniciar|logs|caída|iis", "operacion"),
    (r"^storage ·", "infraestructura"),
    (r"habilitar servidor|apagar|replicar|^dc ·|satellite|servidor", "infraestructura"),
]


def area_de(a):
    if a.get("area"):
        return a["area"]
    n = a.get("nombre_aap") or ""
    if n in EXPLICITAS:
        return EXPLICITAS[n]
    texto = f"{n} {a.get('nombre') or ''}".lower()
    for patron, area in REGLAS:
        if re.search(patron, texto):
            return area
    return "operacion"
