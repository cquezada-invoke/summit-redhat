#!/bin/bash
# Emite un certificado de servidor firmado por la CA interna. Uso: emitir.sh <nombre> [ip]
# La llave y el CSR se generan aquí; la CA firma con el perfil caServerCert. Queda en ~/pki/emitidos/<nombre>/
set -euo pipefail
N=$1; IP=${2:-}; P=~/pki; D=$P/emitidos/$N; mkdir -p $D && chmod 700 $D
c() { grep "^$1=" $P/claves | cut -d= -f2; }
SAN="DNS:$N,DNS:$N.demo.local${IP:+,IP:$IP}"
openssl req -new -newkey rsa:2048 -nodes -keyout $D/servidor.key -out $D/servidor.csr \
  -subj "/CN=$N.demo.local/O=INVOKE" -addext "subjectAltName=$SAN" 2>/dev/null
chmod 600 $D/servidor.key
pki -d $P/nssdb -c "$(c nss)" -U https://localhost:18443 -n caadmin --ignore-cert-status UNTRUSTED_ISSUER \
  ca-cert-issue --profile caServerCert --csr-file $D/servidor.csr --output-file $D/servidor.crt >/dev/null
cp $P/ca.crt $D/ca.crt
openssl x509 -in $D/servidor.crt -noout -subject -issuer -enddate
