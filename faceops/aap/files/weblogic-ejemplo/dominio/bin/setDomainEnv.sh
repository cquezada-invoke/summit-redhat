#!/bin/sh
# Dominio WebLogic EMULADO (laboratorio de FaceOps): solo las variables que lee el análisis
DOMAIN_HOME="/u01/oracle/user_projects/domains/ventas_domain"
USER_MEM_ARGS="-Xms512m -Xmx1024m"
JAVA_OPTIONS="${JAVA_OPTIONS} -Dventas.config=/u01/apps/ventas/ventas.properties -Dweblogic.ProductionModeEnabled=true"
export DOMAIN_HOME USER_MEM_ARGS JAVA_OPTIONS
