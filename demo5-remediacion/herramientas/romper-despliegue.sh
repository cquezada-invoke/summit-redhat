#!/bin/bash
# Caso 3 (riesgo alto): queda en producción una versión defectuosa (responde con error).
# Se despliega directo con jboss-cli, sin pasar por el pipeline ni por AAP; la última versión validada sigue guardada.
source "$(dirname "$0")/comun.sh"
JU=$(sed -n 's/^jboss_usuario=//p' ~/demo-drift/cicd-secretos.txt)
JC=$(sed -n 's/^jboss_clave=//p' ~/demo-drift/cicd-secretos.txt)
$SSHJ 'sudo bash -s' <<'R'
set -e
d=$(mktemp -d); mkdir -p $d/WEB-INF
cat > $d/WEB-INF/web.xml <<'X'
<web-app xmlns="https://jakarta.ee/xml/ns/jakartaee" version="6.0">
  <servlet><servlet-name>salud</servlet-name><jsp-file>/salud.jsp</jsp-file></servlet>
  <servlet-mapping><servlet-name>salud</servlet-name><url-pattern>/salud</url-pattern></servlet-mapping>
</web-app>
X
printf '%s' '<% response.setStatus(503); %>error version 9.9.9-defectuosa: no hay conexion con el servicio de pagos' > $d/salud.jsp
printf '%s' '<% response.setStatus(500); %><html><body><h1>Error 500</h1><p>version 9.9.9-defectuosa</p></body></html>' > $d/index.jsp
cd $d && python3 -c "import zipfile,os;z=zipfile.ZipFile('/var/lib/app-jboss/defectuosa.war','w');[z.write(f) for f in ['WEB-INF/web.xml','salud.jsp','index.jsp']]"
rm -rf $d
R
$SSHJ "sudo /opt/rh/eap8/root/usr/share/wildfly/bin/jboss-cli.sh --connect --user='$JU' --password='$JC' --command='deploy /var/lib/app-jboss/defectuosa.war --name=app-jboss.war --force'" >/dev/null
echo "Versión defectuosa 9.9.9 en producción."
sleep 3; estado_app
echo "En unos 30 s el vigía avisa a EDA → workflow 'Remediación · Rollback con aprobación' queda esperando tu aprobación."
echo "Aprobar en AAP: $AAP_URL/execution/workflow-approvals"
