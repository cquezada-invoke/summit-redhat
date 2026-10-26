#!/bin/bash
# Demo 1 · prueba del control de paquetes en nodo-so, como lo haría una persona (usuario operador, con sudo).
#   probar-paquetes.sh instalar   → instala "tree" (no está en la base): el panel lo detecta;
#                                   en "corregir" Ansible lo desinstala, en "solo registrar" queda como commit en Git.
#   probar-paquetes.sh quitar     → desinstala "tree" a mano (para dejar el servidor como estaba en "solo registrar").
#   probar-paquetes.sh estado     → muestra si "tree" está instalado.
# Para volver a un estado conocido: botón "Volver a un commit" del panel o la plantilla AAP "Estado · Volver a un commit".
P=${PAQUETE:-tree}; H=${SERVIDOR:-nodo-so}
case "${1:-instalar}" in
  instalar) ssh op-$H "sudo dnf -y -q install $P" && echo "$P instalado en $H por operador. Mira el panel: https://34.176.42.56:9443" ;;
  quitar)   ssh op-$H "sudo dnf -y -q remove $P" && echo "$P desinstalado en $H" ;;
  estado)   ssh op-$H "rpm -q $P" || true ;;
  *) echo "uso: $0 instalar|quitar|estado" ;;
esac
