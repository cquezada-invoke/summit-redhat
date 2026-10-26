# JBoss como código · dos nodos y manifiesto por ambiente

- **Dos JBoss EAP 8:** nodo-jboss (.15) y nodo-jboss2 (.18). Nginx reparte `/app-jboss/` entre ambos (cabecera `X-Servidor-JBoss`).
- **jboss-config (Gitea):** `comun/jboss.yml` (JVM, logging, datasource, HTTP, driver JDBC con versión fija) + `nodos/<servidor>.yml`.
  Un commit → EDA (webhook :5006) → "JBoss · Aplicar configuración": servidor por servidor, solo lo que difiere, valida la app.
  Cada 5 minutos la misma plantilla en modo drift corrige los cambios manuales. Claves: Vault, nunca en Git.
- **ambientes (Gitea) · produccion.yml:** versión del `.war` + commit de jboss-config + base del SO de cada servidor.
  Lo actualizan el despliegue del pipeline y cada cambio de configuración.
- **"JBoss · Restablecer a versión":** deja ambos JBoss con la app y la configuración de cualquier commit del manifiesto
  (y deja jboss-config `main` igual, para que el drift no lo revierta).

Comandos (en la VM):
```
bash ~/summit-redhat/demo-jboss/herramientas/cambiar-config-jboss.sh      # commit de ejemplo: log raíz INFO ↔ WARN en ambos
bash ~/summit-redhat/demo-jboss/herramientas/romper-config-jboss.sh       # cambio manual en nodo-jboss2 → AAP lo corrige
bash ~/summit-redhat/demo-jboss/herramientas/restablecer-version.sh       # lista estados; con <commit> restablece
bash ~/summit-redhat/demo-jboss/herramientas/reiniciar-jboss-config.sh    # vuelve a la configuración base
```
Instalación (idempotente): `~/bin/clonar-jboss2.sh` (VM) y `python3 demo-jboss/configurar_jboss_config.py`; luego "JBoss · Preparar servidor" (Nginx).
