"""Gobierno › Integraciones: las herramientas externas con que FaceOps conversa por cliente (tickets, automatización,
bóveda, repositorios, monitoreo, IA, nube, identidades). Las credenciales nunca se escriben aquí: viven en la bóveda y una
automatización de Invoke conecta y prueba la herramienta. Mientras no esté probada queda en gris («por conectar»)."""
import os

from .bd import ahora, ej, j, q

CATEGORIAS = {
    "tickets": ("Herramientas de tickets", "Donde nace y se sigue cada ticket del cliente; FaceOps lo crea, informa el avance de AAP y lo cierra"),
    "automatizacion": ("Automatización", "Ejecuta todo lo que FaceOps orquesta; FaceOps nunca entra directo a las máquinas"),
    "boveda": ("Bóveda de secretos", "Credenciales de máquinas y servicios; FaceOps las usa sin mostrarlas"),
    "repositorios": ("Repositorios de código", "Estado aprobado de servidores, arquitectura, pipelines y respaldos como código"),
    "cicd": ("CI/CD", "Herramientas de integración y despliegue continuo (se administran en DevSecOps)"),
    "monitoreo": ("Monitoreo", "Métricas y eventos que alimentan la salud y la autorremediación"),
    "siem": ("SIEM", "Correlación de eventos de seguridad: FaceOps le envía su auditoría y recibe alertas que disparan remediaciones"),
    "vulnerabilidades": ("Evaluación de vulnerabilidades", "Escáneres de CVE: sus hallazgos se suman al escaneo de seguridad y abren tickets de parchado"),
    "identidades": ("Identidades", "Accesos y su gobierno (IGA)"),
    "nube": ("Nubes", "Cuentas de nube donde FaceOps crea y vigila recursos"),
    "ia": ("Inteligencia artificial", "El agente del chat de FaceOps"),
}

