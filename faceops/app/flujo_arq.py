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
IDENTIDAD = {"keycloak", "red-hat-idm", "idm", "active-directory", "389-directory-server", "midpoint", "vault"}
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
    if sk & IDENTIDAD:   # IdM o Keycloak traen httpd, pero su rol es de apoyo
        return "servicios"
    if sk & APP:
        return "aplicacion"
    if sk & DATOS:
        return "datos"
    if sk & SERV:
        return "servicios"
    return "otros"


POR_INFRA = 4          # columnas máximas de cada centro de datos / infraestructura
SEP_INFRA = 46


ANCHO_PLEG = 150       # ancho de un centro de datos comprimido


def infra_de(e):
    return (e.get("ubicacion") or "").strip() or "Sin ubicación"


def id_infra(i):
    """Identificador de un centro para el parámetro plegar (centro:<id>): sin comas ni espacios."""
    import re
    return re.sub(r"[^a-z0-9]+", "-", i.lower()).strip("-") or "x"


def _encendido(e):
    return (e.get("energia") or "").lower().startswith("encend")


def armar(eqs, grupo, plegadas=()):
    """plegadas: capas que se muestran comprimidas (una caja con el resumen en vez de cada equipo) y centros comprimidos
    («centro:<id>»: la columna se dibuja angosta, con una sola caja resumen que recibe los buses de cada capa).
    Subdivisión transversal: cada centro de datos o infraestructura es una columna que cruza todas las capas, así se ve en
    qué sitio está cada pieza del flujo."""
    centros_pl = {p[7:] for p in plegadas if p.startswith("centro:")}
    por = {}
    for e in eqs:
        if (e.get("categoria") or "").lower().startswith("nodo"):   # nodos de un clúster: los representa el clúster
            continue
        por.setdefault(capa_de(e, grupo), []).append(e)
    conteo = {}
    for es in por.values():
        for e in es:
            conteo[infra_de(e)] = conteo.get(infra_de(e), 0) + 1
    infras = sorted(conteo, key=lambda i: (i == "Sin ubicación", -conteo[i], i))
    cols_i = {i: max(1, min(POR_INFRA, max([sum(1 for e in es if infra_de(e) == i) for es in por.values()] + [1]))) for i in infras}
    pleg_i = {i for i in infras if id_infra(i) in centros_pl}
    ancho_i = {i: ANCHO_PLEG if i in pleg_i else cols_i[i] * ANCHO_NODO + (cols_i[i] - 1) * SEP_X for i in infras}
    on_i = {i: sum(1 for es in por.values() for e in es if infra_de(e) == i and _encendido(e)) for i in infras}
    x_i, x = {}, X0
    for i in infras:
        x_i[i] = x
        x += ancho_i[i] + SEP_INFRA
    ancho_util = max(x - SEP_INFRA - X0, 4 * ANCHO_NODO + 3 * SEP_X)
    ancho = X0 + ancho_util + 84   # a la derecha, el botón para comprimir o expandir cada capa
    y = Y_INET + 70 + 46 + 64      # bajo «Usuarios de Internet»: los títulos de cada centro de datos y el primer bus
    bandas = []
    for k, titulo, expl, proto in CAPAS:
        es = sorted(por.get(k, []), key=lambda e: (0 if grupo(e) != "clusters" else 1, e.get("nombre") or ""))
        if not es:
            continue
        nodos = []
        plegada = k in plegadas
        y_ini = y
        pleg_aqui = [i for i in infras if i in pleg_i and any(infra_de(e) == i for e in es)]
        if plegada:
            on = sum(1 for e in es if _encendido(e))
            w_r, x_r = 2 * ANCHO_NODO + SEP_X, X0 + (ancho_util - 2 * ANCHO_NODO - SEP_X) / 2
            if pleg_i:   # que no quede bajo la caja de un centro comprimido: el tramo libre más ancho
                libres, a = [], X0
                for i in sorted(pleg_i, key=lambda i: x_i[i]):
                    libres.append((a, x_i[i] - 10))
                    a = x_i[i] + ANCHO_PLEG + 10
                libres.append((a, X0 + ancho_util))
                a, b_ = max(libres, key=lambda t: t[1] - t[0])
                if b_ - a - 16 >= ANCHO_PLEG:
                    w_r = min(w_r, b_ - a - 16)
                    x_r = a + (b_ - a - w_r) / 2
            nodos.append({"resumen": True, "x": x_r, "y": y, "w": w_r, "n": len(es), "on_n": on,
                          "nombres": ", ".join(e.get("nombre") or "" for e in es), "icono": ICONO[k]})
            y += ALTO_NODO + SEP_Y
        else:
            filas = 1
            for i in infras:
                if i in pleg_i:
                    continue
                de_i = [e for e in es if infra_de(e) == i]
                for j, e in enumerate(de_i):
                    sk = [s_ for s_ in (e.get("skills") or []) if s_ not in ("kvm",)]
                    energia = (e.get("energia") or "").lower()
                    nodos.append({"id": e["id"], "nombre": e.get("nombre") or "", "infra": i,
                                  "x": x_i[i] + (j % cols_i[i]) * (ANCHO_NODO + SEP_X), "y": y + (j // cols_i[i]) * (ALTO_NODO + SEP_Y),
                                  "sub": (e.get("so") or e.get("categoria") or "").strip(),
                                  "skills": ", ".join(sk[:3]), "ip": e.get("ip") if (e.get("ip") or "") not in ("", "pendiente") else "",
                                  "icono": "openshift" if grupo(e) == "clusters" else ICONO[k],
                                  "on": None if not energia else energia.startswith("encend"),
                                  "segs": ", ".join((e.get("segmentos") or [])[:2])})
                filas = max(filas, -(-len(de_i) // cols_i[i]) if de_i else 1)
            y += filas * (ALTO_NODO + SEP_Y)
        y_fin = y - SEP_Y
        bandas.append({"k": k, "titulo": titulo, "expl": expl, "proto": proto, "y": y_ini - 14, "h": y_fin - y_ini + 28,
                       "nodos": nodos, "n": len(es), "plegada": plegada, "centros_pl": [] if plegada else pleg_aqui})
        y = y_fin + 74            # espacio para el bus hacia la capa siguiente
    columnas = []
    if bandas:
        y0, y1 = bandas[0]["y"] - 84, bandas[-1]["y"] + bandas[-1]["h"] + 8
        for i in infras:
            w = ancho_i[i]
            col = {"nombre": i, "id": id_infra(i), "x": x_i[i] - 10, "w": w + 20, "y": y0, "h": y1 - y0, "n": conteo[i],
                   "on_n": on_i[i], "plegada": i in pleg_i, "nube": any(w_ in i.lower() for w_ in ("gcp", "aws", "azure", "nube", "cloud"))}
            if i in pleg_i:   # una sola caja, de la primera a la última capa donde el centro tiene equipos
                con = [b for b in bandas if any(infra_de(e) == i for e in por.get(b["k"], []))] or bandas
                col["caja"] = {"x": x_i[i], "w": ANCHO_PLEG, "y": con[0]["y"] + 14, "h": con[-1]["y"] + con[-1]["h"] - 28 - con[0]["y"]}
            columnas.append(col)
    caja_de = {c["nombre"]: c["caja"] for c in columnas if c.get("caja")}
    # buses: de la capa i a la i+1 (y desde Internet a la primera)
    buses = []
    x_inet = X0 + ancho_util / 2
    previa = {"centros": [x_inet], "y_abajo": Y_INET + 70}
    for b in bandas:
        arriba = [n for n in b["nodos"] if n["y"] == b["nodos"][0]["y"]] if b["nodos"] else []
        y_bus = b["y"] - 24
        xs_sup = previa["centros"]
        # un centro comprimido participa con el centro de su caja: la línea que cae bajo la caja queda tapada por ella
        xs_pl = [caja_de[i]["x"] + ANCHO_PLEG / 2 for i in b["centros_pl"]]
        xs_inf = [n["x"] + n.get("w", ANCHO_NODO) / 2 for n in arriba] + xs_pl
        if b["k"] != "otros" and (xs_sup or xs_inf):
            todos = xs_sup + xs_inf
            lados = []   # si el bus llega a la caja por el costado (no por arriba), una punta de flecha en su borde
            for i in b["centros_pl"]:
                cj = caja_de[i]
                if y_bus > cj["y"]:
                    if any(v < cj["x"] for v in todos):
                        lados.append({"x": cj["x"], "d": -14})
                    if any(v > cj["x"] + cj["w"] for v in todos):
                        lados.append({"x": cj["x"] + cj["w"], "d": 14})
            buses.append({"y": y_bus, "x1": min(todos), "x2": max(todos), "desde": xs_sup, "y_desde": previa["y_abajo"],
                          "hacia": xs_inf, "y_hacia": b["y"] + 14, "proto": b["proto"] or "", "lados": lados})
            ultimos = [n for n in b["nodos"] if n["y"] == b["nodos"][-1]["y"]] if b["nodos"] else []
            previa = {"centros": [n["x"] + n.get("w", ANCHO_NODO) / 2 for n in ultimos] + xs_pl, "y_abajo": b["y"] + b["h"] - 14}
    alto = (bandas[-1]["y"] + bandas[-1]["h"] + 30) if bandas else 260
    return {"ancho": ancho, "alto": alto, "bandas": bandas, "buses": buses, "nodo": {"w": ANCHO_NODO, "h": ALTO_NODO}, "columnas": columnas,
            "todo_centros": ["centro:" + c["id"] for c in columnas], "inet": {"x": x_inet, "y": Y_INET}, "x_etq": 20, "ancho_util": ancho_util, "total": sum(b["n"] for b in bandas)}
