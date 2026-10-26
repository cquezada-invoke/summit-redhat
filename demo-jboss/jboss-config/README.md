# jboss-config · configuración de JBoss EAP como código

Fuente única de verdad de la configuración de **todos** los JBoss del grupo `jboss` (nodo-jboss y nodo-jboss2).

| Archivo | Qué declara |
|---|---|
| `comun/jboss.yml` | Lo común: JVM (`standalone.conf`), logging, ajuste del datasource, conector HTTP, driver JDBC (versión fija + sha256) y nivel de drift |
| `nodos/<servidor>.yml` | Solo lo que difiere en ese servidor (p. ej. `jboss.node.name`) |
| `playbooks/` | Cómo se aplica (AAP) y cómo se restablece una versión |

**Cómo se cambia:** commit en este repo (rama `main`). Gitea avisa a Event-Driven Ansible y AAP ejecuta
"JBoss · Aplicar configuración": servidor por servidor, escribe solo lo que difiere y verifica la app antes de seguir.

**Cambios a mano en el servidor** (consola, `standalone.xml`, `standalone.conf`, driver): "JBoss · Verificar configuración"
los detecta cada 5 minutos y, con `drift.nivel: corregir`, los revierte a lo que dice este repo.

**Claves:** nunca aquí. El usuario de administración y la clave del datasource viven en Vault.

**Versiones completas:** el repo `ambientes` (archivo `produccion.yml`) amarra versión del `.war` + commit de este repo +
base del SO de cada servidor. "JBoss · Restablecer a versión" deja todo como en cualquier commit de ese manifiesto.
