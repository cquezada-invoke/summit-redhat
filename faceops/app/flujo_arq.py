"""Arquitectura como flujo de interacciones, armada desde el inventario: el usuario de Internet entra por el ISP, pasa el
firewall / WAF, llega a los balanceadores, de ahí a las máquinas de aplicación y a OpenShift, y hacia abajo a los
servicios de apoyo (identidad, mensajería, caché, secretos), los datos y el almacenamiento. Dibuja CADA equipo, con su
estado de energía y un enlace a su detalle; entre capas, un bus con el protocolo típico de esa interacción."""

ANCHO_NODO, ALTO_NODO, SEP_X, SEP_Y, POR_FILA = 184, 66, 18, 16, 7
X0, Y_INET, MARGEN_ETQ = 190, 20, 170

# (clave, título, explicación, protocolo con que se LLEGA a esta capa)
CAPAS = [
    ("isp", "ISP y enlaces", "routers de borde, proveedores de Internet y enlaces entre sitios", "HTTPS 443"),
    ("perimetro", "Firewall · WAF", "filtrado perimetral, reglas de acceso e inspección de tráfico web", "HTTPS 443"),
    ("red", "Red interna", "switches y routers internos", "HTTPS 443"),
    ("balanceo", "Balanceo y publicación", "balanceadores y proxies que reparten la carga entre los servidores", "HTTPS 443"),
    ("aplicacion", "Aplicaciones", "máquinas de aplicación y plataformas de contenedores (OpenShift)", "HTTP 8080 · HTTPS 443"),
    ("servicios", "Servicios de apoyo", "identidad, mensajería, caché y secretos que usan las aplicaciones", "LDAP · AMQP · Redis"),
    ("datos", "Datos", "bases de datos", "JDBC 5432 · SQL"),
    ("storage", "Almacenamiento", "storage y volúmenes", "iSCSI · NFS"),
    ("otros", "Otras máquinas", "equipos sin un rol reconocido en el flujo (operación, pruebas, estaciones)", None),
]
APP = {"jboss", "tomcat", "iis", "httpd", "wildfly", "nodejs", "php", "app-python"}
SERV = {"keycloak", "red-hat-idm", "idm", "active-directory", "389-directory-server", "redis", "valkey", "activemq", "kafka",
        "amq", "vault", "midpoint", "rabbitmq", "redisinsight", "certificados"}
DATOS = {"postgres", "postgresql", "mysql", "mariadb", "mongodb", "oracle", "sqlserver", "datagrid"}
ICONO = {"isp": "router", "perimetro": "firewall", "red": "switch", "balanceo": "balanceador", "aplicacion": "servidor",
         "servicios": "servicio", "datos": "bd", "storage": "storage", "otros": "servidor"}


def capa_de(e, grupo):
    """En qué capa del flujo cae un equipo."""
    gp = grupo(e)
    sk = set(e.get("skills") or [])
    nom = (e.get("nombre") or "").lower()
    if gp == "routers":
        return "isp" if any(x in nom for x in ("isp", "borde", "edge", "wan", "rtr")) else "red"
    if gp == "firewalls":
        return "perimetro"
    if gp == "switches":
        return "red"
    if gp == "balanceadores":
        return "balanceo"
    if gp == "clusters":
        return "aplicacion"
    if gp == "storage" or "storage-emulado" in sk:
        return "storage"
    if gp == "estaciones":
        return "otros"
    if sk & APP:
        return "aplicacion"
    if sk & DATOS:
        return "datos"
    if sk & SERV:
        return "servicios"
    return "otros"


def armar(eqs, grupo):
    por = {}
    for e in eqs:
        if (e.get("categoria") or "").lower().startswith("nodo"):   # nodos de un clúster: los representa el clúster
            continue
        por.setdefault(capa_de(e, grupo), []).append(e)
    filas_max = max([min(len(v), POR_FILA) for v in por.values()] + [4])
    ancho_util = filas_max * ANCHO_NODO + (filas_max - 1) * SEP_X
    ancho = X0 + ancho_util + 40
    y = Y_INET + 70 + 46          # bajo «Usuarios de Internet» queda el primer bus
    bandas = []
    for k, titulo, expl, proto in CAPAS:
        es = sorted(por.get(k, []), key=lambda e: (0 if grupo(e) != "clusters" else 1, e.get("nombre") or ""))
        if not es:
            continue
        nodos = []
        filas = [es[i:i + POR_FILA] for i in range(0, len(es), POR_FILA)]
        y_ini = y
        for f in filas:
            w = len(f) * ANCHO_NODO + (len(f) - 1) * SEP_X
            x = X0 + (ancho_util - w) / 2
            for e in f:
                sk = [s for s in (e.get("skills") or []) if s not in ("kvm",)]
                energia = (e.get("energia") or "").lower()
                nodos.append({"id": e["id"], "nombre": e.get("nombre") or "", "x": x, "y": y,
                              "sub": (e.get("so") or e.get("categoria") or "").strip(),
                              "skills": ", ".join(sk[:3]), "ip": e.get("ip") if (e.get("ip") or "") not in ("", "pendiente") else "",
                              "icono": "openshift" if grupo(e) == "clusters" else ICONO[k],
                              "on": None if not energia else energia.startswith("encend"),
                              "segs": ", ".join((e.get("segmentos") or [])[:2])})
                x += ANCHO_NODO + SEP_X
            y += ALTO_NODO + SEP_Y
        y_fin = y - SEP_Y
        bandas.append({"k": k, "titulo": titulo, "expl": expl, "proto": proto, "y": y_ini - 14, "h": y_fin - y_ini + 28,
                       "nodos": nodos, "n": len(es)})
        y = y_fin + 74            # espacio para el bus hacia la capa siguiente
    # buses: de la capa i a la i+1 (y desde Internet a la primera)
    buses = []
    x_inet = X0 + ancho_util / 2
    previa = {"centros": [x_inet], "y_abajo": Y_INET + 70}
    for b in bandas:
        arriba = [n for n in b["nodos"] if n["y"] == b["nodos"][0]["y"]]
        y_bus = b["y"] - 24
        xs_sup = previa["centros"]
        xs_inf = [n["x"] + ANCHO_NODO / 2 for n in arriba]
        if b["k"] != "otros":
            buses.append({"y": y_bus, "x1": min(xs_sup + xs_inf), "x2": max(xs_sup + xs_inf), "desde": xs_sup, "y_desde": previa["y_abajo"],
                          "hacia": xs_inf, "y_hacia": b["y"] + 14, "proto": b["proto"] or ""})
            ultimos = [n for n in b["nodos"] if n["y"] == b["nodos"][-1]["y"]]
            previa = {"centros": [n["x"] + ANCHO_NODO / 2 for n in ultimos], "y_abajo": b["y"] + b["h"] - 14}
    alto = (bandas[-1]["y"] + bandas[-1]["h"] + 30) if bandas else 260
    return {"ancho": ancho, "alto": alto, "bandas": bandas, "buses": buses, "nodo": {"w": ANCHO_NODO, "h": ALTO_NODO},
            "inet": {"x": x_inet, "y": Y_INET}, "x_etq": 20, "ancho_util": ancho_util, "total": sum(b["n"] for b in bandas)}
