#!/bin/bash
# Demo 2/3 · seguridad en el pipeline: agrega a app-jboss una librería con una vulnerabilidad CRÍTICA conocida
# (log4j-core 2.14.1 · Log4Shell · CVE-2021-44228) y hace commit como jperez. Tekton compila, Trivy la detecta
# y el pipeline SE DETIENE antes de publicar y desplegar. Se revierte con quitar-vulnerabilidad.sh.
set -e
C=$(sed -n 's/^gitea_jperez_clave=//p' ~/demo-drift/cicd-secretos.txt)
D=$(mktemp -d); trap 'rm -rf $D' EXIT
git clone -q "http://jperez:$C@localhost:3000/invoke/app-jboss.git" $D && cd $D
grep -q "log4j-core" pom.xml && { echo "app-jboss ya tiene log4j-core: no hay nada que agregar"; exit 0; }
python3 - <<'PY'
s = open("pom.xml").read()
dep = """    <dependency>
      <groupId>org.apache.logging.log4j</groupId>
      <artifactId>log4j-core</artifactId>
      <version>2.14.1</version>
    </dependency>
  </dependencies>"""
open("pom.xml", "w").write(s.replace("  </dependencies>", dep, 1))
PY
git -c user.name="Juan Pérez" -c user.email=jperez@invoke.cl commit -qam "Logs: agregar log4j-core 2.14.1"
git push -q 2>&1 | sed "s/$C/***/g"; unset C
echo "Commit $(git rev-parse --short HEAD): app-jboss ahora usa log4j-core 2.14.1 (Log4Shell)."
echo "Mira el pipeline: http://34.176.42.56:30097  → paso 'vulnerabilidades' (se detiene ahí)."
