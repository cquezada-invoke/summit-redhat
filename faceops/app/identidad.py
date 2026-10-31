"""Identidad de cada máquina en FaceOps: un código estable (no depende de lo que tenga instalado), su sistema operativo y una
descripción del propósito. El nombre técnico (hostname, nombre de la VM, inventario de AAP) no se toca: queda como dato
secundario. El código usa el tipo de equipo y su número interno: SRV-0012, RED-0004, STO-0002, CLU-0001, EST-0003."""
from .bd import j

PREFIJO = {"servidor": "SRV", "router": "RED", "switch": "RED", "firewall": "RED", "balanceador": "RED", "almacenamiento": "STO",
           "estación de trabajo": "EST"}


def codigo(e):
    """Identificador que se muestra: el hostname de la instancia; si no tiene, su IP; si tampoco, el código interno."""
    d = e.get("datos")
    d = j(d, {}) if isinstance(d, str) else (d or {})
    h = (d.get("hostname") or "").strip()
    if h and not h.startswith("localhost"):
        return h
    if e.get("ip"):
        return e["ip"]
    return codigo_interno(e)


def codigo_interno(e):
    c = (e.get("categoria") or "servidor").lower()
    pre = "CLU" if c.startswith("clúster") or c in ("kubernetes", "openshift") else PREFIJO.get(c, "EQP")
    return f"{pre}-{int(e.get('id') or 0):04d}"


def descripcion(e):
    """La que escribió el cliente; si no hay, se arma con lo que corre en la máquina (sus plataformas)."""
    if (e.get("descripcion") or "").strip():
        return e["descripcion"].strip()
    sk = e.get("skills")
    sk = j(sk, []) if isinstance(sk, str) else (sk or [])
    nombres = {"postgres": "PostgreSQL", "nginx": "Nginx", "redis": "Redis", "kafka": "Kafka", "jboss": "JBoss EAP", "vault": "Vault",
               "activemq": "ActiveMQ", "tomcat": "Tomcat", "iis": "IIS", "keycloak": "Keycloak", "respaldo-git": None, "node-exporter": None, "redisinsight": None, "node_exporter": None, "monitoreo": None}
    p = [nombres.get(s, s.capitalize()) for s in sk if nombres.get(s, s) is not None]
    return " · ".join(dict.fromkeys(p)) if p else "Sin plataformas declaradas"
