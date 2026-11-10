#!/usr/bin/python3
# Migración WebLogic → OpenShift · análisis sin IA: lee el dominio (config.xml, módulos JDBC y JMS, setDomainEnv) y cada
# aplicación (EAR/WAR: weblogic.xml, weblogic-application.xml, weblogic-ejb-jar.xml, APIs weblogic.*, javax, dependencias)
# y decide el destino con reglas fijas. Nunca lee ni devuelve claves.
# Uso: weblogic_analizar.py <carpeta> [apps separadas por coma]  ·  weblogic_analizar.py <carpeta> --despliegues (solo la lista)
import io, json, os, re, sys, zipfile
import xml.etree.ElementTree as ET

DRIVERS = {   # clase del driver → (tipo, módulo EAP, jar en Maven Central, clase XA, verificador de conexión)
    "oracle": ("oracle", "com.oracle.ojdbc", "com/oracle/database/jdbc/ojdbc11/23.5.0.24.07/ojdbc11-23.5.0.24.07.jar",
               "oracle.jdbc.xa.client.OracleXADataSource", "org.jboss.jca.adapters.jdbc.extensions.oracle.OracleValidConnectionChecker", "oracle.jdbc.OracleDriver"),
    "postgresql": ("postgresql", "org.postgresql", "org/postgresql/postgresql/42.7.4/postgresql-42.7.4.jar",
                   "org.postgresql.xa.PGXADataSource", "org.jboss.jca.adapters.jdbc.extensions.postgres.PostgreSQLValidConnectionChecker", "org.postgresql.Driver"),
    "mysql": ("mysql", "com.mysql", "com/mysql/mysql-connector-j/9.1.0/mysql-connector-j-9.1.0.jar",
              "com.mysql.cj.jdbc.MysqlXADataSource", "org.jboss.jca.adapters.jdbc.extensions.mysql.MySQLValidConnectionChecker", "com.mysql.cj.jdbc.Driver"),
    "sqlserver": ("sqlserver", "com.microsoft.sqlserver", "com/microsoft/sqlserver/mssql-jdbc/12.8.1.jre11/mssql-jdbc-12.8.1.jre11.jar",
                  "com.microsoft.sqlserver.jdbc.SQLServerXADataSource", "org.jboss.jca.adapters.jdbc.extensions.mssql.MSSQLValidConnectionChecker",
                  "com.microsoft.sqlserver.jdbc.SQLServerDriver"),
}
JAVAX_EE = ("javax/servlet", "javax/ejb", "javax/jms", "javax/persistence", "javax/ws/rs", "javax/faces", "javax/inject",
            "javax/enterprise", "javax/annotation/security", "javax/validation", "javax/json", "javax/websocket", "javax/mail",
            "javax/batch", "javax/resource", "javax/transaction", "javax/xml/ws", "javax/jws")
APIS_WL = [   # prefijo → (severidad, título, acción)
    ("weblogic/servlet/security", "crítica", "Autenticación propietaria (ServletAuthentication)", "Reemplazar por HttpServletRequest.login() o Jakarta Security"),
    ("weblogic/jndi", "crítica", "Contexto JNDI propietario (WLInitialContextFactory / t3)", "Usar java:comp/env o, para remoto, wildfly-ejb-client / AMQ"),
    ("weblogic/transaction", "crítica", "API de transacciones de WebLogic", "Usar jakarta.transaction (JTA estándar)"),
    ("weblogic/logging", "alta", "Logging de WebLogic", "Reemplazar por java.util.logging o SLF4J"),
    ("weblogic/work", "alta", "Work managers de WebLogic (commonj)", "Usar ManagedExecutorService (Jakarta Concurrency)"),
    ("weblogic/", "crítica", "Otra API propietaria weblogic.*", "Reescribir con la API Jakarta EE equivalente"),
    ("com/bea/", "crítica", "API propietaria com.bea.*", "Reescribir con la API Jakarta EE equivalente"),
    ("oracle/toplink", "alta", "TopLink propietario", "Migrar a JPA (Hibernate)"),
]
WEBXML_SIN_EQUIVALENTE = {   # elementos de weblogic.xml que no se traducen solos
    "virtual-directory-mapping": "Carpeta del servidor publicada en la app: llevarla a un volumen o a la propia app",
    "url-match-map": "Mapeo de URL propietario: usar el estándar de servlet",
    "run-as-role-assignment": "run-as con principal propietario: definirlo en jboss-web.xml a mano",
    "security-role-assignment": "Roles asignados a principales o externos: mapear en Elytron (security-domain)",
    "servlet-descriptor": "Descriptor propietario de servlets: revisar a mano",
    "jsp-descriptor": "Opciones de JSP de WebLogic: revisar (EAP compila las JSP solo)",
    "library-ref": "Biblioteca compartida de WebLogic: empaquetarla en la app o como módulo",
}


