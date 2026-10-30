"""Icono y color de cada automatización (tarjetas estilo OperatorHub). Se elige por palabras del nombre, la categoría y la skill."""
import re

# (patrón, clave) en orden: gana el primero que calza
REGLAS = [
    (r"openshift|\bocp\b|kubernetes", "openshift"),
    (r"^storage|volumen|snapshot|lun", "storage"), (r"segmento", "segmento"), (r"windows|iis|\bad\b|active directory", "windows"), (r"jboss|eap|wildfly", "jboss"),
    (r"postgres|base de datos|\bbd\b|mysql|oracle", "postgres"), (r"nginx|apache|httpd|publicar|web", "web"),
    (r"keycloak|midpoint|idm|directory|identidad|iga|ldap|usuario|sso", "identidad"),
    (r"activemq|kafka|amq|mensaj", "mensajeria"), (r"exporta|monitoreo|prometheus|m[eé]trica|alerta", "monitoreo"),
    (r"puerto|acceso de red|\bred\b|firmware|switch|router|vlan|flujo", "red"),
    (r"certific|tls|\bca\b|pki", "certificado"), (r"firewall|waf|bloque|hardening|seguridad|vulnerab", "seguridad"),
    (r"nube|cloud|gcp|aws|azure|hu[eé]rfano", "nube"), (r"satellite|registrar en red hat|suscrip", "registro"),
    (r"parche|actualiz|patch|errata", "parche"), (r"respald|backup|restaur", "respaldo"),
    (r"reinici|restart|levantar", "reinicio"), (r"apagar|encender|energ", "energia"),
    (r"git|versi[oó]n validada|remediaci|drift|estado|cumplimiento|base aprobada|commit", "git"), (r"despleg|autoservicio|deploy|app", "despliegue"),
    (r"diagn|verificar|validar|recolect|inventario|revis", "diagnostico"),
    (r"servidor|vm|m[aá]quina|sistema operativo|habilitar|base de los equipos|construir", "servidor"),
]

ICONOS = {  # color, trazo SVG (24×24, línea blanca sobre el círculo)
    "openshift": ("#ee0000", '<circle cx="12" cy="12" r="7.5"/><path d="M4 9.5 20 14.5M4 14.5l16-5"/>'),
    "segmento": ("#004080", '<rect x="9" y="3" width="6" height="5" rx="1"/><rect x="3" y="16" width="6" height="5" rx="1"/><rect x="15" y="16" width="6" height="5" rx="1"/><path d="M12 8v4M6 16v-2h12v2"/>'),
    "windows": ("#0078d4", '<path d="M4 6.5 11 5.5v6H4zM13 5.2l7-1v7.3h-7zM4 13h7v6l-7-1zM13 13h7v7l-7-1z" fill="#fff" stroke="none"/>'),
    "jboss": ("#cc0000", '<path d="m12 4 8 4.5-8 4.5-8-4.5z"/><path d="m4 12.5 8 4.5 8-4.5"/><path d="m4 16.5 8 4.5 8-4.5"/>'),
    "postgres": ("#336791", '<ellipse cx="12" cy="6" rx="7" ry="2.6"/><path d="M5 6v12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V6"/><path d="M5 12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6"/>'),
    "web": ("#009639", '<circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4a12 12 0 0 1 0 16M12 4a12 12 0 0 0 0 16"/>'),
    "identidad": ("#009596", '<circle cx="8.5" cy="14.5" r="3.5"/><path d="m11 12 8-8M16 7l2 2M14 9l1.6 1.6"/>'),
    "mensajeria": ("#5e40be", '<rect x="4" y="6" width="16" height="12" rx="1.5"/><path d="m4 8 8 5.5L20 8"/>'),
    "monitoreo": ("#ec7a08", '<path d="M4 19h16"/><path d="m5 15 4-5 4 3 6-7"/>'),
    "certificado": ("#c58c00", '<rect x="5" y="10.5" width="14" height="9.5" rx="1.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/><path d="M12 14v2.5"/>'),
    "seguridad": ("#c9190b", '<path d="M12 3.5 5 6.2V12c0 4.3 2.9 7.3 7 8.3 4.1-1 7-4 7-8.3V6.2z"/><path d="m9 12 2.2 2.2L15.5 10"/>'),
    "red": ("#0066cc", '<rect x="9" y="3.5" width="6" height="5" rx="1"/><rect x="3" y="15.5" width="6" height="5" rx="1"/><rect x="15" y="15.5" width="6" height="5" rx="1"/><path d="M12 8.5v3.5M6 15.5V12h12v3.5"/>'),
    "nube": ("#2b9af3", '<path d="M7.5 18.5h9.5a4 4 0 0 0 .5-8 5.5 5.5 0 0 0-10.6 1.4A3.4 3.4 0 0 0 7.5 18.5z"/>'),
    "registro": ("#ee0000", '<path d="M7 4h7l4 4v12H7z"/><path d="M14 4v4h4M10 13l2 2 3.5-3.5"/>'),
    "parche": ("#8476d1", '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4.5v4h-4"/><path d="M12 8v4l2.5 2"/>'),
    "respaldo": ("#4cb140", '<rect x="4" y="5" width="16" height="4" rx="1"/><path d="M5.5 9v10h13V9M10 13h4"/>'),
    "reinicio": ("#3e8635", '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>'),
    "energia": ("#6a6e73", '<path d="M12 4v8"/><path d="M7.5 7a7 7 0 1 0 9 0"/>'),
    "git": ("#f05133", '<circle cx="7" cy="6" r="2"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="9" r="2"/><path d="M7 8v8M17 11c0 4-6 3-9.3 5.5"/>'),
    "despliegue": ("#0066cc", '<path d="M12 20V9M8 13l4-4 4 4"/><path d="M5 5h14"/>'),
    "diagnostico": ("#2b9af3", '<circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 4.5 4.5"/>'),
    "servidor": ("#6753ac", '<rect x="4" y="4.5" width="16" height="6.5" rx="1.3"/><rect x="4" y="13" width="16" height="6.5" rx="1.3"/><path d="M8 7.8h.01M8 16.3h.01"/>'),
    "storage": ("#795600", '<ellipse cx="12" cy="6" rx="7" ry="2.5"/><path d="M5 6v12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V6"/><path d="M5 10c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5M5 14c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5"/>'),
    "general": ("#3c3f42", '<circle cx="12" cy="12" r="3"/><path d="M12 3.5v2.5M12 18v2.5M3.5 12H6M18 12h2.5M6 6l1.8 1.8M16.2 16.2 18 18M6 18l1.8-1.8M16.2 7.8 18 6"/>'),
}


def clave(a):
    # manda el nombre; la skill y la categoría solo si el nombre no dice nada
    for campo in ("nombre", "habilita_skill", "nombre_aap", "categoria"):
        texto = str(a.get(campo) or "").lower()
        for patron, k in REGLAS:
            if texto and re.search(patron, texto):
                return k
    return "general"


def svg(a, tam=40):
    color, trazo = ICONOS[clave(a)]
    return (f'<svg class="logo-auto" width="{tam}" height="{tam}" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="12" fill="{color}"/>'
            f'<g transform="translate(3.6 3.6) scale(.7)" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{trazo}</g></svg>')
