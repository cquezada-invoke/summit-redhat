"""Área de cada automatización (cómo se separan en FaceOps): por el equipo que la usa, no por la demo."""
import re

AREAS = [  # clave, nombre, descripción (en este orden se muestran)
    ("infraestructura", "Infraestructura", "Servidores, sistemas operativos, centro de datos, alta disponibilidad y registro en Red Hat"),
    ("storage", "Storage", "Discos y volúmenes: del hipervisor o de la SAN, asignación a servidores, ampliación y snapshots"),
    ("redes", "Redes", "Routers y firewall gestionados desde Git: accesos, configuración, firmware y redes para OpenShift"),
    ("seguridad", "Seguridad y cumplimiento", "Drift y estado aprobado en Git, endurecimiento, bloqueo de amenazas y certificados"),
    ("devsecops", "DevSecOps", "Aplicaciones: despliegues, configuración como código, autoservicio y vuelta atrás"),
    ("identidades", "Identidades", "Directorios, IdM, Keycloak, midPoint y gobierno de identidades"),
    ("plataformas", "OpenShift y contenedores", "Clústeres OpenShift: instalación con cualquier método y topología, on-premise o en la nube"),
    ("nube", "Nube", "Recursos en la nube: VMs, costos, huérfanos y reglas peligrosas"),
    ("operacion", "Operación y observabilidad", "Monitoreo, diagnóstico y autorremediación del día a día"),
    ("arquitectura", "Arquitectura", "Segmentos de negocio y su arquitectura"),
    ("migraciones", "Migraciones", "RHEL a versión soportada, CentOS a RHEL, JBoss a OpenShift, IIS a OpenShift"),
]
NOMBRES = {k: n for k, n, _ in AREAS}

