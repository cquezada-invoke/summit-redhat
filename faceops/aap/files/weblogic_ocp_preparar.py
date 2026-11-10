#!/usr/bin/python3
# Migración WebLogic → OpenShift · artefactos sin IA: a partir del análisis arma el contexto de construcción S2I de JBoss EAP 8
# (aplicación con jboss-web.xml traducido desde weblogic.xml, drivers JDBC, script de jboss-cli con datasources y JMS que leen
# URL, usuario y clave de variables de entorno, y el assemble que transforma javax → jakarta) y los manifiestos de OpenShift
# (ImageStream, BuildConfig, ConfigMap, Secret sin datos, Deployment, Service y Route). Ninguna clave pasa por aquí.
# Uso: weblogic_ocp_preparar.py  (JSON por stdin: analisis, app, origen, salida, cache, ns, imagen, recursos, salud)
import hashlib, io, json, os, re, shutil, sys, tarfile, zipfile
from xml.sax.saxutils import escape

ETQ = "app.kubernetes.io"


def jndi_ds(j):
    return "java:jboss/datasources/" + j.split("/")[-1]


def jndi_jms(j):
    return "java:/" + j.lstrip("/")


def jboss_web(w, ds_map, jms_map):
    l = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<!-- Generado por FaceOps desde weblogic.xml -->", "<jboss-web>"]
    if w.get("context_root"):
        l.append(f"  <context-root>{escape(w['context_root'])}</context-root>")
    for r in w.get("recursos", []):
        dest = ds_map.get(r["jndi"]) or jms_map.get(r["jndi"])
        if dest:
            l.append(f"  <resource-ref><res-ref-name>{escape(r['ref'])}</res-ref-name><jndi-name>{escape(dest)}</jndi-name></resource-ref>")
    for r in w.get("recursos_env", []):
        dest = jms_map.get(r["jndi"]) or ds_map.get(r["jndi"])
        if dest:
            l.append(f"  <resource-env-ref><resource-env-ref-name>{escape(r['ref'])}</resource-env-ref-name><jndi-name>{escape(dest)}</jndi-name></resource-env-ref>")
    l.append("</jboss-web>")
    return "\n".join(l) + "\n"


def reescribir(zb, cambios):
    """Copia el archivo (EAR/WAR) agregando o reemplazando entradas; cambios: {ruta: bytes | {ruta_anidada: bytes}}."""
    ent = io.BytesIO()
    zin = zipfile.ZipFile(io.BytesIO(zb))
    with zipfile.ZipFile(ent, "w", zipfile.ZIP_DEFLATED) as zout:
        for i in zin.infolist():
            if i.filename in cambios and isinstance(cambios[i.filename], dict):
                zout.writestr(i, reescribir(zin.read(i.filename), cambios[i.filename]))
            elif i.filename not in cambios:
                zout.writestr(i, zin.read(i.filename))
        for k, v in cambios.items():
            if not isinstance(v, dict):
                zi = zipfile.ZipInfo(k, (2026, 1, 1, 0, 0, 0))   # fecha fija: misma entrada → mismo archivo (huella estable)
                zi.compress_type, zi.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
                zout.writestr(zi, v)
    return ent.getvalue()


