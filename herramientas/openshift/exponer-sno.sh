#!/bin/bash
# Publica la consola del SNO en internet sin tocar la configuración de AAP:
#  · nombres públicos (sslip.io) para consola, OAuth y descargas: ingress.config componentRoutes + certificado Let's Encrypt
#  · enrutador por nombre (SNI) en el 443 público: los nombres del SNO van a 192.168.122.50:443; el resto va a AAP,
#    solo desde las IPs autorizadas (las mismas de la regla aap-https-cristian). El tráfico externo al 443 se redirige
#    al enrutador (firewalld, solo lo que entra por eth0); dentro de la VM AAP sigue en su 443 de siempre.
#  · clave de kubeadmin a la bóveda (faceops/web/ocp-sno) para verla desde FaceOps con auditoría; nunca se imprime.
set -euo pipefail
IP=${IP_PUBLICA:-34.176.42.56}
SUF="sno.$IP.sslip.io"
CONSOLA="console.$SUF"; OAUTH="oauth.$SUF"; DESCARGAS="downloads.$SUF"
D=$HOME/openshift/sno; export KUBECONFIG=$D/instalar/auth/kubeconfig; OC="$D/bin/oc"
P=/var/lib/faceops-publico; R=/var/lib/faceops-sni
paso() { echo "== $*"; }

paso "Certificado Let's Encrypt para $CONSOLA, $OAUTH y $DESCARGAS (validación HTTP en el 80 del proxy público)"
if ! sudo test -s $P/letsencrypt/live/$CONSOLA/fullchain.pem; then
  sudo podman run --rm -v $P/www:/var/www:z -v $P/letsencrypt:/etc/letsencrypt:z docker.io/certbot/certbot:v3.0.1 \
    certonly --webroot -w /var/www -d "$CONSOLA" -d "$OAUTH" -d "$DESCARGAS" --agree-tos --register-unsafely-without-email --non-interactive -q
