#!/usr/bin/env bash
# Simula personas que hacen cambios manuales en los nodos.
# Uso: simulador.sh minuto [persona ...] | iniciar | detener | estado
set -uo pipefail
PID=~/demo-drift/.simulador.pid
LOG=~/demo-drift/simulador.log
PERSONAS=(jperez mgonzalez alopez rsoto areyes)
NODOS=(nodo-so nodo-nginx nodo-postgres nodo-tomcat nodo-jboss)

cambio(){
  local p="$1" n="$2" e="$3" cmd desc
  case "$e" in
    1) cmd="sudo sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config"; desc="habilitó login root por SSH" ;;
    2) cmd="echo 'soporte ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/soporte-$p >/dev/null"; desc="agregó un sudo 'temporal'" ;;
    3) cmd="sudo sed -i 's/^MaxAuthTries.*/MaxAuthTries 10/' /etc/ssh/sshd_config"; desc="subió los intentos de login a 10" ;;
    4) cmd="sudo sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config"; desc="habilitó login con contraseña" ;;
    5) cmd="echo 'deploy ALL=(ALL) NOPASSWD:/usr/bin/systemctl' | sudo tee /etc/sudoers.d/deploy-app >/dev/null"; desc="dio sudo a la cuenta deploy" ;;
  esac
  ssh -i ~/.ssh/id_$p -o StrictHostKeyChecking=accept-new -o BatchMode=yes "$p@$n" "$cmd" 2>/dev/null \
    && echo "$(date '+%T') $p $desc en $n" >> "$LOG"
}

bucle(){
  while true; do
    cambio "${PERSONAS[RANDOM % ${#PERSONAS[@]}]}" "${NODOS[RANDOM % ${#NODOS[@]}]}" $((RANDOM % 5 + 1))
    sleep $((20 + RANDOM % 26))
  done
}

case "${1:-}" in
  iniciar)
    [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null && { echo "Ya está corriendo"; exit 0; }
    setsid nohup bash "$0" _bucle > /dev/null 2>&1 < /dev/null & echo $! > "$PID"
    echo "Simulador iniciado. Detalle: tail -f $LOG" ;;
  detener)
    [ -f "$PID" ] && pkill -g "$(cat "$PID")" 2>/dev/null; pkill -f "simulador.sh _bucle" 2>/dev/null; rm -f "$PID"; echo "Simulador detenido" ;;
  estado)
    [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null && echo "Corriendo" || echo "Detenido"; tail -5 "$LOG" 2>/dev/null ;;
  minuto)
    shift; [ $# -gt 0 ] && PERSONAS=("$@") || PERSONAS=(areyes)
    FIN=$((SECONDS + 60))
    echo "Simulando 1 minuto con: ${PERSONAS[*]}"
    while [ $SECONDS -lt $FIN ]; do
      cambio "${PERSONAS[RANDOM % ${#PERSONAS[@]}]}" "${NODOS[RANDOM % ${#NODOS[@]}]}" $((RANDOM % 5 + 1)) && tail -1 "$LOG"
      sleep $((10 + RANDOM % 6))
    done
    echo "Fin de la simulación" ;;
  _bucle) bucle ;;
  *) echo "Uso: $0 minuto [persona ...] | iniciar | detener | estado" ;;
esac
