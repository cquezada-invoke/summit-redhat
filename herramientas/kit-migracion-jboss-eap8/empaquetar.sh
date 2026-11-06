#!/bin/bash
# Arma kit-migracion-jboss-eap8.zip con la MISMA automatización que usa FaceOps (faceops/aap/migracion-jboss-eap8.yml y sus
# scripts): nunca se edita una copia; el kit se arma desde la fuente.
set -euo pipefail
K=$(cd "$(dirname "$0")" && pwd); A=$K/../../faceops/aap; S=$(mktemp -d); D=$S/kit-migracion-jboss-eap8
mkdir -p $D/files $D/informes
cp $A/migracion-jboss-eap8.yml $D/migrar-jboss-eap8.yml
cp $A/files/jboss_eap8_analizar.py $A/files/jboss_eap8_ajustar.py $D/files/
cp $K/ansible.cfg $K/inventario.ini $K/ejecutar.sh $K/LEEME.md $D/
(cd $S && rm -f "${1:-$K/kit-migracion-jboss-eap8.zip}" && python3 -c "
import os, zipfile, sys
z = zipfile.ZipFile(sys.argv[1], 'w', zipfile.ZIP_DEFLATED)
for r, _, fs in os.walk('kit-migracion-jboss-eap8'):
    for f in fs:
        p = os.path.join(r, f); i = zipfile.ZipInfo.from_file(p, p); i.external_attr = (0o755 if f.endswith('.sh') else 0o644) << 16
        z.writestr(i, open(p, 'rb').read(), zipfile.ZIP_DEFLATED)
z.writestr(zipfile.ZipInfo('kit-migracion-jboss-eap8/informes/'), '')
" "${1:-$K/kit-migracion-jboss-eap8.zip}")
rm -rf $S; echo "${1:-$K/kit-migracion-jboss-eap8.zip}"
