#!/bin/bash
# Maqueta de la demo de migración a JBoss EAP 8 (corre en el anfitrión del laboratorio; idempotente; nunca imprime claves):
#   · orígenes: JBoss AS 5.1 «pedidos» (srv-legado-jboss5-01), JBoss AS 6.1 «clientes» (srv-legado-jboss6-01) y
#     JBoss EAP 7.4 «facturas» (srv-legado-jboss7-01) · destino: JBoss EAP 8 (nodo-jboss)
#   · claves propias de cada consola (no las de fábrica) en la bóveda: faceops/web/<máquina>/jboss
#   · acceso público con certificado Let's Encrypt, solo desde las IPs autorizadas (las mismas de AAP, en el enrutador SNI):
#       https://jboss5|jboss6|jboss7|eap8.<ip>.sslip.io      aplicaciones (y /admin-console en 5 y 6)
#       https://consola-jboss7|consola-eap8.<ip>.sslip.io    consola de administración (HAL) de EAP 7 y EAP 8
#     Bloqueado siempre: jmx-console, web-console, invoker y status de JBoss 5/6 (sin autenticación de fábrica).
set -euo pipefail
IP=${IP_PUBLICA:-34.176.42.56}; P=/var/lib/faceops-publico; R=/var/lib/faceops-sni; AQUI=$(cd "$(dirname "$0")" && pwd)
paso() { echo "== $*"; }
ip_de() { sudo virsh domifaddr "$1" --source lease 2>/dev/null | awk '/ipv4/{split($4,a,"/");print a[1];exit}'; }
declare -A VM=([jboss5]=srv-legado-jboss5-01 [jboss6]=srv-legado-jboss6-01 [jboss7]=srv-legado-jboss7-01 [eap8]=nodo-jboss)
declare -A IPS
for k in "${!VM[@]}"; do IPS[$k]=$(ip_de "${VM[$k]}"); [ -n "${IPS[$k]}" ] || { echo "falta la IP de ${VM[$k]} (¿encendida?)"; exit 1; }; done

T=$(mktemp -d); chmod 700 $T; trap 'rm -rf $T' EXIT
sudo podman exec faceops cat /var/lib/faceops/llaves/id_demo > $T/llave; chmod 600 $T/llave
SSH="ssh -i $T/llave -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
ROOT=$($SSH ansible@192.168.122.210 'sudo cat /root/vault-init.json' | python3 -c 'import json,sys;print(json.load(sys.stdin)["root_token"])')
boveda() {   # boveda <máquina> <url> <usuario> → deja (o reutiliza) la clave en faceops/web/<máquina>/jboss y la escribe en $T/<máquina>
  python3 - "$1" "$2" "$3" "$T/$1" <<'PY'
import json, os, secrets, sys, urllib.request, urllib.error
maq, url, usr, sal = sys.argv[1:5]; tok = os.environ["ROOT"]; v = "http://192.168.122.210:8200/v1/faceops/data/web/" + maq + "/jboss"
try:
    d = json.load(urllib.request.urlopen(urllib.request.Request(v, headers={"X-Vault-Token": tok}), timeout=10))["data"]["data"]
except urllib.error.HTTPError:
    d = {}
if not d.get("clave"):
    d = {"sitio": "JBoss", "usuario": usr, "clave": secrets.token_urlsafe(14).replace("-", "a").replace("_", "b") + "9!", "url": url}
else:
    d.update(url=url, usuario=usr)
urllib.request.urlopen(urllib.request.Request(v, method="POST", data=json.dumps({"data": d}).encode(),
                                              headers={"X-Vault-Token": tok, "Content-Type": "application/json"}), timeout=10).read()
fd = os.open(sal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, json.dumps({"jb_consola_clave": d["clave"]}).encode()); os.close(fd)
PY
}
export ROOT

paso "Claves de las consolas en la bóveda (faceops/web/<máquina>/jboss)"
boveda srv-legado-jboss5-01 "https://jboss5.$IP.sslip.io/admin-console/" admin
boveda srv-legado-jboss6-01 "https://jboss6.$IP.sslip.io/admin-console/" admin
boveda srv-legado-jboss7-01 "https://consola-jboss7.$IP.sslip.io/" admin
boveda nodo-jboss "https://consola-eap8.$IP.sslip.io/" admin

export ANSIBLE_HOST_KEY_CHECKING=False ANSIBLE_SSH_ARGS="-o UserKnownHostsFile=/dev/null -o ControlMaster=auto -o ControlPersist=60s"
AP="ansible-playbook -u ansible --private-key $T/llave"
paso "JBoss 5: consola de administración con clave propia"
$AP -i "${IPS[jboss5]}," -e @$T/srv-legado-jboss5-01 /dev/stdin <<'Y' | grep -E "changed=|failed=|fatal" | tail -2
- hosts: all
  become: true
  gather_facts: false
  tasks:
    - ansible.builtin.copy: {dest: /opt/jboss-5.1.0.GA/server/default/conf/props/jmx-console-users.properties, content: "admin={{ jb_consola_clave }}\n", owner: jboss, mode: "0600"}
      no_log: true
      register: r
    - ansible.builtin.systemd: {name: jboss5, state: restarted}
      when: r.changed
