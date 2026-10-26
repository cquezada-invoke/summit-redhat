#!/bin/bash
# Abre la CLI del firewall VyOS del laboratorio (usuario vyos, por llave). Ejemplos dentro:
#   show firewall ipv4 forward filter     → reglas activas
#   show configuration commands | match firewall
exec ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/demo4/llave-lab vyos@172.20.20.12 "$@"
