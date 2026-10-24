#!/bin/bash
# Demo 5 · Autorremediación: prepara nodo-jboss (logs con límite y vigía), abre el puerto del webhook de EDA
# hacia la red de los nodos y configura Gitea, AAP y EDA. Idempotente.
set -e
cd "$(dirname "$0")"
echo "== 1/3 Puerto 5001 (webhook de EDA) desde la red de los nodos"
sudo firewall-cmd -q --permanent --zone=libvirt --add-port=5001/tcp && sudo firewall-cmd -q --reload
echo "== 2/3 nodo-jboss: logs con límite y vigía"
ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook -i nodo-jboss, preparar-nodo.yml \
  -e ansible_host=192.168.122.15 -e ansible_user=ansible -e ansible_ssh_private_key_file="$HOME/.ssh/id_demo"
echo "== 3/3 Gitea, AAP y EDA"
python3 configurar_remediacion.py
echo "LISTO. Prueba: bash $(pwd)/herramientas/romper-jboss.sh"
