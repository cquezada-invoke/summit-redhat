# CA interna (Red Hat Certificate System / Dogtag PKI) del laboratorio

Emula la autoridad certificadora interna de un cliente: una CA raíz «Invoke CA interna» (O=INVOKE) que firma los
certificados de los servidores. Usa la PKI de Red Hat que viene en RHEL 9 (paquetes `idm-pki-*`, el mismo Dogtag PKI en
que se basa Red Hat Certificate System; RHCS como producto aparte requiere su propia suscripción).

- Dónde: la VM del stand (RHEL 9, con repositorios RHUI). Directorio LDAP 389 propio en 1389 y CA en 18080/18443.
- Claves: `~/pki/claves` (0600, no se imprimen). Certificado del administrador: `~/pki/nssdb`.

```bash
bash herramientas/pki/instalar-ca.sh                      # idempotente
bash herramientas/pki/emitir.sh nodo-postgres 192.168.122.13   # emite (key + CSR locales, firma la CA)
bash herramientas/pki/instalar-postgres.sh                # TLS en nodo-postgres con el certificado de la CA interna
```
FaceOps lo ve solo: al verificar los flujos, AAP lee el certificado de cada puerto y el mapa marca 🔒 «CA interna»,
🔓 «autofirmado» o 🔒 «CA pública».
