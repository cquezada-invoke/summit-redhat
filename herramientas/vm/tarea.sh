cd ~/summit-redhat && git pull -q origin main && cd portal-automatizacion && python3 configurar_aap.py 2>&1 | tail -32
cd ~/summit-redhat/portal-automatizacion && sudo podman build -q -t localhost/portal-automatizacion:latest -f Containerfile . >/dev/null && sudo systemctl restart portal-automatizacion && echo "portal actualizado"
echo "== Pruebas"
cd ~/summit-redhat/portal-automatizacion && timeout 2400 python3 pruebas/probar.py salud optim proyecto amdsalud cert 2>&1 | cut -c1-240
