#!/bin/bash
# Demo 2/3 · corrige la vulnerabilidad: sube log4j-core a 2.17.1 (versión corregida) y hace commit como jperez.
# El pipeline vuelve a pasar Trivy, publica y despliega. Con "--quitar" elimina la librería en vez de actualizarla.
set -e
C=$(sed -n 's/^gitea_jperez_clave=//p' ~/demo-drift/cicd-secretos.txt)
D=$(mktemp -d); trap 'rm -rf $D' EXIT
git clone -q "http://jperez:$C@localhost:3000/invoke/app-jboss.git" $D && cd $D
grep -q "log4j-core" pom.xml || { echo "app-jboss no usa log4j-core: no hay nada que corregir"; exit 0; }
MODO=${1:-actualizar} python3 - <<'PY'
import os, re
s = open("pom.xml").read()
if os.environ["MODO"] == "--quitar":
    s = re.sub(r"\s*<dependency>\s*<groupId>org\.apache\.logging\.log4j</groupId>\s*<artifactId>log4j-core</artifactId>.*?</dependency>", "", s, flags=re.S)
else:
    s = re.sub(r"(<artifactId>log4j-core</artifactId>\s*<version>)[^<]+", r"\g<1>2.17.1", s)
open("pom.xml", "w").write(s)
PY
MSG="Seguridad: log4j-core a 2.17.1 (corrige CVE-2021-44228)"; [ "${1:-}" = --quitar ] && MSG="Seguridad: quitar log4j-core"
git -c user.name="Juan Pérez" -c user.email=jperez@invoke.cl commit -qam "$MSG"
git push -q 2>&1 | sed "s/$C/***/g"; unset C
echo "Commit $(git rev-parse --short HEAD): $MSG. El pipeline debería pasar y desplegar."