TIPOS = {
    "faceops": {"cat": "tickets", "nombre": "FaceOps (tickets propios)", "detalle": "Sin herramienta externa", "color": "#c9190b"},
    "servicenow": {"cat": "tickets", "nombre": "ServiceNow", "detalle": "API REST (Table API)", "color": "#62d84e"},
    "odoo": {"cat": "tickets", "nombre": "Odoo Helpdesk", "detalle": "API JSON-RPC", "color": "#714b67"},
    "servicedeskplus": {"cat": "tickets", "nombre": "ServiceDesk Plus", "detalle": "ManageEngine · API REST v3 (Cloud u on-premise)", "color": "#f68b1f"},
    "jira": {"cat": "tickets", "nombre": "Jira Service Management", "detalle": "API REST de Atlassian", "color": "#0052cc"},
    "glpi": {"cat": "tickets", "nombre": "GLPI", "detalle": "API REST", "color": "#2f3f64"},
    "aap": {"cat": "automatizacion", "nombre": "Ansible Automation Platform", "detalle": "Red Hat · API del controlador", "color": "#ee0000"},
    "vault": {"cat": "boveda", "nombre": "HashiCorp Vault", "detalle": "AppRole por propósito", "color": "#000000"},
    "gitea": {"cat": "repositorios", "nombre": "Gitea", "detalle": "Repositorios de FaceOps", "color": "#609926"},
    "github": {"cat": "repositorios", "nombre": "GitHub", "detalle": "", "color": "#24292f"},
    "gitlab": {"cat": "repositorios", "nombre": "GitLab", "detalle": "", "color": "#fc6d26"},
    "tekton": {"cat": "cicd", "nombre": "Tekton", "detalle": "Pipelines nativos de Kubernetes", "color": "#fd495c"},
    "jenkins": {"cat": "cicd", "nombre": "Jenkins", "detalle": "", "color": "#d33833"},
    "prometheus": {"cat": "monitoreo", "nombre": "Prometheus y Grafana", "detalle": "Agente en cada servidor", "color": "#e6522c"},
    "eda": {"cat": "monitoreo", "nombre": "Event-Driven Ansible", "detalle": "Eventos → automatizaciones", "color": "#ee0000"},
    "zabbix": {"cat": "monitoreo", "nombre": "Zabbix", "detalle": "API JSON-RPC", "color": "#d40000"},
    "dynatrace": {"cat": "monitoreo", "nombre": "Dynatrace", "detalle": "API de problemas y eventos", "color": "#1496ff"},
    "datadog": {"cat": "monitoreo", "nombre": "Datadog", "detalle": "Webhooks y API", "color": "#632ca6"},
    "elastic": {"cat": "monitoreo", "nombre": "Elastic Observability", "detalle": "", "color": "#00bfb3"},
    "splunk": {"cat": "siem", "nombre": "Splunk Enterprise Security", "detalle": "HTTP Event Collector", "color": "#65a637"},
    "qradar": {"cat": "siem", "nombre": "IBM QRadar", "detalle": "Syslog / API", "color": "#0f62fe"},
    "sentinel": {"cat": "siem", "nombre": "Microsoft Sentinel", "detalle": "Log Analytics", "color": "#0078d4"},
    "wazuh": {"cat": "siem", "nombre": "Wazuh", "detalle": "API REST", "color": "#3595f9"},
    "qualys": {"cat": "vulnerabilidades", "nombre": "Qualys VMDR", "detalle": "API de detecciones por host", "color": "#ed2e26"},
    "tenable": {"cat": "vulnerabilidades", "nombre": "Tenable (Nessus / Tenable.io)", "detalle": "API de vulnerabilidades", "color": "#00a0dd"},
    "rapid7": {"cat": "vulnerabilidades", "nombre": "Rapid7 InsightVM", "detalle": "API v3", "color": "#e85e26"},
    "rh_insights": {"cat": "vulnerabilidades", "nombre": "Red Hat Insights", "detalle": "Vulnerabilidades y parches de RHEL", "color": "#ee0000"},
    "acs": {"cat": "vulnerabilidades", "nombre": "Red Hat Advanced Cluster Security", "detalle": "Imágenes y clústeres", "color": "#ee0000"},
    "midpoint": {"cat": "identidades", "nombre": "midPoint (IGA)", "detalle": "Gobierno de identidades", "color": "#1c7cba"},
    "gcp": {"cat": "nube", "nombre": "Google Cloud", "detalle": "Compute Engine", "color": "#4285f4"},
    "anthropic": {"cat": "ia", "nombre": "Claude (Anthropic)", "detalle": "API de mensajes", "color": "#d97757"},
}


