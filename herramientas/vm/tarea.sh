# Tarea de prueba del canal: muestra que el runner corre en la VM.
set -e
echo "Host: $(hostname) · Usuario: $(whoami) · $(date)"
systemctl is-active panel-cumplimiento.service || true
