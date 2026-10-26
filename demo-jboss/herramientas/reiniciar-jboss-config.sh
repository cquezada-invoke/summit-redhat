#!/bin/bash
# Deja jboss-config con su configuración base (la del repo summit-redhat) y ambos JBoss iguales a ella.
set -e; source "$(dirname "$0")/comun.sh"
D=$(mktemp -d); trap 'rm -rf $D' EXIT
git clone -q "http://invoke:$GT@127.0.0.1:3000/invoke/jboss-config.git" $D
cp ~/summit-redhat/demo-jboss/jboss-config/comun/jboss.yml $D/comun/jboss.yml
cp ~/summit-redhat/demo-jboss/jboss-config/nodos/*.yml $D/nodos/
cd $D; git add -A
if git -c user.name="Cristian Quezada" -c user.email=cquezada@invoke.cl commit -qm "Reinicio de la demo: configuración base"; then
  git push -q 2>&1 | sed "s/$GT/***/g"; echo "jboss-config vuelve a la base (commit $(git rev-parse --short HEAD)); AAP la aplica por el webhook."
  sleep 10
fi
lanzar "JBoss · Aplicar configuración" '{"modo":"aplicar"}'
estado