def _tabla():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS conexiones (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, tipo TEXT NOT NULL, nombre TEXT NOT NULL, url TEXT DEFAULT '',
      descripcion TEXT DEFAULT '', estado TEXT DEFAULT 'por conectar', autor TEXT, creado TEXT, UNIQUE(cliente_id, nombre));""")


def _derivadas(cliente):
    """Las que FaceOps ya usa hoy para este cliente (no se registran a mano: salen de su configuración)."""
    conf = j(cliente.get("tickets_conf"), {})
    out = [{"tipo": "aap", "nombre": "Ansible Automation Platform", "url": os.environ.get("FACEOPS_AAP_PUBLICA", ""),
            "estado": "operativa" if cliente.get("aap_url") else "por conectar", "fija": True}]
    tt = cliente.get("tickets_tipo") or "faceops"
    out.append({"tipo": tt if tt in TIPOS else "faceops", "nombre": TIPOS.get(tt, TIPOS["faceops"])["nombre"],
                "url": (conf.get("url") or "") if tt == "servicenow" else "", "estado": "operativa", "fija": True, "principal": True})
    if (conf.get("odoo") or {}).get("url"):
        out.append({"tipo": "odoo", "nombre": "Odoo Helpdesk", "url": conf["odoo"]["url"], "estado": "operativa", "fija": True})
    out += [{"tipo": "vault", "nombre": "Bóveda de FaceOps", "url": "", "estado": "operativa", "fija": True},
            {"tipo": "gitea", "nombre": "Repositorios de FaceOps", "url": "/git/", "estado": "operativa", "fija": True},
            {"tipo": "tekton", "nombre": "Tekton", "url": "", "estado": "operativa", "fija": True},
            {"tipo": "prometheus", "nombre": "Prometheus y Grafana", "url": "", "estado": "operativa", "fija": True},
            {"tipo": "eda", "nombre": "Event-Driven Ansible", "url": "", "estado": "operativa", "fija": True},
            {"tipo": "midpoint", "nombre": "midPoint (IGA)", "url": "", "estado": "operativa", "fija": True},
            {"tipo": "gcp", "nombre": "Google Cloud", "url": "", "estado": "operativa", "fija": True},
            {"tipo": "anthropic", "nombre": "Claude (Anthropic)", "url": "", "estado": "operativa", "fija": True}]
    return out


LOGOS = {"odoo", "aap", "vault", "gitea", "tekton", "prometheus", "grafana", "eda", "gcp", "anthropic"}


def catalogo(cliente):
    """Todas las herramientas que FaceOps sabe integrar, por categoría, marcadas «operativa» (en uso y probada) o «inactiva»."""
    _tabla()
    usadas = {}
    for it in _derivadas(cliente) + [dict(r, fija=False) for r in q("SELECT * FROM conexiones WHERE cliente_id=? ORDER BY id", cliente["id"])]:
        x = usadas.setdefault(it["tipo"], it)
        if it.get("estado") == "operativa":
            usadas[it["tipo"]] = dict(x, **it)
    grupos = []
    for k, (titulo, desc) in CATEGORIAS.items():
        xs = []
        for tk, t in TIPOS.items():
            if t["cat"] != k or (tk == "faceops" and (cliente.get("tickets_tipo") or "faceops") != "faceops"):
                continue
            u = usadas.get(tk) or {}
            xs.append({"tipo": tk, "nombre": t["nombre"], "detalle": t.get("detalle", ""), "color": t.get("color", "#6a6e73"),
                       "logo": tk if tk in LOGOS else "", "estado": "operativa" if u.get("estado") == "operativa" else "inactiva",
                       "principal": bool(u.get("principal")), "url": u.get("url") or "", "registro": u.get("nombre") or ""})
        xs.sort(key=lambda x: (x["estado"] != "operativa", x["nombre"].lower()))
        grupos.append({"clave": k, "titulo": titulo, "descripcion": desc, "items": xs,
                       "operativas": sum(1 for x in xs if x["estado"] == "operativa")})
    return grupos


def detalle(cliente, tipo):
    for gr in catalogo(cliente):
        for x in gr["items"]:
            if x["tipo"] == tipo:
                return dict(x, cat=gr["clave"], cat_titulo=gr["titulo"], cat_desc=gr["descripcion"])
    return None


def vista(cliente):
    _tabla()
    items = _derivadas(cliente) + [dict(r, fija=False) for r in q("SELECT * FROM conexiones WHERE cliente_id=? ORDER BY id", cliente["id"])]
    grupos = []
    for k, (titulo, desc) in CATEGORIAS.items():
        xs = []
        for it in items:
            t = TIPOS.get(it["tipo"]) or {}
            if t.get("cat") == k:
                xs.append(dict(it, tipo_nombre=t.get("nombre", it["tipo"]), detalle=t.get("detalle", ""), color=t.get("color", "#6a6e73")))
        grupos.append({"clave": k, "titulo": titulo, "descripcion": desc, "items": xs})
    return grupos


def agregar(cliente, f, autor):
    _tabla()
    tipo, nombre = f.get("tipo", ""), (f.get("nombre") or "").strip()
    if tipo not in TIPOS:
        raise ValueError("Elige la herramienta")
    if not nombre:
        raise ValueError("Ponle un nombre")
    url = (f.get("url") or "").strip()
    if url and not url.startswith(("https://", "http://")):
        raise ValueError("La URL debe empezar con https://")
    ej("INSERT INTO conexiones(cliente_id, tipo, nombre, url, descripcion, autor, creado) VALUES (?,?,?,?,?,?,?)",
       cliente["id"], tipo, nombre[:80], url[:200], (f.get("descripcion") or "").strip()[:200], autor, ahora())