def main():
    p = json.load(sys.stdin)
    an, nombre, sal = p["analisis"], p["app"], p["salida"]
    app = next(a for a in an["aplicaciones"] if a["aplicacion"] == nombre)
    dom = an["dominio"]
    k8s = re.sub(r"[^a-z0-9-]", "-", nombre.lower()).strip("-")[:40]
    refs = {r["jndi"] for w in app.get("web", []) for r in w["recursos"] + w["recursos_env"]}
    dss = [d for d in dom["datasources"] if d["driver"] in an["drivers"] and (set(d["jndi"]) & refs or not refs)]
    jms = [dict(q, tipo=t) for m in dom["jms"] for t in ("colas", "topicos", "fabricas") for q in m[t] if q["jndi"] in refs or app["jms"]]
    ds_map = {j: jndi_ds(j) for d in dss for j in d["jndi"]}
    jms_map = {q["jndi"]: jndi_jms(q["jndi"]) for q in jms}
    shutil.rmtree(sal, ignore_errors=True)
    os.makedirs(f"{sal}/ctx/deployments")
    os.makedirs(f"{sal}/ctx/faceops/drivers")
    os.makedirs(f"{sal}/ctx/.s2i/bin")
    # 1. Aplicación: jboss-web.xml traducido en cada módulo web
    zb = open(os.path.join(p["origen"], "apps", nombre, app["archivo"]), "rb").read()
    cambios, generados = {}, []
    for w in app.get("web", []):
        jw = jboss_web(w, ds_map, jms_map).encode()
        if w["modulo"]:
            cambios.setdefault(w["modulo"], {})["WEB-INF/jboss-web.xml"] = jw
        else:
            cambios["WEB-INF/jboss-web.xml"] = jw
        generados.append((w["modulo"] or app["archivo"]) + "/WEB-INF/jboss-web.xml")
    open(f"{sal}/ctx/deployments/{app['archivo']}", "wb").write(reescribir(zb, cambios) if cambios else zb)
    # 2. jboss-cli de construcción (sin secretos: URL, usuario y clave quedan como ${env.*})
    cli = ["embed-server --std-out=discard --server-config=standalone.xml"]
    for tipo in sorted({d["driver"] for d in dss}):
        dr = an["drivers"][tipo]
        jar = os.path.basename(dr["maven"])
        shutil.copy(os.path.join(p["cache"], jar), f"{sal}/ctx/faceops/drivers/{jar}")
        cli += [f"module add --name={dr['modulo']} --resources=/tmp/faceops/drivers/{jar} --dependencies=java.se,jakarta.transaction.api",
                f"/subsystem=datasources/jdbc-driver={tipo}:add(driver-name={tipo},driver-module-name={dr['modulo']},"
                f"driver-class-name={dr['clase']},driver-xa-datasource-class-name={dr['xa']})"]
    env_cm, env_sec = {}, []
    for d in dss:
        e = "WL_DS_" + d["env"]
        cli.append(f"data-source add --name={d['nombre']} --jndi-name={jndi_ds(d['jndi'][0])} --driver-name={d['driver']} "
                   f"--connection-url=${{env.{e}_URL}} --user-name=${{env.{e}_USUARIO}} --password=${{env.{e}_CLAVE}} "
                   f"--min-pool-size={d['min']} --max-pool-size={d['max']} --validate-on-match=true "
                   f"--valid-connection-checker-class-name={an['drivers'][d['driver']]['verificador']}")
        env_cm[f"{e}_URL"], env_cm[f"{e}_USUARIO"] = d["url"], d["usuario"]
        env_sec.append({"datasource": d["nombre"], "variable": f"{e}_CLAVE"})
    for q in jms:
        n = re.sub(r"[^A-Za-z0-9_.-]", "_", q["nombre"])
        if q["tipo"] == "colas":
            cli.append(f"jms-queue add --queue-address={n} --entries=[{jndi_jms(q['jndi'])} java:jboss/exported/{q['jndi'].lstrip('/')}]")
        elif q["tipo"] == "topicos":
            cli.append(f"jms-topic add --topic-address={n} --entries=[{jndi_jms(q['jndi'])}]")
        else:
            cli.append(f"/subsystem=messaging-activemq/server=default/connection-factory={n}:add(connectors=[in-vm],entries=[{jndi_jms(q['jndi'])}])")
    cli.append("stop-embedded-server")
    open(f"{sal}/ctx/faceops/config.cli", "w").write("\n".join(cli) + "\n")
    # 3. assemble: javax → jakarta (si corresponde), S2I de EAP 8 y luego el jboss-cli
    tr = ""
    if app["destino"].get("transformar_javax"):
        dist = os.path.join(p["cache"], "org.eclipse.transformer.cli-0.5.0-distribution.jar")
        zipfile.ZipFile(dist).extractall(f"{sal}/ctx/faceops/transformer")
        cli_jar = next(x for x in os.listdir(f"{sal}/ctx/faceops/transformer") if x.startswith("org.eclipse.transformer.cli-") and "distribution" not in x)
        tr = (f"for f in /tmp/src/deployments/*.ear /tmp/src/deployments/*.war; do\n  [ -f \"$f\" ] || continue\n"
              f"  java -jar /tmp/src/faceops/transformer/{cli_jar} \"$f\" \"$f.jakarta\" -o -q && mv -f \"$f.jakarta\" \"$f\"\n"
              "  echo \"FaceOps: $(basename $f) transformado a Jakarta EE\"\ndone\n")
    asm = ("#!/bin/bash\n# Generado por FaceOps: transforma la aplicación, construye EAP 8 (S2I) y aplica la configuración traducida de WebLogic\nset -e\n"
           + tr + "rm -rf /tmp/faceops && cp -r /tmp/src/faceops /tmp/faceops\n/usr/local/s2i/assemble\n"
           "\"$JBOSS_HOME/bin/jboss-cli.sh\" --file=/tmp/faceops/config.cli\n"
           "rm -rf \"$JBOSS_HOME\"/standalone/{configuration/standalone_xml_history,tmp,data,log} /tmp/faceops\n"
           "chmod -R g+rwX \"$JBOSS_HOME/standalone\" \"$JBOSS_HOME/modules\"\n")
    open(f"{sal}/ctx/.s2i/bin/assemble", "w").write(asm)
    os.chmod(f"{sal}/ctx/.s2i/bin/assemble", 0o755)
    with tarfile.open(f"{sal}/contexto.tar.gz", "w:gz") as t:
        t.add(f"{sal}/ctx", arcname=".")
    hz = hashlib.sha256()   # huella del contexto (contenido, no fechas): si no cambió, no se reconstruye
    for d_, _, fs in sorted(os.walk(f"{sal}/ctx")):
        for f in sorted(fs):
            hz.update(os.path.relpath(os.path.join(d_, f), f"{sal}/ctx").encode())
            hz.update(open(os.path.join(d_, f), "rb").read())
    huella = hz.hexdigest()[:16]
    # 4. Manifiestos
    lab = {f"{ETQ}/name": k8s, f"{ETQ}/part-of": "migracion-weblogic", f"{ETQ}/managed-by": "faceops"}
    ns, rec = p["ns"], p["recursos"]
    props = {x["archivo"]: open(os.path.join(p["origen"], "apps", nombre, x["archivo"]), encoding="utf-8", errors="replace").read()
             for x in app.get("propiedades", [])}
    cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": f"{k8s}-config", "namespace": ns, "labels": lab},
          "data": dict(env_cm, **{f"prop-{k}": v for k, v in props.items()}, **{"config.cli": "\n".join(cli) + "\n"})}
    env = [{"name": k, "valueFrom": {"configMapKeyRef": {"name": f"{k8s}-config", "key": k}}} for k in sorted(env_cm)]
    env += [{"name": s["variable"], "valueFrom": {"secretKeyRef": {"name": f"{k8s}-ds", "key": s["variable"]}}} for s in env_sec]
    jopts = " ".join(app.get("opciones_java", []))
    if jopts:
        env.append({"name": "JAVA_OPTS_APPEND", "value": jopts})
    mounts = [{"name": "propiedades", "mountPath": x["ruta"], "subPath": f"prop-{x['archivo']}", "readOnly": True} for x in app.get("propiedades", [])]
    contexto = (app.get("web") or [{}])[0].get("context_root") or f"/{nombre}"
    salud = p.get("salud") or (contexto.rstrip("/") + "/")
    sonda = {"httpGet": {"path": salud, "port": 8080}, "periodSeconds": 10, "timeoutSeconds": 5}
    dep = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": k8s, "namespace": ns, "labels": lab},
           "spec": {"replicas": 1, "revisionHistoryLimit": 3, "selector": {"matchLabels": {f"{ETQ}/name": k8s}},
                    "strategy": {"type": "Recreate"},
                    "template": {"metadata": {"labels": lab, "annotations": {"faceops/origen": p.get("equipo", ""), "faceops/huella": huella}},
                                 "spec": {"containers": [{"name": k8s, "image": p["imagen"], "env": env,
                                                          "ports": [{"containerPort": 8080, "name": "http"}],
                                                          "resources": {"requests": {"cpu": rec["cpu"], "memory": rec["mem_req"]}, "limits": {"memory": rec["mem_lim"]}},
                                                          "readinessProbe": dict(sonda, initialDelaySeconds=20, failureThreshold=3),
                                                          "livenessProbe": {"tcpSocket": {"port": 8080}, "initialDelaySeconds": 120, "periodSeconds": 20},
                                                          "volumeMounts": mounts}],
                                          "volumes": [{"name": "propiedades", "configMap": {"name": f"{k8s}-config"}}] if mounts else []}}}}
    if not mounts:
        dep["spec"]["template"]["spec"]["containers"][0].pop("volumeMounts")
    capas = app["destino"]["capas"] + (["datasources-web-server"] if dss else [])
    bc = {"apiVersion": "build.openshift.io/v1", "kind": "BuildConfig", "metadata": {"name": k8s, "namespace": ns, "labels": lab, "annotations": {"faceops/huella": huella}},
          "spec": {"source": {"type": "Binary", "binary": {}}, "runPolicy": "Serial", "successfulBuildsHistoryLimit": 2, "failedBuildsHistoryLimit": 2,
                   "strategy": {"type": "Source", "sourceStrategy": {
                       "incremental": True, "from": {"kind": "DockerImage", "name": p["builder"]},
                       "env": [{"name": "GALLEON_PROVISION_FEATURE_PACKS", "value": "org.jboss.eap:wildfly-ee-galleon-pack,org.jboss.eap.cloud:eap-cloud-galleon-pack"},
                               {"name": "GALLEON_PROVISION_LAYERS", "value": ",".join(dict.fromkeys(capas))},
                               {"name": "GALLEON_PROVISION_CHANNELS", "value": p.get("canal") or "org.jboss.eap.channels:eap-8.0"}]}},
                   "output": {"to": {"kind": "ImageStreamTag", "name": f"{k8s}:latest"}},
                   "resources": {"requests": {"cpu": rec["cpu"], "memory": rec["mem_req"]}, "limits": {"memory": rec["build_lim"]}}}}
    man = [{"apiVersion": "image.openshift.io/v1", "kind": "ImageStream", "metadata": {"name": k8s, "namespace": ns, "labels": lab}}, bc, cm, dep,
           {"apiVersion": "v1", "kind": "Service", "metadata": {"name": k8s, "namespace": ns, "labels": lab},
            "spec": {"selector": {f"{ETQ}/name": k8s}, "ports": [{"name": "http", "port": 8080, "targetPort": 8080}]}},
           {"apiVersion": "route.openshift.io/v1", "kind": "Route", "metadata": {"name": k8s, "namespace": ns, "labels": lab},
            "spec": {"to": {"kind": "Service", "name": k8s}, "port": {"targetPort": "http"}, "tls": {"termination": "edge", "insecureEdgeTerminationPolicy": "Redirect"}}}]
    print(json.dumps({"k8s": k8s, "huella": huella, "contexto": f"{sal}/contexto.tar.gz", "manifiestos": man, "secretos": env_sec, "labels": lab, "salud": salud,
                      "context_root": contexto, "capas": capas, "cli": cli, "jboss_web": generados,
                      "datasources": [{"nombre": d["nombre"], "jndi_weblogic": d["jndi"], "jndi_eap": jndi_ds(d["jndi"][0]), "driver": d["driver"]} for d in dss],
                      "jms": [{"nombre": q["nombre"], "jndi_weblogic": q["jndi"], "jndi_eap": jndi_jms(q["jndi"])} for q in jms]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