def local(t):
    return t.split("}")[-1]


def hijos(e, nombre):
    return [x for x in list(e) if local(x.tag) == nombre]


def texto(e, ruta):
    for p in ruta.split("/"):
        e = next(iter(hijos(e, p)), None) if e is not None else None
    return (e.text or "").strip() if e is not None and e.text else ""


def xml(datos):
    try:
        return ET.fromstring(datos)
    except ET.ParseError:
        return None


def driver_de(clase, url):
    c = (clase or "") + " " + (url or "")
    for k in DRIVERS:
        if k in c.lower() or (k == "sqlserver" and "microsoft" in c.lower()):
            return k
    return "weblogic" if "weblogic.jdbc" in c else ""


def leer_dominio(raiz):
    cfg = os.path.join(raiz, "config", "config.xml")
    d = xml(open(cfg, "rb").read())
    if d is None:
        raise SystemExit(json.dumps({"error": "config.xml ilegible"}))
    dom = {"nombre": texto(d, "name"), "version": texto(d, "domain-version"),
           "servidores": [texto(s, "name") for s in hijos(d, "server")], "clusters": [texto(c, "name") for c in hijos(d, "cluster")],
           "despliegues": [{"nombre": texto(a, "name"), "destino": texto(a, "target"), "tipo": texto(a, "module-type"),
                            "ruta": texto(a, "source-path")} for a in hijos(d, "app-deployment")],
           "bibliotecas": [texto(a, "name") for a in hijos(d, "library")], "datasources": [], "jms": [], "hallazgos": []}
    for r in hijos(d, "jdbc-system-resource"):
        f = os.path.join(raiz, "config", texto(r, "descriptor-file-name"))
        x = xml(open(f, "rb").read()) if os.path.exists(f) else None
        if x is None:
            dom["hallazgos"].append({"severidad": "alta", "titulo": f"Módulo JDBC {texto(r, 'name')} sin descriptor legible", "accion": "Traerlo del dominio"})
            continue
        props = {texto(p, "name"): texto(p, "value") for p in hijos(next(iter(hijos(next(iter(hijos(x, "jdbc-driver-params")), x), "properties")), x), "property")}
        clase, url = texto(x, "jdbc-driver-params/driver-name"), texto(x, "jdbc-driver-params/url")
        tipo = driver_de(clase, url)
        nombre = texto(x, "name") or texto(r, "name")
        dom["datasources"].append({
            "nombre": nombre, "jndi": [j.text.strip() for j in hijos(next(iter(hijos(x, "jdbc-data-source-params")), x), "jndi-name") if j.text],
            "url": url, "clase": clase, "driver": tipo, "usuario": props.get("user", ""), "xa": "xa" in clase.lower(),
            "min": texto(x, "jdbc-connection-pool-params/min-capacity") or "0", "max": texto(x, "jdbc-connection-pool-params/max-capacity") or "15",
            "clave_cifrada": bool(texto(x, "jdbc-driver-params/password-encrypted")),
            "env": re.sub(r"[^A-Z0-9]", "_", nombre.upper())})
        if tipo == "weblogic":
            dom["hallazgos"].append({"severidad": "crítica", "titulo": f"{nombre}: driver DataDirect de WebLogic ({clase})",
                                     "accion": "Cambiar al driver JDBC del fabricante"})
        elif not tipo:
            dom["hallazgos"].append({"severidad": "alta", "titulo": f"{nombre}: driver no reconocido ({clase})", "accion": "Indicar el driver JDBC a mano"})
    for r in hijos(d, "jms-system-resource"):
        f = os.path.join(raiz, "config", texto(r, "descriptor-file-name"))
        x = xml(open(f, "rb").read()) if os.path.exists(f) else None
        if x is None:
            continue
        m = {"modulo": texto(r, "name"), "fabricas": [], "colas": [], "topicos": []}
        for tag, k in (("connection-factory", "fabricas"), ("queue", "colas"), ("uniform-distributed-queue", "colas"), ("topic", "topicos"),
                       ("uniform-distributed-topic", "topicos")):
            for q in hijos(x, tag):
                m[k].append({"nombre": q.get("name"), "jndi": texto(q, "jndi-name")})
        for tag, t in (("foreign-server", "Servidor JMS externo (foreign server)"), ("saf-imported-destinations", "Store-and-forward (SAF)")):
            if hijos(x, tag):
                dom["hallazgos"].append({"severidad": "alta", "titulo": f"{m['modulo']}: {t}", "accion": "Llevarlo a un broker AMQ externo"})
        dom["jms"].append(m)
    env = os.path.join(raiz, "bin", "setDomainEnv.sh")
    s = open(env, encoding="utf-8", errors="replace").read() if os.path.exists(env) else ""
    mem = re.search(r'USER_MEM_ARGS="([^"]*)"', s)
    opts = re.search(r'JAVA_OPTIONS="([^"]*)"', s)
    dom["jvm"] = mem.group(1) if mem else ""
    dom["propiedades_sistema"] = [o for o in (opts.group(1).split() if opts else []) if o.startswith("-D") and not o.startswith("-Dweblogic.")]
    return dom


