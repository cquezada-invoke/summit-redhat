#!/usr/bin/env python3
"""Análisis de una aplicación JBoss (5, 6 o 7) frente a JBoss EAP 8 (Jakarta EE 10), sin herramientas externas.

Uso: jboss_eap8_analizar.py <archivo.war|ear|jar> [<archivo-ds.xml> ...]
Salida (JSON): aplicación, hallazgos (regla, severidad, esfuerzo, ocurrencias, ejemplo, acción) y los datasources
traducidos a comandos de jboss-cli de EAP 8. Las reglas siguen las de Migration Toolkit for Applications (MTA) para
los objetivos eap8 y jakarta-ee10; los puntos de esfuerzo usan su misma escala (1 trivial … 13 arquitectura).
"""
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

# (código, título, patrón sobre clases y textos, severidad, esfuerzo por aplicación, acción)
REGLAS = [
    ("jakarta-servlet", "API Servlet en javax.* (Jakarta EE 10 usa jakarta.*)", rb"javax/servlet|javax\.servlet", "obligatorio", 3,
     "Transformación automática javax → jakarta (Eclipse Transformer) o recompilar contra Jakarta EE 10"),
    ("jakarta-ejb", "EJB en javax.ejb", rb"javax/ejb|javax\.ejb", "obligatorio", 3, "Transformación javax → jakarta; revisar descriptores ejb-jar.xml"),
    ("jakarta-persistence", "JPA en javax.persistence", rb"javax/persistence|javax\.persistence", "obligatorio", 3,
     "Transformación javax → jakarta; persistence.xml a versión 3.1"),
    ("jakarta-faces", "JSF en javax.faces", rb"javax/faces|javax\.faces", "obligatorio", 5, "Migrar a Jakarta Faces 4; revisar componentes de terceros"),
    ("jakarta-ws", "JAX-RS / JAX-WS en javax.ws / javax.jws", rb"javax/ws/rs|javax/jws|javax\.ws\.rs|javax\.jws", "obligatorio", 3,
     "Transformación javax → jakarta; JAX-WS requiere el subsistema webservices de EAP 8"),
    ("jakarta-otros", "Otras APIs javax.* de Java EE (jms, annotation, inject, validation, transaction, mail)",
     rb"javax/(jms|annotation|inject|validation|transaction|mail)|javax\.(jms|annotation|inject|validation|transaction|mail)", "obligatorio", 2,
     "Transformación javax → jakarta"),
    ("ejb2-entity", "Entity Beans EJB 2.x (CMP/BMP): eliminados en Jakarta EE", rb"<entity>|javax/ejb/EntityBean", "obligatorio", 13,
     "Reescribir como entidades JPA"),
    ("jboss5-descriptor", "Descriptores propietarios de JBoss 5/6 (jboss.xml, jboss-app.xml, jboss-service.xml)",
     rb"jboss\.xml|jboss-app\.xml|jboss-service\.xml|-service\.xml", "obligatorio", 3,
     "Reemplazar por jboss-ejb3.xml / jboss-deployment-structure.xml o configuración del servidor"),
    ("jboss5-classloading", "Aislamiento de clases de JBoss 5 (loader-repository / jboss-classloading.xml)",
     rb"loader-repository|jboss-classloading\.xml", "obligatorio", 2, "Usar jboss-deployment-structure.xml con módulos de EAP 8"),
    ("jndi-legacy", "Nombres JNDI antiguos (java:/ sin espacio global, o java:comp/env sin mapeo)", rb"java:/[A-Za-z]", "opcional", 1,
     "Usar java:jboss/... o java:global/... y declarar los recursos en el descriptor"),
    ("seam2", "JBoss Seam 2 (sin soporte en EAP 8)", rb"org/jboss/seam|org\.jboss\.seam", "obligatorio", 13, "Migrar a CDI / Jakarta Faces"),
    ("richfaces", "RichFaces 3/4 (fin de vida)", rb"org/richfaces|org\.richfaces|org/ajax4jsf", "obligatorio", 8, "Reemplazar por PrimeFaces o componentes estándar"),
    ("hibernate3", "Hibernate 3/4 embebido", rb"org/hibernate/(cfg|classic)|hibernate3", "obligatorio", 5, "Usar Hibernate 6 del servidor (APIs cambiaron)"),
    ("log4j1", "Log4j 1.x embebido (fin de vida, vulnerabilidades)", rb"org/apache/log4j/(Logger|Category)", "obligatorio", 2,
     "Usar el logging del servidor (JBoss Logging) o Log4j 2"),
    ("jboss-api-interna", "APIs internas de JBoss 5 (org.jboss.mx, org.jboss.system, org.jboss.security)", rb"org/jboss/(mx|system|security)/",
     "obligatorio", 5, "Reemplazar por APIs estándar o de Elytron"),
    ("web-xml-viejo", "web.xml de Servlet 2.x", rb"web-app_2_[345]\.xsd|version=\"2\.[345]\"", "opcional", 1, "Actualizar web.xml a Jakarta Servlet 6.0"),
]


def _leer(zf, nombre, limite=4_000_000):
    try:
        with zf.open(nombre) as f:
            return f.read(limite)
    except KeyError:
        return b""


