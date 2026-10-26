#!/bin/bash
# Demo · drift: alguien cambia JBoss A MANO en nodo-jboss2 (consola de administración), sin pasar por Git:
# log raíz en DEBUG y pool máximo del datasource en 99. AAP lo detecta y lo deja otra vez igual a jboss-config
# (en la revisión programada cada 5 minutos; aquí se lanza de inmediato para la demo).
set -e; source "$(dirname "$0")/comun.sh"
U=$(sed -n 's/^jboss_usuario=//p' $SEC); C=$(sed -n 's/^jboss_clave=//p' $SEC)
ssh nodo-jboss2 "sudo /opt/rh/eap8/root/usr/share/wildfly/bin/jboss-cli.sh --connect --user=$U --password=$C \
  --commands='/subsystem=logging/root-logger=ROOT:write-attribute(name=level,value=DEBUG),/subsystem=datasources/data-source=app-jboss:write-attribute(name=max-pool-size,value=99)'" >/dev/null
echo "Cambio manual hecho en nodo-jboss2:"; estado
echo "Revisión de drift (lo mismo que corre cada 5 minutos):"
lanzar "JBoss · Aplicar configuración" '{"modo":"drift"}'
echo "Después:"; estado