def archivos(zb, prefijo=""):
    """Todas las entradas, entrando en los módulos anidados (WAR/JAR dentro de EAR, JAR dentro de WAR)."""
    out = {}
    z = zipfile.ZipFile(io.BytesIO(zb))
    for n in z.namelist():
        if n.endswith("/"):
            continue
        b = z.read(n)
        out[prefijo + n] = b
        if n.endswith((".war", ".jar", ".rar")) and prefijo.count("!") < 2:
            try:
                out.update(archivos(b, prefijo + n + "!/"))
            except zipfile.BadZipFile:
                pass
    return out


def analizar_app(nombre, ruta, dom, props_txt):
    ent = archivos(open(ruta, "rb").read())
    h, desc = [], []
    def hallar(sev, tit, acc, n=1):
        for x in h:
            if x["titulo"] == tit:
                x["ocurrencias"] += n
                return
        h.append({"severidad": sev, "titulo": tit, "ocurrencias": n, "accion": acc})
    javax, ejb, jms_uso, wl_api, globales, webs = False, False, False, [], set(), []
    for n, b in ent.items():
        base = n.split("!/")[-1]
        if base.endswith("weblogic.xml"):
            desc.append(n)
            x = xml(b)
            webs.append({"modulo": n.split("!/")[0] if "!/" in n else "", "weblogic": x})
            for el, acc in WEBXML_SIN_EQUIVALENTE.items():
                if x is not None and x.iter() and any(local(e.tag) == el for e in x.iter()):
                    hallar("alta", f"weblogic.xml: {el}", acc)
        elif base.endswith("weblogic-application.xml"):
            desc.append(n)
            x = xml(b)
            if x is not None:
                if hijos(x, "prefer-application-packages"):
                    hallar("media", "weblogic-application.xml: prefer-application-packages", "Equivale a excluir esos paquetes del servidor (jboss-deployment-structure.xml) si el servidor los trae")
                if hijos(x, "work-manager"):
                    hallar("media", "weblogic-application.xml: work-manager", "Límite de hilos: se ajusta en el subsistema de EJB/Undertow o con ManagedExecutorService")
                if hijos(x, "library-ref"):
                    hallar("crítica", "weblogic-application.xml: biblioteca compartida (library-ref)", "Empaquetarla en el EAR (lib/) antes de migrar")
                if hijos(x, "application-param"):
                    hallar("baja", "weblogic-application.xml: application-param", "Parámetros propietarios: se documentan, sin efecto en EAP")
        elif base.endswith(("weblogic-ejb-jar.xml", "weblogic-cmp-rdbms-jar.xml")):
            desc.append(n)
            ejb = True
            hallar("alta", f"{base}: descriptor EJB de WebLogic", "Traducir pools y JNDI a jboss-ejb3.xml" + ("; CMP 2.x exige reescritura a JPA" if "cmp" in base else ""))
        elif base.endswith("ejb-jar.xml"):
            ejb = True
        if base.endswith((".class", ".jsp", ".jspf", ".java", ".tag", ".properties", ".xml")):
            t = b.decode("latin-1")
            if base.endswith((".jsp", ".jspf", ".java", ".tag")):
                t = t.replace(".", "/")
            if not base.endswith(".xml"):
                resto = t.count("weblogic/")
                for pref, sev, tit, acc in APIS_WL:
                    k = t.count(pref) if pref != "weblogic/" else resto
                    if k:
                        hallar(sev, tit, acc, k)
                        wl_api.append(base)
                        resto -= k if pref.startswith("weblogic/") and pref != "weblogic/" else 0
            if any(p in t for p in JAVAX_EE) and not base.endswith(".properties"):
                javax = True
            if "javax/jms" in t or "javax.jms" in t:
                jms_uso = True
            if "javax/ejb" in t:
                ejb = True
            if base.endswith(".class"):
                for g in re.findall(r"(?<![:/])\b((?:jdbc|jms)/[A-Za-z0-9_.-]+)", t):
                    globales.add(g)
    for g in sorted(globales):
        hallar("alta", f"Búsqueda JNDI global «{g}»", "Usar java:comp/env con resource-ref (EAP no publica nombres globales de WebLogic)")
    if javax:
        hallar("info", "Java EE (javax.*) → Jakarta EE 10 (jakarta.*)", "Transformación automática con Eclipse Transformer durante la construcción")
    t3 = re.findall(r"t3s?://[^\s\"']+", props_txt or "")
    if t3 or "WLInitialContextFactory" in (props_txt or ""):
        hallar("crítica", "Propiedades con JNDI remoto de WebLogic (t3 / WLInitialContextFactory)", "Reemplazar por el cliente remoto del destino (wildfly-ejb-client o AMQ)")
    mapeo_web = []
    for w in webs:
        x = w["weblogic"]
        if x is None:
            continue
        mapeo_web.append({"modulo": w["modulo"], "context_root": texto(x, "context-root"),
                          "recursos": [{"ref": texto(r, "res-ref-name"), "jndi": texto(r, "jndi-name")} for r in hijos(x, "resource-description")],
                          "recursos_env": [{"ref": texto(r, "resource-env-ref-name"), "jndi": texto(r, "jndi-name")} for r in hijos(x, "resource-env-description")],
                          "sesion_seg": texto(x, "session-descriptor/timeout-secs")})
    criticos = [x for x in h if x["severidad"] == "crítica"]
    usa_ds = [d for d in dom["datasources"] if d["driver"] in DRIVERS]
    usa_jms = jms_uso or any(w["recursos_env"] for w in mapeo_web) or any("jms/" in r["ref"] for w in mapeo_web for r in w["recursos"])
    perfil_completo = ejb or usa_jms or ruta.endswith(".ear") or any(d["xa"] for d in usa_ds)
    if criticos:
        destino = {"runtime": "eap8", "migrable": False,
                   "motivo": "Usa APIs o servicios propietarios de WebLogic: ningún servidor de destino los tiene; requiere cambios de código antes de migrar"}
    elif perfil_completo:
        destino = {"runtime": "eap8", "migrable": True,
                   "motivo": "Necesita el perfil completo de Jakarta EE (" + ", ".join(x for x, c in (("EAR", ruta.endswith(".ear")), ("EJB", ejb), ("JMS", usa_jms),
                             ("XA", any(d["xa"] for d in usa_ds))) if c) + "): JBoss EAP 8 lo cubre con soporte de Red Hat; Open Liberty también, "
                             "pero sin el soporte ni la suscripción que ya tiene el cliente"}
    else:
        destino = {"runtime": "eap8", "migrable": True,
                   "motivo": "Solo perfil web (servlets/JSP/JAX-RS): JBoss EAP 8 con capas mínimas. Alternativa: Open Liberty (perfil web) si el cliente "
                             "prefiere ese runtime; WildFly solo para ambientes sin soporte"}
    capas = ["cloud-server"] + (["ejb"] if ejb else []) + (["embedded-activemq"] if usa_jms else [])
    destino.update({"capas": capas, "transformar_javax": javax,
                    "alternativas": [{"runtime": "openliberty", "cuando": "Aplicación de perfil web o MicroProfile y el cliente estandariza en Liberty"},
                                     {"runtime": "wildfly", "cuando": "Solo desarrollo o pruebas: mismo runtime que EAP, sin soporte"}]})
    orden = {"crítica": 0, "alta": 1, "media": 2, "baja": 3, "info": 4}
    puntos = sum({"crítica": 13, "alta": 5, "media": 2, "baja": 1, "info": 1}[x["severidad"]] for x in h)
    return {"aplicacion": nombre, "archivo": os.path.basename(ruta), "tipo": "ear" if ruta.endswith(".ear") else "war",
            "descriptores": desc, "hallazgos": sorted(h, key=lambda x: orden[x["severidad"]]), "puntos": puntos,
            "complejidad": "alta" if criticos else ("media" if puntos > 8 else "baja"), "destino": destino, "web": mapeo_web,
            "ejb": ejb, "jms": usa_jms, "javax": javax}