def recorrer(datos, ruta, out):
    """Recorre el archivo (y los WAR/JAR anidados de un EAR) acumulando por regla: ocurrencias y un ejemplo."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(datos))
    except zipfile.BadZipFile:
        return
    for n in zf.namelist():
        if n.endswith("/"):
            continue
        low = n.lower()
        if low.endswith((".war", ".jar", ".rar", ".sar")) and len(out["anidados"]) < 200:
            out["anidados"].append(f"{ruta}!{n}")
            if low.startswith("web-inf/lib/") or low.startswith("lib/"):
                out["librerias"].append(n.rsplit("/", 1)[-1])
            recorrer(_leer(zf, n, 60_000_000), f"{ruta}!{n}", out)
            continue
        if not low.endswith((".class", ".xml", ".jsp", ".jspx", ".xhtml", ".properties", ".tld")):
            continue
        b = _leer(zf, n)
        cuerpo = b + n.encode()
        for cod, _t, pat, *_ in REGLAS:
            if re.search(pat, cuerpo):
                r = out["hits"].setdefault(cod, {"n": 0, "ejemplo": f"{ruta}!{n}"})
                r["n"] += 1
        if low.endswith("web.xml"):
            out["web_xml"] = True
        if low.endswith(".class"):
            out["clases"] += 1


def ds_a_cli(ruta):
    """*-ds.xml de JBoss 5/6 o standalone.xml de EAP 6/7 → comandos de jboss-cli de EAP 8 (nunca la contraseña)."""
    out = []
    try:
        raiz = ET.parse(ruta).getroot()
    except (ET.ParseError, OSError):
        return out
    sin_ns = lambda e: e.tag.split("}")[-1]  # noqa: E731
    for ds in raiz.iter():
        tag = sin_ns(ds)
        if tag in ("local-tx-datasource", "xa-datasource", "no-tx-datasource") and ds.find("jndi-name") is not None:   # JBoss 5/6 (*-ds.xml)
            g = lambda k, ds=ds: (ds.findtext(k) or "").strip()  # noqa: E731
            jndi, usuario = g("jndi-name"), g("user-name")
            pmin, pmax = g("min-pool-size"), g("max-pool-size")
        elif tag in ("datasource", "xa-datasource") and ds.get("jndi-name"):   # EAP 6/7 (standalone.xml): mismo JNDI en EAP 8
            hijo = lambda k, ds=ds: next(((x.text or "").strip() for x in ds.iter() if sin_ns(x) == k), "")  # noqa: E731
            jndi, usuario = ds.get("jndi-name"), hijo("user-name")
            pmin, pmax = hijo("min-pool-size"), hijo("max-pool-size")
            if jndi.endswith("/ExampleDS"):   # el de ejemplo de JBoss: EAP 8 ya trae el suyo
                continue
            g = lambda k, h=hijo: h(k)  # noqa: E731
        else:
            continue
        url = g("connection-url")
        if url.startswith("jdbc:hsqldb:mem:"):   # HSQLDB en memoria (JBoss 5/6): EAP 8 trae H2, que la reemplaza
            url = "jdbc:h2:mem:" + url.split(":", 3)[3] + ";DB_CLOSE_DELAY=-1"
        drv = "postgresql" if "postgresql" in url else ("oracle" if "oracle" in url else ("mysql" if "mysql" in url else ("sqlserver" if "sqlserver" in url else "h2")))
        nombre = re.sub(r"[^A-Za-z0-9]", "", jndi.rsplit("/", 1)[-1]) or "MigradoDS"
        destino = jndi if jndi.startswith(("java:jboss/", "java:/")) and tag == "datasource" else f"java:jboss/datasources/{nombre}"
        out.append({"jndi_jboss5": jndi, "jndi_eap8": destino, "nombre": nombre, "url": url, "driver": drv,
                    "usuario": usuario, "min": pmin or "5", "max": pmax or "20",
                    "cli": f"data-source add --name={nombre} --jndi-name={destino} --driver-name={drv} "
                           f"--connection-url={url} --user-name={usuario} --password=${{env.DS_{nombre.upper()}_CLAVE}} "
                           f"--min-pool-size={pmin or 5} --max-pool-size={pmax or 20} --enabled=true"})
    return out


def main():
    app = sys.argv[1]
    out = {"hits": {}, "anidados": [], "librerias": [], "clases": 0, "web_xml": False}
    with open(app, "rb") as f:
        recorrer(f.read(), app.rsplit("/", 1)[-1], out)
    hallazgos = []
    for cod, titulo, _p, sev, pts, accion in REGLAS:
        h = out["hits"].get(cod)
        if h:
            hallazgos.append({"regla": cod, "titulo": titulo, "severidad": sev, "esfuerzo": pts, "ocurrencias": h["n"], "ejemplo": h["ejemplo"], "accion": accion})
    ds = [x for r in sys.argv[2:] for x in ds_a_cli(r)]
    pts = sum(h["esfuerzo"] for h in hallazgos)
    automatico = {"jakarta-servlet", "jakarta-ejb", "jakarta-persistence", "jakarta-ws", "jakarta-otros", "web-xml-viejo", "jndi-legacy"}
    manual = [h for h in hallazgos if h["regla"] not in automatico and h["severidad"] == "obligatorio"]
    print(json.dumps({
        "aplicacion": app.rsplit("/", 1)[-1], "clases": out["clases"], "librerias": sorted(set(out["librerias"]))[:60],
        "hallazgos": sorted(hallazgos, key=lambda h: (h["severidad"] != "obligatorio", -h["esfuerzo"])),
        "puntos": pts, "migrable_automatico": not manual,
        "complejidad": "baja" if pts <= 8 else ("media" if pts <= 20 else "alta"),
        "datasources": ds,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
