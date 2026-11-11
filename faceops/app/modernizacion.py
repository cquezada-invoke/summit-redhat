"""Modernización: las migraciones ya trabajadas, agrupadas por origen → destino (qué versión antigua lleva a cuál nueva).
Una sola definición alimenta las tres páginas: Arquitectura › Modernización de aplicaciones, Sistemas operativos ›
Modernización y Sistemas operativos › Actualizaciones. Cada opción es una acción del catálogo (se busca por su nombre en
AAP) y se lanza con el flujo de siempre: formulario, ticket y aprobación."""
import re

# página → (título, subtítulo, [grupo: (origen, destino, nota, [patrones del nombre en AAP])])
PAGINAS = {
    "aplicaciones": ("Modernización de aplicaciones",
                     "Migraciones de aplicaciones y middleware desde versiones antiguas a plataformas actuales",
                     [("Oracle WebLogic", "JBoss EAP 8 en OpenShift 4.x", "Primero se evalúa el dominio y cada aplicación; luego se migra sin tocar el origen",
                       [r"^Migración · WebLogic a OpenShift · Evaluar$", r"^Migración · WebLogic a OpenShift$"]),
                      ("JBoss AS 5/6 · JBoss EAP 6/7", "JBoss EAP 8 (Jakarta EE 10)", "Evaluar o migrar las aplicaciones de un servidor JBoss a uno con EAP 8",
                       [r"^Migración · JBoss a EAP 8$"]),
                      ("IIS · .NET en Windows", "Contenedores en OpenShift", "Clasifica sitios y aplicaciones de IIS para llevarlos a OpenShift",
                       [r"^Migración · IIS a OpenShift$"]),
                      ("Kubernetes", "OpenShift en GCP", "Pasos en orden: evaluar origen, preparar destino, convertir manifiestos, migrar y conmutar",
                       [r"^Migración K8s → OpenShift · Evaluar origen$", r"^Migración K8s → OpenShift · Preparar destino$",
                        r"^Migración K8s → OpenShift · Convertir manifiestos$", r"^Migración K8s → OpenShift · Migrar aplicación$",
                        r"^Migración K8s → OpenShift · Validar y conmutar$"])]),
    "so": ("Modernización de sistemas operativos",
           "Cambios de versión mayor o de distribución: de un sistema antiguo o sin soporte a uno con parches",
           [("Cualquier Linux", "Versión de destino recomendada", "Evaluación de solo lectura: versión de destino, camino y semáforo",
             [r"^Migración · Evaluar sistema operativo$"]),
            ("RHEL 6 · 7 · 8", "RHEL con soporte (8 · 9 · 10)", "Saltos in-place con Leapp, snapshot previo y vuelta atrás si falla",
             [r"^Migración · RHEL a versión soportada$"]),
            ("CentOS · Rocky · AlmaLinux · Oracle Linux", "RHEL", "Conversión con Convert2RHEL y, si la política lo pide, salto a la versión con parches",
             [r"^Migración · CentOS a RHEL$"])]),
    "so_actualizaciones": ("Actualizaciones de sistemas operativos",
                           "Parches y actualizaciones dentro de la misma versión del sistema",
                           [("RHEL", "Erratas y parches al día", "Parches de seguridad, CVE y actualización de paquetes en la misma versión",
                             [r"^Seguridad · Aplicar parches", r"^(Sistema|RHEL|Linux) · (Actualizar|Aplicar actualizaciones|Parches)"]),
                            ("Windows Server", "Actualizaciones de Windows al día", "Windows Update y parches acumulativos",
                             [r"^Windows · (Actualiza|Aplicar (parches|actualizaciones)|Windows Update|Parches)"]),
                            ("Satellite", "Contenido y parches gestionados", "Registro en Satellite para recibir contenido y parches",
                             [r"^Satellite · Incorporar RHEL$"])]),
}


def armar(clave, autos):
    """Grupos de la página con las acciones del catálogo que calzan (en el orden de los patrones)."""
    titulo, sub, grupos = PAGINAS[clave]
    out = []
    for origen, destino, nota, pats in grupos:
        xs = []
        for p in pats:
            xs += [a for a in sorted(autos, key=lambda x: x["nombre_aap"]) if re.search(p, a["nombre_aap"]) and a not in xs]
        if xs:
            out.append({"origen": origen, "destino": destino, "nota": nota, "opciones": xs})
    return titulo, sub, out
