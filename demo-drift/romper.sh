#!/usr/bin/env bash
# Simula cambios manuales de personas reales en nodo-so (cada una entra con su propia llave).
set -euo pipefail
NODO="${2:-nodo-so}"
case "${1:-1}" in
  1) ssh -i ~/.ssh/id_jperez -o StrictHostKeyChecking=accept-new jperez@$NODO \
       "sudo sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config"
     echo "jperez habilitó el acceso root por SSH en $NODO" ;;
  2) ssh -i ~/.ssh/id_mgonzalez -o StrictHostKeyChecking=accept-new mgonzalez@$NODO \
       "echo 'soporte ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/soporte-temporal >/dev/null"
     echo "mgonzalez agregó un sudo 'temporal' en $NODO" ;;
  3) ssh -i ~/.ssh/id_jperez -o StrictHostKeyChecking=accept-new jperez@$NODO \
       "sudo sed -i 's/^MaxAuthTries.*/MaxAuthTries 10/; s/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config"
     echo "jperez relajó la política de contraseñas SSH en $NODO" ;;
  *) echo "Uso: romper.sh [1|2|3] [nodo]"; exit 1 ;;
esac