def main():
    raiz = sys.argv[1]
    filtro = [x.strip() for x in (sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "--despliegues" else "").split(",") if x.strip()]
    dom = leer_dominio(os.path.join(raiz, "dominio"))
    if "--despliegues" in sys.argv:
        print(json.dumps(dom["despliegues"], ensure_ascii=False))
        return
    apps = []
    for dp in dom["despliegues"]:
        if filtro and dp["nombre"] not in filtro:
            continue
        local_ = os.path.join(raiz, "apps", dp["nombre"], os.path.basename(dp["ruta"]))
        if not os.path.exists(local_):
            apps.append({"aplicacion": dp["nombre"], "archivo": os.path.basename(dp["ruta"]), "hallazgos": [], "puntos": 0, "complejidad": "—",
                         "destino": {"runtime": "", "migrable": False, "motivo": "No se encontró el archivo desplegado en el origen"}})
            continue
        dir_ = os.path.join(raiz, "apps", dp["nombre"])
        pfs = sorted(p for p in os.listdir(dir_) if p.endswith(".properties"))
        props = "".join(open(os.path.join(dir_, p), encoding="utf-8", errors="replace").read() for p in pfs)
        a = analizar_app(dp["nombre"], local_, dom, props)
        carpeta = os.path.dirname(dp["ruta"])
        a["propiedades"] = [{"archivo": p, "ruta": carpeta + "/" + p} for p in pfs]
        a["opciones_java"] = [o for o in dom["propiedades_sistema"] if carpeta + "/" in o or not o.split("=", 1)[-1].startswith("/")]
        a["destino_original"] = dp["destino"]
        apps.append(a)
    print(json.dumps({"dominio": dom, "aplicaciones": apps,
                      "drivers": {k: {"modulo": v[1], "maven": v[2], "xa": v[3], "verificador": v[4], "clase": v[5]} for k, v in DRIVERS.items()}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
