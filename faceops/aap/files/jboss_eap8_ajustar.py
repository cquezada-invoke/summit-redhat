#!/usr/bin/env python3
"""Ajusta los descriptores de una aplicación ya transformada a Jakarta EE 10 para que EAP 8 la acepte.

Uso: jboss_eap8_ajustar.py <app.war|ear> '<{"java:/CatalogoDS": "java:jboss/datasources/CatalogoDS"}>'
Cambios (se informan en la salida, uno por línea):
  · jboss-web.xml: quita <class-loading>/<loader-repository> (JBoss 5) y mapea los JNDI de los datasources
  · web.xml: esquema Jakarta Servlet 6.0
  · descriptores JBoss 5 sin equivalente (jboss.xml, jboss-app.xml, jboss-classloading.xml): se retiran
  · jboss-deployment-structure.xml: se agrega si no existe (aislamiento de clases equivalente)
"""
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

WEB_XML_6 = ('<web-app xmlns="https://jakarta.ee/xml/ns/jakartaee" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
             'xsi:schemaLocation="https://jakarta.ee/xml/ns/jakartaee https://jakarta.ee/xml/ns/jakartaee/web-app_6_0.xsd" version="6.0"')
DS_XML = ('<?xml version="1.0" encoding="UTF-8"?>\n<jboss-deployment-structure xmlns="urn:jboss:deployment-structure:1.3">\n'
          '  <!-- reemplaza el loader-repository de JBoss 5 (aislamiento por aplicación, por defecto en EAP 8) -->\n'
          '  <deployment/>\n</jboss-deployment-structure>\n')
RETIRAR = ("jboss.xml", "jboss-app.xml", "jboss-classloading.xml")


def main():
    app, jndi = sys.argv[1], json.loads(sys.argv[2] if len(sys.argv) > 2 else "{}")
    cambios = []
    tmp = tempfile.mktemp(suffix=os.path.splitext(app)[1])
    with zipfile.ZipFile(app) as zi, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
        nombres = zi.namelist()
        for n in nombres:
            b = zi.read(n)
            base = n.rsplit("/", 1)[-1]
            if base in RETIRAR:
                cambios.append(f"retirado {n} (descriptor de JBoss 5 sin equivalente en EAP 8)")
                continue
            if base == "jboss-web.xml":
                t = b.decode("utf-8", "replace")
                t2 = re.sub(r"<class-loading\b.*?</class-loading>|<class-loading\b[^>]*/>", "", t, flags=re.S)
                if t2 != t:
                    cambios.append(f"{n}: quitado <class-loading> (JBoss 5) → jboss-deployment-structure.xml")
                for viejo, nuevo in jndi.items():
                    if viejo in t2:
                        t2 = t2.replace(f">{viejo}<", f">{nuevo}<")
                        cambios.append(f"{n}: JNDI {viejo} → {nuevo}")
                b = t2.encode()
            elif base == "web.xml":
                t = b.decode("utf-8", "replace")
                t2 = re.sub(r"<web-app\b[^>]*", WEB_XML_6, t, count=1, flags=re.S)
                if t2 != t:
                    cambios.append(f"{n}: esquema Jakarta Servlet 6.0")
                b = t2.encode()
            zo.writestr(n, b)
        if not any(x.endswith("jboss-deployment-structure.xml") for x in nombres) and any(c.find("class-loading") >= 0 for c in cambios):
            zo.writestr("WEB-INF/jboss-deployment-structure.xml" if app.endswith(".war") else "META-INF/jboss-deployment-structure.xml", DS_XML)
            cambios.append("agregado jboss-deployment-structure.xml")
    shutil.move(tmp, app)
    print("\n".join(cambios) or "sin cambios de descriptores")


if __name__ == "__main__":
    main()
