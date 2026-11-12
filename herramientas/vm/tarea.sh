cd ~/summit-redhat && git pull -q --ff-only origin main
python3 faceops/aap/configurar.py >/dev/null 2>&1 || echo "configurar con errores"
dif=0; for f in faceops/aap/migrar-so*.yml faceops/aap/migrar-so-plan.py faceops/aap/paquetes-registro.yml faceops/aap/migracion-jboss-eap8.yml faceops/datos/formularios.yml; do
  [ "$(sudo podman exec faceops md5sum /opt/$f 2>/dev/null | cut -c1-32)" = "$(md5sum $f | cut -c1-32)" ] || { dif=1; echo "distinto: $f"; }; done
if [ $dif = 1 ]; then bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 15; echo "desplegado $(git log -1 --format=%h)"; fi
for i in $(seq 1 30); do sudo podman exec faceops true 2>/dev/null && break; sleep 6; done; sleep 10
bash herramientas/pruebas/p-migraciones-so.sh rhel8-rv 2>&1 | grep -E "^(  \(arnés|IP|  C|CASO|CERT|RHEL|FIN)" | cut -c1-2500
