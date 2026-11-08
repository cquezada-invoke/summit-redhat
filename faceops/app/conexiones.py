"""Gobierno › Integraciones: las herramientas externas con que FaceOps conversa por cliente (tickets, automatización,
bóveda, repositorios, monitoreo, IA, nube, identidades). Las credenciales nunca se escriben aquí: viven en la bóveda y una
automatización de Invoke conecta y prueba la herramienta. Mientras no esté probada queda en gris («por conectar»)."""
import json
import os

from .bd import ahora, ej, j, q, q1

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
    "usuarios": ("Usuarios de FaceOps", "De dónde vienen quienes entran a FaceOps: locales (también cargados desde HTPasswd) o una fuente externa (Active Directory, LDAP o el IGA midPoint) integrada desde Ansible; la clave la valida el Gateway de AAP"),
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
    try:
        con().execute("ALTER TABLE conexiones ADD COLUMN params TEXT DEFAULT '{}'")
    except Exception:  # noqa: BLE001 - ya existe
        pass


# Ruta para probar cada herramienta (respuesta sin efectos); con usuario y clave se prueba además la autenticación
PRUEBA = {"servicenow": "/api/now/table/sys_user?sysparm_limit=1", "odoo": "/web/webclient/version_info", "aap": "/api/controller/v2/ping/",
          "vault": "/v1/sys/health", "gitea": "/api/v1/version", "github": "/", "gitlab": "/api/v4/version", "jenkins": "/api/json",
          "prometheus": "/-/ready", "grafana": "/api/health", "zabbix": "/api_jsonrpc.php", "jira": "/rest/api/2/serverInfo",
          "glpi": "/apirest.php", "servicedeskplus": "/api/v3/requests", "elastic": "/", "splunk": "/services/server/info",
          "wazuh": "/", "qualys": "/api/2.0/fo/scan/?action=list", "tenable": "/server/status", "rapid7": "/api/3",
          "dynatrace": "/api/v1/time", "datadog": "/api/v1/validate", "midpoint": "/midpoint/ws/rest/self", "tekton": "/", "eda": "/"}
CAMPOS = [("url", "URL base", "https://servicio.cliente.com"), ("host", "IP o nombre", "10.0.0.10"), ("puerto", "Puerto", "443"),
          ("usuario", "Usuario o ID de cliente", ""), ("verificar_tls", "Verificar certificado TLS", "si")]


def params(cliente_id, tipo):
    _tabla()
    r = q("SELECT params FROM conexiones WHERE cliente_id=? AND tipo=? ORDER BY id LIMIT 1", cliente_id, tipo)
    return j(r[0]["params"], {}) if r else {}


def _ruta_secreto(cliente_id, tipo):
    return f"integraciones/{cliente_id}/{tipo}"


def guardar(cliente, tipo, f, autor):
    """Parámetros de conexión en FaceOps; la clave o token va a la bóveda (nunca a la base de datos)."""
    from . import boveda
    _tabla()
    if tipo not in TIPOS:
        raise ValueError("Herramienta desconocida")
    p = params(cliente["id"], tipo)
    for k, _, _ in CAMPOS:
        v = (f.get(k) or "").strip()[:300]
        p[k] = v
    if p.get("url") and not p["url"].startswith(("https://", "http://")):
        raise ValueError("La URL debe empezar con https:// o http://")
    if p.get("puerto") and not (p["puerto"].isdigit() and 0 < int(p["puerto"]) < 65536):
        raise ValueError("Puerto no válido")
    ca = (f.get("ca") or "").strip()
    if ca:
        if "BEGIN CERTIFICATE" not in ca or len(ca) > 20000:
            raise ValueError("El certificado debe venir en formato PEM (-----BEGIN CERTIFICATE-----)")
        p["ca"] = ca
    elif f.get("ca_quitar"):
        p.pop("ca", None)
    clave = f.get("clave") or ""
    if clave:
        if not boveda.escribir(_ruta_secreto(cliente["id"], tipo), {"usuario": p.get("usuario", ""), "clave": clave}):
            raise ValueError("La bóveda no aceptó la clave (el rol de FaceOps no tiene escritura): pídele a Invoke que la cargue")
        p["clave_en_boveda"] = ahora()
    nombre = TIPOS[tipo]["nombre"]
    if q("SELECT 1 FROM conexiones WHERE cliente_id=? AND tipo=?", cliente["id"], tipo):
        ej("UPDATE conexiones SET params=?, url=? WHERE cliente_id=? AND tipo=?", json.dumps(p), p.get("url", ""), cliente["id"], tipo)
    else:
        ej("INSERT INTO conexiones(cliente_id, tipo, nombre, url, params, autor, creado) VALUES (?,?,?,?,?,?,?)",
           cliente["id"], tipo, nombre, p.get("url", ""), json.dumps(p), autor, ahora())