EXPLICITAS = {
    "FaceOps · Nuevo segmento de negocio": "arquitectura", "Arquitectura · Evaluar stack tecnológico": "arquitectura",
    "Plataforma · Controlar servicio": "operacion", "Plataforma · Desinstalar": "operacion", "FaceOps · Vincular máquina a la bóveda": "seguridad",
    "DC · Base de los equipos": "seguridad", "Seguridad · Revisar CVEs": "seguridad", "Seguridad · Aplicar parches de seguridad": "seguridad", "Vault · Preparar servidor": "seguridad", "Redis · Preparar servidor": "devsecops", "FaceOps · Aplicar política de seguridad": "seguridad", "Remediación · Renovar certificado": "seguridad",
    "Remediación · Volver a la versión validada": "devsecops", "Remediación · Rollback con aprobación": "devsecops",
    "Plataforma · Actualizar firmware": "redes", "VPN · Preparar servidor": "redes", "VPN · Gestionar acceso": "redes", "Nube · Vigilar firewall": "nube",
    "DC · Satellite": "infraestructura", "FaceOps · Respaldo continuo en Git": "infraestructura",
    "FaceOps · Recuperar servidor": "infraestructura", "FaceOps · Incorporar máquinas": "infraestructura", "FaceOps · Eliminar servidor": "infraestructura", "FaceOps · Volver a una versión": "infraestructura", "FaceOps · Agregar disco": "storage", "Storage · Preparar emulador": "storage", "Storage · Crear volumen": "storage", "Storage · Ampliar volumen": "storage", "Storage · Asignar volumen a servidor": "storage", "Storage · Snapshot de volumen": "storage", "DC · Registrar en Red Hat": "infraestructura",
}
REGLAS = [  # (patrón sobre nombre_aap y nombre, área)
    (r"^openshift ·|openshift", "plataformas"),
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
    if (a.get("categoria") or "") == "Migraciones" or (a.get("nombre_aap") or "").lower().startswith("migración ·"):
        return "migraciones"
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


# ─────────── Secciones del menú de la izquierda (cómo se muestran las automatizaciones) ───────────
# Solo para mostrar y filtrar: las áreas de aprobación siguen siendo AREAS / area_de (no cambian quién aprueba).
SECCIONES = [  # clave, nombre (igual que en el menú), descripción
    ("infraestructura", "Infraestructura", "Centros de datos y traslado de máquinas entre ellos"),
    ("servidores", "Servidores", "Alta, incorporación, baja y energía de servidores"),
    ("redes", "Redes", "Routers, firewall, accesos, VPN y firmware de red"),
    ("storage", "Storage", "Discos y volúmenes: del hipervisor o de la SAN, asignación, ampliación, snapshots y S3"),
    ("so", "Sistemas operativos", "Parches, registro en Red Hat, Satellite, Windows y migraciones de sistema operativo"),
    ("bd", "Bases de datos", "SQL Server, PostgreSQL y su diagnóstico"),
    ("plataformas", "Plataformas", "JBoss, Redis, Vault, Jenkins, directorios e identidades y el servicio de cada plataforma"),
    ("contenedores", "Contenedores", "Clústeres OpenShift y migraciones a contenedores"),
    ("hipervisores", "Hipervisores", "Recursos y encendido de las máquinas en el anfitrión"),
    ("nube", "Nubes", "Recursos en la nube: VMs, costos, huérfanos y reglas peligrosas"),
    ("aplicaciones", "Aplicaciones", "Acciones de los segmentos de negocio y sus despliegues"),
    ("seguridad", "Seguridad", "Drift, cumplimiento, CVEs, certificados, bloqueos y bóveda"),
    ("disponibilidad", "Disponibilidad", "Alta disponibilidad y réplicas"),
    ("arquitectura", "Arquitectura", "Segmentos de negocio, stack tecnológico y homologación de ambientes"),
    ("devsecops", "DevSecOps", "Pipelines, configuración como código y vuelta atrás"),
    ("integraciones", "Integraciones", "Conexión con herramientas del cliente"),
    ("respaldos", "Respaldos y recuperación", "Respaldo continuo, recuperación y vuelta a una versión"),
    ("monitoreo", "Monitoreo", "Agentes, diagnóstico y autorremediación del día a día"),
]
NOMBRES_SECCION = {k: n for k, n, _ in SECCIONES}
SECCION_EXPLICITA = {
    "FaceOps · Agregar disco": "storage", "FaceOps · Ampliar disco": "storage", "Infra · Agregar disco (anfitrión)": "storage",
    "Infra · Ampliar disco (anfitrión)": "storage", "Sistema · Ampliar disco en la máquina": "storage", "Sistema · Preparar disco nuevo": "storage",
    "Infra · Mover máquina entre centros de datos": "infraestructura", "Infra · Preparar centro de datos emulado": "infraestructura",
    "Infra · Ajustar recursos de máquinas": "hipervisores", "Infra · Encender o apagar equipo": "hipervisores", "Infra · Encendido automático de máquinas": "hipervisores",
    "FaceOps · Habilitar servidor": "servidores", "FaceOps · Incorporar máquinas": "servidores", "FaceOps · Eliminar servidor": "servidores",
    "FaceOps · Apagar equipo": "servidores", "FaceOps · Vincular máquina a la bóveda": "seguridad", "FaceOps · Aplicar política de seguridad": "seguridad",
    "FaceOps · Respaldo continuo en Git": "respaldos", "FaceOps · Recuperar servidor": "respaldos", "FaceOps · Volver a una versión": "respaldos",
    "Estado · Volver a un commit": "respaldos", "FaceOps · Replicar con alta disponibilidad": "disponibilidad",
    "FaceOps · Nuevo segmento de negocio": "arquitectura", "Arquitectura · Evaluar stack tecnológico": "arquitectura", "Homologación · Relevar entorno": "arquitectura",
    "FaceOps · Agente de monitoreo (Linux)": "monitoreo", "Monitoreo · Exportador en Windows": "monitoreo", "Incidentes · Verificar error conocido": "monitoreo",
    "Incidentes · Diagnosticar PostgreSQL": "bd", "Integración · PostgreSQL": "integraciones", "Plataforma · Actualizar firmware": "redes",
    "Plataforma · Controlar servicio": "plataformas", "Plataforma · Desinstalar": "plataformas", "Vault · Preparar servidor": "plataformas",
    "DC · Base de los equipos": "seguridad", "DC · Registrar en Red Hat": "so", "Sistema · Registrar en Red Hat": "so", "DC · Satellite": "so",
    "Ciclo de vida · Evaluar destino": "so", "Migración · JBoss a EAP 8": "plataformas", "Migración · IIS a OpenShift": "contenedores",
    "Windows · Preparar IIS": "plataformas", "Remediación · IIS en Windows": "monitoreo", "Windows · Provocar caída de IIS (demo)": "monitoreo",
    "Remediación · Volver a la versión validada": "devsecops", "Remediación · Rollback con aprobación": "devsecops", "Remediación · Renovar certificado": "seguridad",
    "Despliegue · Cambiar tráfico": "aplicaciones", "OpenShift · Desplegar segmento": "aplicaciones", "Fiscalía · Aviso de complemento": "aplicaciones",
    "Remediación · Cola de pagos": "aplicaciones", "Remediación · Limpiar logs": "monitoreo", "SQL Server · Configurar alta disponibilidad": "disponibilidad", "SQL Server · Conmutar réplica primaria": "disponibilidad",
}
SECCION_REGLAS = [  # (patrón sobre «nombre_aap nombre», sección)
    (r"^openshift ·|^migración k8s|openshift", "contenedores"),
    (r"^nube ·", "nube"),
    (r"^red ·|^vpn ·|firmware|acceso de red", "redes"),
    (r"^storage ·|disco|volumen", "storage"),
    (r"^sql server ·|postgres|base de datos", "bd"),
    (r"^satellite ·|^sistema ·|^seguridad · aplicar parches|^migración ·|rhel|centos|^windows ·", "so"),
    (r"drift|^estado ·|cumplimiento|bloquear|^seguridad ·|hardening|certific|bóveda", "seguridad"),
    (r"^jboss ·|^redis ·|^jenkins ·|active directory|idm|directory server|midpoint|keycloak|iga|identidad|^dc ·", "plataformas"),
    (r"pipeline|tekton|devsecops|autoservicio|rollback", "devsecops"),
    (r"monitoreo|exportador|^remediación ·|diagnost|logs|caída|alerta", "monitoreo"),
    (r"^integración ·", "integraciones"),
    (r"^infra ·|centro de datos", "infraestructura"),
    (r"servidor|equipo", "servidores"),
]
_DE_AREA = {"infraestructura": "servidores", "storage": "storage", "redes": "redes", "seguridad": "seguridad", "devsecops": "devsecops",
            "identidades": "plataformas", "plataformas": "contenedores", "nube": "nube", "operacion": "monitoreo", "arquitectura": "arquitectura",
            "migraciones": "so"}


def seccion_de(a):
    """Sección del menú de la izquierda en que se muestra la automatización."""
    n = a.get("nombre_aap") or ""
    if n in SECCION_EXPLICITA:
        return SECCION_EXPLICITA[n]
    texto = f"{n} {a.get('nombre') or ''}".lower()
    for patron, s in SECCION_REGLAS:
        if re.search(patron, texto):
            return s
    return _DE_AREA.get(area_de(a), "monitoreo")