fi
T=$(mktemp -d); sudo cat $P/letsencrypt/live/$CONSOLA/fullchain.pem > $T/tls.crt; sudo cat $P/letsencrypt/live/$CONSOLA/privkey.pem > $T/tls.key; chmod 600 $T/*
$OC -n openshift-config create secret tls sno-publico-tls --cert=$T/tls.crt --key=$T/tls.key --dry-run=client -o yaml | $OC apply -f - >/dev/null
rm -rf $T

paso "Rutas de consola, OAuth y descargas con los nombres públicos"
$OC patch ingress.config.openshift.io cluster --type=merge -p "{\"spec\":{\"componentRoutes\":[
 {\"name\":\"console\",\"namespace\":\"openshift-console\",\"hostname\":\"$CONSOLA\",\"servingCertKeyPairSecret\":{\"name\":\"sno-publico-tls\"}},
 {\"name\":\"downloads\",\"namespace\":\"openshift-console\",\"hostname\":\"$DESCARGAS\",\"servingCertKeyPairSecret\":{\"name\":\"sno-publico-tls\"}},
 {\"name\":\"oauth-openshift\",\"namespace\":\"openshift-authentication\",\"hostname\":\"$OAUTH\",\"servingCertKeyPairSecret\":{\"name\":\"sno-publico-tls\"}}]}}" >/dev/null

paso "Enrutador por nombre (SNI) en 10443: SNO o AAP (AAP solo desde IPs autorizadas)"
sudo install -d -m 0755 $R
sudo tee $R/nginx.conf >/dev/null <<C
worker_processes 2;
events { worker_connections 2048; }
stream {
  geo \$remote_addr \$aap_permitida {
    default 0;
    127.0.0.0/8 1; 10.0.0.0/8 1; 192.168.0.0/16 1;
    include /etc/nginx/aap-permitidas.conf;
  }
  map \$ssl_preread_server_name \$sitio { ~\.$(echo $SUF | sed 's/\./\\./g')\$ sno; default aap; }
  map "\$sitio:\$aap_permitida" \$destino { "~^sno:" 192.168.122.50:443; "aap:1" 127.0.0.1:443; default 127.0.0.1:9; }
  server { listen 10443; ssl_preread on; proxy_pass \$destino; proxy_connect_timeout 5s; }
}
C
sudo test -f $R/aap-permitidas.conf || sudo tee $R/aap-permitidas.conf >/dev/null <<'L'
181.42.211.130 1; 200.113.135.25 1; 200.113.133.254 1; 186.11.8.148 1; 187.229.33.46 1; 152.230.191.137 1;
187.229.33.101 1; 162.120.186.109 1; 181.42.210.90 1; 45.167.201.213 1; 45.167.201.80 1; 72.14.201.212 1;
38.49.136.202 1; 162.120.185.224 1; 200.68.168.22 1; 200.68.168.149 1;
L
sudo tee /etc/containers/systemd/faceops-sni.container >/dev/null <<U
[Unit]
Description=FaceOps · enrutador SNI del 443 público (SNO y AAP)
After=network-online.target
[Container]
Image=docker.io/library/nginx:1.27-alpine
ContainerName=faceops-sni
Network=host
Volume=$R/nginx.conf:/etc/nginx/nginx.conf:z,ro
Volume=$R/aap-permitidas.conf:/etc/nginx/aap-permitidas.conf:z,ro
[Service]
Restart=always
[Install]
WantedBy=multi-user.target
U
sudo systemctl daemon-reload && sudo systemctl restart faceops-sni && sleep 3 && sudo podman exec faceops-sni nginx -t -q

paso "Lo que entra por eth0 al 443 va al enrutador (permanente)"
regla="-i eth0 -p tcp --dport 443 -j REDIRECT --to-ports 10443"
sudo firewall-cmd -q --direct --query-rule ipv4 nat PREROUTING 0 $regla || sudo firewall-cmd -q --direct --add-rule ipv4 nat PREROUTING 0 $regla
sudo firewall-cmd -q --permanent --direct --query-rule ipv4 nat PREROUTING 0 $regla || sudo firewall-cmd -q --permanent --direct --add-rule ipv4 nat PREROUTING 0 $regla
# Las VMs (el propio SNO valida sus rutas públicas) llegan a la IP pública sin salir a GCP (no hay «hairpin»)
regla="-i virbr0 -d $IP -p tcp --dport 443 -j DNAT --to-destination 192.168.122.1:10443"
sudo firewall-cmd -q --direct --query-rule ipv4 nat PREROUTING 0 $regla || sudo firewall-cmd -q --direct --add-rule ipv4 nat PREROUTING 0 $regla
sudo firewall-cmd -q --permanent --direct --query-rule ipv4 nat PREROUTING 0 $regla || sudo firewall-cmd -q --permanent --direct --add-rule ipv4 nat PREROUTING 0 $regla

paso "Clave de kubeadmin a la bóveda (faceops/web/ocp-sno; no se imprime)"
python3 - "$D/instalar/auth/kubeadmin-password" "$CONSOLA" <<'PY'
import json, os, subprocess, sys, tempfile, urllib.request
clave, consola = open(sys.argv[1]).read().strip(), sys.argv[2]
llave = subprocess.run(["sudo", "podman", "exec", "faceops", "cat", "/var/lib/faceops/llaves/id_demo"], capture_output=True, text=True, check=True).stdout
fd, kf = tempfile.mkstemp(); os.write(fd, llave.encode()); os.close(fd); os.chmod(kf, 0o600)
try:
    out = subprocess.run(["ssh", "-i", kf, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                          "ansible@192.168.122.210", "sudo cat /root/vault-init.json"], capture_output=True, text=True, check=True).stdout
finally:
    os.remove(kf)
tok = json.loads(out)["root_token"]
r = urllib.request.Request("http://192.168.122.210:8200/v1/faceops/data/web/ocp-sno", method="POST",
                           data=json.dumps({"data": {"sitio": "Consola de OpenShift (SNO)", "usuario": "kubeadmin", "clave": clave,
                                                     "url": f"https://{consola}"}}).encode(),
                           headers={"X-Vault-Token": tok, "Content-Type": "application/json"})
urllib.request.urlopen(r, timeout=15).read()
print("   · guardada")
PY
echo "LISTO · consola: https://$CONSOLA"