def probar(cliente, tipo):
    """Prueba la conexión: resuelve, conecta, valida TLS (con el certificado indicado) y, si hay credencial, la autenticación."""
    import tempfile
    import time
    import requests
    from . import boveda
    p = params(cliente["id"], tipo)
    base = p.get("url") or (f"https://{p['host']}:{p.get('puerto') or 443}" if p.get("host") else "")
    if not base:
        raise ValueError("Falta la URL o la IP de la herramienta")
    if p.get("host") and p.get("url") and p.get("puerto"):
        base = p["url"]
    sec = boveda.leer(_ruta_secreto(cliente["id"], tipo)) or {}
    verificar = p.get("verificar_tls", "si") != "no"
    ca_f = None
    if verificar and p.get("ca"):
        ca_f = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
        ca_f.write(p["ca"]); ca_f.close()
    t0 = time.time()
    res = {"fecha": ahora(), "url": base.rstrip("/") + PRUEBA.get(tipo, "/")}
    try:
        r = requests.get(res["url"], timeout=8, verify=(ca_f.name if ca_f else verificar), allow_redirects=False,
                         auth=(sec.get("usuario") or p.get("usuario"), sec["clave"]) if sec.get("clave") else None)
        res.update(ms=int((time.time() - t0) * 1000), codigo=r.status_code)
        if r.status_code in (401, 403):
            res.update(ok=False, detalle=f"Conecta, pero la credencial no fue aceptada (HTTP {r.status_code})" if sec.get("clave") else
                       f"Conecta (HTTP {r.status_code}); falta cargar la clave para probar la autenticación")
        elif r.status_code < 400:
            res.update(ok=True, detalle=f"Responde HTTP {r.status_code} en {res['ms']} ms" + (" · credencial aceptada" if sec.get("clave") else " · sin credencial cargada"))
        else:
            res.update(ok=False, detalle=f"La herramienta respondió HTTP {r.status_code}")
    except requests.exceptions.SSLError:
        res.update(ok=False, detalle="El certificado TLS no es válido para esta conexión (carga el certificado de la CA o revisa el nombre)")
    except requests.exceptions.ConnectionError:
        res.update(ok=False, detalle="No conecta: revisa la IP, el puerto y que el firewall permita el tráfico desde FaceOps")
    except requests.exceptions.Timeout:
        res.update(ok=False, detalle="No respondió en 8 segundos")
    finally:
        if ca_f:
            os.unlink(ca_f.name)
    p["prueba"] = res
    estado = "operativa" if res.get("ok") and "credencial aceptada" in res.get("detalle", "") else None
    ej("UPDATE conexiones SET params=?" + (", estado='operativa'" if estado else "") + " WHERE cliente_id=? AND tipo=?", json.dumps(p), cliente["id"], tipo)
    return res


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
    from .semillas import limpia
    if limpia():   # instancia de un cliente: solo lo que realmente tiene configurado
        hay = {"vault": bool(os.environ.get("FACEOPS_VAULT_ADDR")), "gitea": bool(os.environ.get("FACEOPS_GITEA_TOKEN")),
               "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("FACEOPS_ANTHROPIC_KEY")), "eda": True}
        out = [x for x in out if x["tipo"] not in ("vault", "gitea", "tekton", "prometheus", "eda", "midpoint", "gcp", "anthropic") or hay.get(x["tipo"])]
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
        if k == "usuarios":
            xs = _fuentes_usuarios(cliente)
        grupos.append({"clave": k, "titulo": titulo, "descripcion": desc, "items": xs,
                       "operativas": sum(1 for x in xs if x["estado"] == "operativa")})
    return grupos


def _fuentes_usuarios(cliente):
    """Tiles de la categoría «Usuarios de FaceOps»: local, HTPasswd, cada fuente conectada y las que se pueden conectar."""
    from . import fuentes_usuarios as fu
    n_ht = (q1("SELECT COUNT(*) AS n FROM usuarios u JOIN perfiles p ON p.usuario_id=u.id WHERE p.cliente_id=? AND u.origen='htpasswd'", cliente["id"]) or {}).get("n", 0)
    xs = [{"tipo": "usr_local", "nombre": "Usuarios locales", "detalle": "Se administran en FaceOps", "color": "#c9190b", "logo": "", "estado": "operativa",
           "principal": False, "url": "", "registro": ""},
          {"tipo": "usr_htpasswd", "nombre": "HTPasswd", "detalle": "Carga de usuarios locales desde un archivo", "color": "#6a6e73", "logo": "",
           "estado": "operativa" if n_ht else "inactiva", "principal": False, "url": "", "registro": f"{n_ht} cargados" if n_ht else ""}]
    colores = {"ad": "#0078d4", "ldap": "#3c6eb4", "midpoint": "#1c7cba"}
    for f in fu.fuentes(cliente["id"]):
        xs.append({"tipo": f"usr_{f['tipo']}", "fuente_id": f["id"], "nombre": f["nombre"], "detalle": fu.TIPOS[f["tipo"]]["nombre"], "color": colores[f["tipo"]],
                   "logo": "midpoint" if f["tipo"] == "midpoint" and "midpoint" in LOGOS else "",
                   "estado": "operativa" if f["activo"] and (f["ultima_sync"] or (f["estado"] or "").startswith("✓")) else "inactiva",
                   "principal": False, "url": "", "registro": ""})
    for k, t in fu.TIPOS.items():
        xs.append({"tipo": f"usr_{k}", "nombre": f"Conectar {t['nombre']}", "detalle": "Nueva fuente de usuarios", "color": colores[k],
                   "logo": "midpoint" if k == "midpoint" and "midpoint" in LOGOS else "", "estado": "inactiva", "principal": False, "url": "", "registro": ""})
    return xs


def detalle(cliente, tipo):
    from . import boveda
    for gr in catalogo(cliente):
        for x in gr["items"]:
            if x["tipo"] == tipo:
                p = params(cliente["id"], tipo)
                if not p.get("url") and x.get("url", "").startswith("http"):
                    p["url"] = x["url"]
                return dict(x, cat=gr["clave"], cat_titulo=gr["titulo"], cat_desc=gr["descripcion"], params=p,
                            clave_ok=bool(p.get("clave_en_boveda")) and bool(boveda.leer(_ruta_secreto(cliente["id"], tipo))))
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
