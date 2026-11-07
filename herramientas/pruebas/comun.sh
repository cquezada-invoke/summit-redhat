# Funciones para los planes de prueba (se cargan con «source» en el anfitrión).
P=~/summit-redhat/herramientas/pruebas
caso() {  # caso '<json>' → imprime la línea RES y la deja en $RES
  case "$1" in *'"rhsm":true'*) sudo podman cp ~/faceops/rhsm-activacion.json faceops:/tmp/rhsm.json ;; esac
  RES=$(sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 -u - "$1" < $P/lanzar.py 2>/dev/null | grep '^RES ' | tail -1 | cut -c5-)
  [ -z "$RES" ] && RES='{"estado":"sin respuesta del arnés"}'
  case "$1" in *'"rhsm":true'*) sudo podman exec faceops rm -f /tmp/rhsm.json ;; esac
  echo "RES $RES"
}
estado() { echo "$RES" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("estado",""))'; }
ticket() { echo "$RES" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("ticket",""))'; }
_K=$(mktemp); sudo podman exec faceops cat /var/lib/faceops/llaves/id_demo > $_K; chmod 600 $_K
en() {  # en <ip> '<comando>' : verificación en la máquina (solo lectura)
  ssh -i $_K -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR ansible@$1 "$2"
}
CASOS='[]'; TKS='[]'
anotar() {  # anotar C1 "descripción" ok|falla "evidencia"
  CASOS=$(python3 -c 'import json,sys;l=json.loads(sys.argv[1]);l.append({"id":sys.argv[2],"caso":sys.argv[3],"resultado":sys.argv[4],"evidencia":sys.argv[5][:300]});print(json.dumps(l,ensure_ascii=False))' "$CASOS" "$1" "$2" "$3" "${4:-}")
  t=$(ticket); [ -n "$t" ] && TKS=$(python3 -c 'import json,sys;l=json.loads(sys.argv[1]);l.append(sys.argv[2]);print(json.dumps(sorted(set(l))))' "$TKS" "$t")
  echo "CASO $1 $3 · $2"
}
certificar() {  # certificar '<nombre AAP>' '<plan>'
  sudo podman exec -i faceops python3 - "$1" "$2" "$CASOS" "$TKS" < $P/certificar.py 2>/dev/null | grep CERT
}
jq_() {  # jq_ '<expresión python sobre r>' : evalúa sobre el último RES (r = dict)
  echo "$RES" | python3 -c "import json,sys;r=json.load(sys.stdin);s=r.get('stats') or {};print($1)" 2>/dev/null
}
export KUBECONFIG=$HOME/openshift/sno/instalar/auth/kubeconfig; OC=$HOME/openshift/sno/bin/oc
ip_de() { sudo podman exec -i faceops python3 - "$1" 2>/dev/null <<'Q' | tail -1
import sys
from app import create_app
create_app()
from app.bd import q1
e = q1("SELECT * FROM equipos WHERE nombre=?", sys.argv[1]); print((e or {}).get("ip") or "")
Q
}
energia() {  # energia <equipo> encender|apagar
  caso "{\"auto\":\"Infra · Encender o apagar equipo\",\"segmento\":\"\",\"params\":{\"en_equipo\":\"$1\",\"en_accion\":\"$2\"},\"min\":10,\"caso\":\"energía\"}" >/dev/null
  echo "energía $1 $2: $(estado) $(ticket)"
  if [ "$2" = encender ]; then   # espera a que responda por SSH (o RDP/WinRM en Windows), hasta 5 minutos
    local ip; ip=$(ip_de "$1"); for i in $(seq 1 30); do timeout 3 bash -c "echo > /dev/tcp/$ip/22" 2>/dev/null && break; timeout 3 bash -c "echo > /dev/tcp/$ip/5986" 2>/dev/null && break; sleep 10; done; sleep 20
  fi
}
espera_ssh() {  # espera_ssh <equipo>: tras un reinicio, hasta 5 minutos a que vuelva el SSH
  local ip; ip=$(ip_de "$1"); sleep 30; for i in $(seq 1 27); do timeout 3 bash -c "echo > /dev/tcp/$ip/22" 2>/dev/null && break; sleep 10; done; sleep 20
}
aap_() { sudo podman exec -i faceops python3 - "$@" < $P/aap.py 2>/dev/null | grep -E '^(JOBS|APROBADAS)' | tail -1; }
sincronizar() { sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - < $P/sincronizar.py 2>/dev/null | grep SINCRONIZADO; }
