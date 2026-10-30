#!/bin/bash
# CA interna «Invoke CA interna» con la PKI de Red Hat (Dogtag) en esta VM RHEL 9. Idempotente; no imprime claves.
set -euo pipefail
P=~/pki; mkdir -p $P && chmod 700 $P
CL=$P/claves
if [ ! -f $CL ]; then
  umask 077
  { echo "ds=$(openssl rand -hex 16)"; echo "admin=$(openssl rand -hex 16)"; echo "nss=$(openssl rand -hex 16)"; echo "p12=$(openssl rand -hex 16)"; } > $CL
fi
c() { grep "^$1=" $CL | cut -d= -f2; }
H=$(hostname -f)

echo "== Paquetes"
rpm -q 389-ds-base idm-pki-ca idm-pki-tools >/dev/null 2>&1 || sudo dnf -y -q install 389-ds-base idm-pki-ca idm-pki-tools >/dev/null

echo "== Directorio LDAP de la CA (389, instancia pki, puerto 1389)"
if ! sudo dsctl pki status >/dev/null 2>&1; then
  umask 077
  cat > $P/ds.inf <<INF
[general]
full_machine_name = $H
[slapd]
instance_name = pki
port = 1389
secure_port = 1636
root_password = $(c ds)
self_sign_cert = False
[backend-userroot]
sample_entries = no
suffix = dc=pki,dc=invoke
INF
  sudo dscreate from-file $P/ds.inf >/dev/null
  rm -f $P/ds.inf
fi
sudo dsctl pki status | head -1

echo "== CA (pkispawn, instancia pki-invoke, puertos 18080/18443)"
if [ ! -d /var/lib/pki/pki-invoke/ca ]; then
  umask 077
  cat > $P/ca.cfg <<CFG
[DEFAULT]
pki_instance_name = pki-invoke
pki_http_port = 18080
pki_https_port = 18443
pki_server_database_password = $(c nss)
pki_admin_password = $(c admin)
pki_client_pkcs12_password = $(c p12)
pki_ds_url = ldap://localhost:1389
pki_ds_password = $(c ds)
pki_security_domain_name = INVOKE
[Tomcat]
pki_tomcat_server_port = 18005
[CA]
pki_admin_email = cquezada@invoke.cl
pki_admin_name = caadmin
pki_admin_nickname = caadmin
pki_admin_uid = caadmin
pki_ds_base_dn = o=pki-invoke-CA
pki_ds_database = pki-invoke-CA
pki_ca_signing_subject_dn = cn=Invoke CA interna,ou=Seguridad,o=INVOKE
pki_ca_signing_nickname = ca_signing
CFG
  sudo pkispawn -s CA -f $P/ca.cfg -v >/tmp/pkispawn.log 2>&1 || { tail -30 /tmp/pkispawn.log; rm -f $P/ca.cfg; exit 1; }
  rm -f $P/ca.cfg
fi
sudo systemctl is-active pki-tomcatd@pki-invoke

echo "== Cliente del administrador (nssdb en ~/pki/nssdb)"
if [ ! -d $P/nssdb ]; then
  pki -d $P/nssdb -c "$(c nss)" client-init >/dev/null
  sudo cp /root/.dogtag/pki-invoke/ca_admin_cert.p12 $P/ca_admin_cert.p12 && sudo chown "$USER" $P/ca_admin_cert.p12 && chmod 600 $P/ca_admin_cert.p12
  pki -d $P/nssdb -c "$(c nss)" pkcs12-import --pkcs12 $P/ca_admin_cert.p12 --pkcs12-password "$(c p12)" >/dev/null
fi
sudo certutil -L -d /var/lib/pki/pki-invoke/alias -n ca_signing -a > $P/ca.crt
# el cliente confía en la CA interna (sin preguntar al emitir)
certutil -L -d $P/nssdb -n "Invoke CA interna" >/dev/null 2>&1 || \
  certutil -A -d $P/nssdb -n "Invoke CA interna" -t "CT,C,C" -a -i $P/ca.crt -f <(echo "$(c nss)")
openssl x509 -in $P/ca.crt -noout -subject -enddate
echo "LISTO · CA interna en https://$H:18443/ca"
