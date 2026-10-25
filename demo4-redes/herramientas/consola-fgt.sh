#!/bin/bash
# Demo 4 · Abre la CLI del FortiGate del laboratorio (por llave, sin clave). Úsalo desde la VM.
# Activar la licencia de evaluación (una sola vez, con tu cuenta FortiCare; el FortiGate se reinicia):
#   execute vm-license-options account-id <tu-correo-forticare>
#   execute vm-license-options account-password <tu-clave-forticare>
#   execute vm-license          (responde y; reinicia en 3 a 5 minutos)
# Luego, en la VM: bash ~/summit-redhat/demo4-redes/instalar-fgt.sh token
exec ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/demo4/llave-lab admin@172.20.20.12