Y
paso "JBoss 6: instalar JBoss AS 6.1 y «clientes»"
$AP -i "${IPS[jboss6]}," -e @$T/srv-legado-jboss6-01 $AQUI/legado-jboss6/instalar-jboss6.yml | grep -E "msg|changed=|fatal" | tail -3
paso "JBoss 7: instalar JBoss EAP 7.4 y «facturas»"
$AP -i "${IPS[jboss7]}," -e @$T/srv-legado-jboss7-01 $AQUI/legado-jboss7/instalar-jboss7.yml | grep -E "msg|changed=|fatal" | tail -3
paso "EAP 8: usuario admin de la consola y consola en todas las interfaces"
$AP -i "${IPS[eap8]}," -e @$T/nodo-jboss /dev/stdin <<'Y' | grep -E "changed=|failed=|fatal" | tail -2
- hosts: all
  become: true
  gather_facts: false
  tasks:
    - ansible.builtin.command: /opt/rh/eap8/root/usr/share/wildfly/bin/add-user.sh -u admin -p {{ jb_consola_clave | quote }} -g admin -s -e
      no_log: true
Y

paso "Certificado Let's Encrypt para los nombres públicos"
NOMBRES="jboss5 jboss6 jboss7 eap8 consola-jboss7 consola-eap8"; D1="jboss5.$IP.sslip.io"
ARGS=""; for n in $NOMBRES; do ARGS="$ARGS -d $n.$IP.sslip.io"; done
sudo test -s $P/letsencrypt/live/$D1/fullchain.pem || sudo podman run --rm -v $P/www:/var/www:z -v $P/letsencrypt:/etc/letsencrypt:z \
  docker.io/certbot/certbot:v3.0.1 certonly --webroot -w /var/www $ARGS --cert-name $D1 --agree-tos --register-unsafely-without-email --non-interactive -q

paso "Proxy de la demo (127.0.0.1:10446; la restricción por IP la hace el enrutador SNI, que ve la IP real)"
sitio() {   # sitio <nombre> <destino> <bloquear consolas de JBoss 5/6: si|no>
  cat <<C
server {
    listen 127.0.0.1:10446 ssl;
    http2 on;
    server_name $1.$IP.sslip.io;
    ssl_certificate /etc/letsencrypt/live/$D1/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$D1/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
$( [ "$3" = si ] && echo '    location ~* ^/(jmx-console|web-console|invoker|status|jbossws|jbossmq-httpil) { return 403; }' )
    location / {
        limit_except GET POST { deny all; }
        proxy_pass http://$2;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_redirect http://$2/ /;
        proxy_redirect http://\$host/ https://\$host/;
    }
}
C
}
{ sitio jboss5 "${IPS[jboss5]}:8080" si; sitio jboss6 "${IPS[jboss6]}:8080" si; sitio jboss7 "${IPS[jboss7]}:8080" no; sitio eap8 "${IPS[eap8]}:8080" no
  sitio consola-jboss7 "${IPS[jboss7]}:9990" no; sitio consola-eap8 "${IPS[eap8]}:9990" no; } | sudo tee $P/conf/demo-jboss.conf >/dev/null
sudo podman exec faceops-publico nginx -t -q && sudo podman exec faceops-publico nginx -s reload

paso "Enrutador SNI: nombres de la demo → proxy"
sudo python3 - $R/nginx.conf "$IP" <<'PY'
import re, sys
p, ip = sys.argv[1], sys.argv[2]; s = open(p).read()
if "demojboss" not in s:
    nombres = " ".join(f"{n}.{ip}.sslip.io demojboss;" for n in ("jboss5", "jboss6", "jboss7", "eap8", "consola-jboss7", "consola-eap8"))
    s = s.replace(" default aap; }", f" {nombres} default aap; }}", 1)
    s = re.sub(r'(map "\$sitio:\$aap_permitida" \$destino \{)', r'\1 "demojboss:1" 127.0.0.1:10446;', s, count=1)   # solo IPs autorizadas
    open(p, "w").write(s)
    print("   · agregado")
PY
sudo systemctl restart faceops-sni && sleep 3

paso "Verificación"
for u in "jboss5/pedidos/pedidos?formato=json" "jboss5/admin-console/" "jboss5/jmx-console/" "jboss6/clientes/clientes?formato=json" "jboss6/admin-console/" \
         "jboss7/facturas/api/facturas" "consola-jboss7/" "eap8/" "consola-eap8/"; do
  h=${u%%/*}.$IP.sslip.io; r=/${u#*/}
  curl -s -o /dev/null -w "$h$r → %{http_code}\n" --resolve $h:10443:127.0.0.1 "https://$h:10443$r"
done
echo "LISTO"
